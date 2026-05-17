import hashlib
import json
import re
import secrets
import threading

import config
import crisis
import db
import llm_gateway  # Fix 2: ALL LLM calls go through gateway
import notifications
from config import SIDE_EFFECT_HINTS
from drug_resolver import resolve_drug_name as _resolve_drug_name
from pharma_tools import ModelRouter


SEVERITY_RANK = {
    "none": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


class PharmaSafetyEngine:
    """Deterministic rule engine. Zero LLM calls. Self-learning hooks for feedback."""

    def __init__(self):
        self.critical_pairs = self._load_critical_pairs()
        self.router = ModelRouter()

    def _load_critical_pairs(self) -> dict:
        """Load active interaction rules from db.get_all_drug_interactions()."""
        interactions = db.get_all_drug_interactions()
        if not interactions:
            try:
                db.ensure_default_drug_interactions()
                interactions = db.get_all_drug_interactions()
            except Exception:
                interactions = []
        if not interactions:
            interactions = [
                {
                    "drug_a": row[0],
                    "drug_b": row[1],
                    "severity": row[3],
                    "message_template": row[4],
                }
                for row in getattr(db, "DEFAULT_DRUG_INTERACTION_RULES", [])
            ]
        pairs = {}
        for row in interactions:
            try:
                a = str(row["drug_a"]).lower().strip()
                b = str(row["drug_b"]).lower().strip()
                if not a or not b:
                    continue
                rule = {
                    "severity": str(row.get("severity") or "medium").lower(),
                    "message": row.get("message_template"),
                }
                pairs[(a, b)] = rule
                pairs[(b, a)] = rule
            except Exception:
                continue
        return pairs

    def evaluate(self, patient_id: str, new_drug: str, patient_context: dict) -> dict:
        """
        Evaluate safety for new drug against patient's active meds and context.
        Returns { "max_severity": "critical|high|medium|low|none", "interactions": [...], ... }.
        """
        context = patient_context or {}
        canonical_drug, drug_confidence = _resolve_drug_name(new_drug)
        normalized_new = self._normalize_drug(canonical_drug or new_drug)

        active_meds = context.get("active_meds")
        if active_meds is None:
            active_meds = db.get_active_medications_schedule(patient_id)

        conditions = context.get("conditions")
        if conditions is None:
            conditions = db.get_patient_conditions(patient_id)

        renal_markers = context.get("renal_markers")
        if "renal_markers" not in context:
            renal_markers = db.get_patient_latest_renal_markers(patient_id)

        self._feedback_cache = db.get_unprocessed_feedback()
        try:
            medication_context_warnings = self._evaluate_medication_context_risk(
                normalized_new,
                active_meds or [],
                context.get("medication_id"),
            )
            interactions = self._evaluate_interactions(
                normalized_new,
                canonical_drug,
                active_meds or [],
                skip_external=bool(context.get("skip_external")),
            )
            renal_warnings = self._evaluate_renal_risk(normalized_new, renal_markers, conditions or [])
            condition_warnings = self._evaluate_condition_risk(normalized_new, conditions or [])
        finally:
            self._feedback_cache = None

        all_findings = self._sort_findings_by_severity(
            interactions + medication_context_warnings + renal_warnings + condition_warnings
        )
        max_severity = self._max_severity(all_findings)

        decision_key = self._decision_key(patient_id, normalized_new, active_meds or [], renal_markers)
        recent = db.is_pharma_decision_recent(patient_id, normalized_new, decision_key)

        return {
            "patient_id": patient_id,
            "new_drug": canonical_drug or new_drug,
            "normalized_new_drug": normalized_new,
            "drug_resolution_confidence": drug_confidence,
            "max_severity": max_severity,
            "interactions": interactions,
            "medication_context_warnings": medication_context_warnings,
            "renal_warnings": renal_warnings,
            "condition_warnings": condition_warnings,
            "active_meds_checked": [
                med.get("drug_name") for med in active_meds or [] if isinstance(med, dict)
            ],
            "conditions_checked": conditions or [],
            "renal_markers": renal_markers,
            "idempotency_key": decision_key,
            "recent_decision_exists": recent,
            "requires_veto": max_severity in config.PHARMA_CRITICAL_SEVERITIES,
            "engine": "deterministic",
        }

    def _evaluate_interactions(
        self,
        normalized_new: str,
        display_new: str,
        active_meds: list[dict],
        skip_external: bool = False,
    ) -> list[dict]:
        findings = []
        external_checks = 0
        external_tool = None
        new_components = self._drug_components(normalized_new)
        seen_pairs = set()
        for med in active_meds:
            if not isinstance(med, dict):
                continue
            existing = med.get("drug_name")
            normalized_existing = self._normalize_drug(existing)
            if not normalized_existing or normalized_existing == normalized_new:
                continue
            existing_components = self._drug_components(normalized_existing)
            for new_component in new_components:
                for existing_component in existing_components:
                    if not new_component or not existing_component or new_component == existing_component:
                        continue
                    pair_key = tuple(sorted([new_component, existing_component]))
                    if pair_key in seen_pairs:
                        continue
                    seen_pairs.add(pair_key)
                    rule = self.critical_pairs.get((new_component, existing_component))
                    if not rule:
                        continue
                    base_severity = self._normalize_severity(rule.get("severity"))
                    learned_severity = self._apply_self_learning(
                        new_component,
                        existing_component,
                        base_severity,
                    )
                    findings.append(
                        {
                            "type": "drug_interaction",
                            "drug_a": display_new or new_component,
                            "drug_b": existing,
                            "existing_drug": existing,
                            "severity": learned_severity,
                            "message": self._render_message(rule.get("message"), display_new or new_component, existing),
                            "source": "self_learning" if learned_severity != base_severity else "drug_interactions",
                            "matched_components": [new_component, existing_component],
                        }
                    )
            if any(finding.get("existing_drug") == existing for finding in findings):
                continue

            learned_severity = "none"
            for new_component in new_components:
                for existing_component in existing_components:
                    learned_severity = self._apply_self_learning(new_component, existing_component, "none")
                    if learned_severity != "none":
                        break
                if learned_severity != "none":
                    break
            if learned_severity != "none":
                findings.append(
                    {
                        "type": "drug_interaction",
                        "drug_a": display_new or normalized_new,
                        "drug_b": existing,
                        "existing_drug": existing,
                        "severity": learned_severity,
                        "message": (
                            f"Caregiver feedback has repeatedly flagged "
                            f"{display_new or normalized_new} with {existing} for review."
                        ),
                        "source": "self_learning",
                    }
                )
                continue

            if skip_external or not getattr(config, "PHARMA_EXTERNAL_INTERACTION_LOOKUP_ENABLED", True):
                continue
            if external_checks >= int(getattr(config, "PHARMA_EXTERNAL_INTERACTION_MAX_PAIRS", 1)):
                continue

            try:
                if external_tool is None:
                    from pharma_tools import DrugInteractionTool

                    external_tool = DrugInteractionTool()
                external_checks += 1
                external = external_tool.check_interaction(display_new or normalized_new, existing)
                if not external:
                    continue
                base_severity = self._normalize_severity(external.get("severity"))
                learned_severity = self._apply_self_learning(
                    normalized_new,
                    normalized_existing,
                    base_severity,
                )
                findings.append(
                    {
                        "type": "drug_interaction",
                        "drug_a": display_new or normalized_new,
                        "drug_b": existing,
                        "existing_drug": existing,
                        "severity": learned_severity,
                        "message": external.get("description") or external.get("message") or (
                            f"Potential interaction between {display_new or normalized_new} and {existing}."
                        ),
                        "source": "self_learning" if learned_severity != base_severity else (
                            external.get("source") or "external_interaction_lookup"
                        ),
                    }
                )
            except Exception:
                continue
        return self._sort_findings_by_severity(findings)

    def _evaluate_medication_context_risk(
        self,
        normalized_new: str,
        active_meds: list[dict],
        medication_id: str | None = None,
    ) -> list[dict]:
        """
        Detect patient-specific medication-list problems that are not classic
        drug-drug interactions: duplicate therapy, overlapping combo drugs,
        and same medicine coming from different prescribers/departments.
        """
        findings = []
        new_components = set(self._drug_components(normalized_new))
        for med in active_meds or []:
            if not isinstance(med, dict):
                continue
            if medication_id and str(med.get("id") or "") == str(medication_id):
                continue
            existing_name = med.get("drug_name")
            normalized_existing = self._normalize_drug(existing_name)
            if not normalized_existing:
                continue
            existing_components = set(self._drug_components(normalized_existing))
            overlap = sorted(new_components.intersection(existing_components))
            same_drug = normalized_existing == normalized_new
            if not same_drug and not overlap:
                continue

            prescriber = str(med.get("prescribed_by") or "").strip()
            severity = "high" if same_drug and prescriber else "medium"
            message = (
                f"{existing_name} is already active for this patient"
                + (f" from {prescriber}" if prescriber else "")
                + ". Check whether the new prescription is a replacement or duplicate."
            )
            findings.append(
                {
                    "type": "duplicate_therapy_warning" if same_drug else "overlapping_combo_warning",
                    "drug": normalized_new,
                    "existing_drug": existing_name,
                    "severity": severity,
                    "message": message,
                    "source": "active_medication_context",
                    "matched_components": overlap or [normalized_new],
                    "existing_medication_id": med.get("id"),
                    "existing_prescribed_by": prescriber,
                }
            )
        return self._sort_findings_by_severity(findings)

    def _apply_self_learning(self, new_drug: str, existing_drug: str, current_severity: str) -> str:
        """
        Escalate pair severity when enough caregiver feedback repeatedly vetoes
        the same pair. Feedback never downgrades deterministic safety rules.
        """
        try:
            if not config.PHARMA_SELF_LEARNING_ENABLED:
                return self._normalize_severity(current_severity)

            normalized_current = self._normalize_severity(current_severity)
            if normalized_current == "critical":
                return normalized_current

            pair_keys = {
                self._feedback_pair_key(new_drug, existing_drug),
                self._feedback_pair_key(existing_drug, new_drug),
            }
            feedback_rows = getattr(self, "_feedback_cache", None)
            if feedback_rows is None:
                feedback_rows = db.get_unprocessed_feedback()

            relevant = []
            for item in feedback_rows:
                if not isinstance(item, dict):
                    continue
                if self._normalize_feedback_pair(item.get("drug_pair")) in pair_keys:
                    relevant.append(item)

            min_count = int(getattr(config, "PHARMA_MIN_FEEDBACK_COUNT", 5))
            if len(relevant) < min_count:
                return normalized_current

            approved = sum(1 for item in relevant if str(item.get("outcome") or "").lower() == "approved")
            vetoed = sum(1 for item in relevant if str(item.get("outcome") or "").lower() == "vetoed")
            total_decisive = approved + vetoed
            if total_decisive <= 0:
                return normalized_current

            confidence = max(approved, vetoed) / total_decisive
            threshold = float(getattr(config, "PHARMA_FEEDBACK_CONFIDENCE_THRESHOLD", 0.8))
            if vetoed <= approved or confidence < threshold:
                return normalized_current

            for item in relevant:
                feedback_id = item.get("id")
                if feedback_id:
                    db.mark_feedback_processed(str(feedback_id))
            return "critical"
        except Exception:
            return self._normalize_severity(current_severity)

    def _evaluate_renal_risk(
        self,
        normalized_new: str,
        renal_markers: dict | None,
        conditions: list[dict] | None = None,
    ) -> list[dict]:
        rules = db.get_renal_dosing_rules_for_drug(normalized_new)
        fallback_rule = {
            "drug_name": normalized_new,
            "advisory_egfr": 60.0,
            "warning_egfr": 45.0,
            "critical_egfr": config.PHARMA_EGFR_WARNING_THRESHOLD,
            "default_severity": "high",
            "message_template": f"{normalized_new.title()} needs kidney-function review.",
            "source": "config_fallback",
        } if normalized_new in config.PHARMA_RENAL_CLEARED_DRUGS else None
        if fallback_rule and not rules:
            rules = [fallback_rule]

        condition_names = {
            str(item.get("condition_name") or "").lower()
            for item in conditions or []
            if isinstance(item, dict)
        }
        has_renal_condition = any("kidney" in name or "renal" in name or "ckd" in name for name in condition_names)
        egfr = self._marker_value(renal_markers or {}, "egfr")
        renal_relevant = bool(rules or has_renal_condition)

        if egfr is None:
            if not renal_relevant:
                return []
            if not getattr(config, "PHARMA_RENAL_CONTEXT_ALERT_ON_MISSING", True):
                return []
            severity = "high" if rules or has_renal_condition else "medium"
            return [
                {
                    "type": "renal_context_missing",
                    "drug": normalized_new,
                    "severity": severity,
                    "message": (
                        "No recent eGFR is available, so kidney-related medication safety "
                        f"could not be fully verified for {normalized_new.title()}."
                    ),
                    "source": "renal_context_rule",
                    "needs_review": True,
                }
            ]

        findings = []
        for rule in rules:
            critical = float(rule.get("critical_egfr") or config.PHARMA_EGFR_WARNING_THRESHOLD)
            warning = float(rule.get("warning_egfr") or 45.0)
            advisory = float(rule.get("advisory_egfr") or 60.0)
            severity = None
            if egfr < critical:
                severity = "critical"
            elif egfr < warning:
                severity = "high"
            elif egfr < advisory:
                severity = self._normalize_severity(rule.get("default_severity") or "medium")
            if not severity:
                continue
            message = rule.get("message_template") or f"{normalized_new.title()} needs kidney-function review."
            findings.append(
                {
                    "type": "renal_clearance_warning",
                    "drug": normalized_new,
                    "severity": severity,
                    "message": f"{message} Latest eGFR is {egfr}.",
                    "source": rule.get("source") or "renal_dosing_rules",
                    "egfr": egfr,
                    "renal_rule": rule,
                }
            )

        if has_renal_condition and not findings and egfr < 60:
            findings.append(
                {
                    "type": "renal_condition_context",
                    "drug": normalized_new,
                    "severity": "medium",
                    "message": f"Kidney or renal condition is recorded and latest eGFR is {egfr}. Review dosing if symptoms or dose changes occur.",
                    "source": "condition_context_rule",
                    "egfr": egfr,
                }
            )
        return self._sort_findings_by_severity(findings)

    def _evaluate_condition_risk(self, normalized_new: str, conditions: list[dict]) -> list[dict]:
        condition_names = {
            str(item.get("condition_name") or "").lower()
            for item in conditions
            if isinstance(item, dict)
        }
        findings = []

        if normalized_new == "metformin" and any("kidney" in name or "renal" in name for name in condition_names):
            findings.append(
                {
                    "type": "condition_warning",
                    "drug": normalized_new,
                    "severity": "high",
                    "message": "Metformin needs renal safety review in patients with kidney or renal disease.",
                    "source": "condition_rule",
                }
            )

        if any("allerg" in name and normalized_new in name for name in condition_names):
            findings.append(
                {
                    "type": "condition_warning",
                    "drug": normalized_new,
                    "severity": "critical",
                    "message": f"A recorded allergy appears to mention {normalized_new.title()}. Confirm before use.",
                    "source": "allergy_condition_rule",
                }
            )

        bleeding_drugs = {"warfarin", "aspirin", "clopidogrel", "apixaban", "rivaroxaban", "dabigatran", "heparin", "ibuprofen", "diclofenac", "naproxen"}
        if normalized_new in bleeding_drugs and any("bleed" in name or "ulcer" in name for name in condition_names):
            findings.append(
                {
                    "type": "condition_warning",
                    "drug": normalized_new,
                    "severity": "high",
                    "message": f"{normalized_new.title()} may need extra review because bleeding/ulcer history is recorded.",
                    "source": "bleeding_condition_rule",
                }
            )

        bp_drugs = {"amlodipine", "telmisartan", "ramipril", "metoprolol", "furosemide", "ivabradine"}
        if normalized_new in bp_drugs and any("fall" in name or "dizziness" in name or "syncope" in name for name in condition_names):
            findings.append(
                {
                    "type": "condition_warning",
                    "drug": normalized_new,
                    "severity": "medium",
                    "message": f"{normalized_new.title()} can affect blood pressure or pulse; monitor closely because fall/dizziness history is recorded.",
                    "source": "fall_risk_condition_rule",
                }
            )

        liver_sensitive = {"atorvastatin", "rosuvastatin", "simvastatin", "methotrexate", "isoniazid", "valproate"}
        if normalized_new in liver_sensitive and any("liver" in name or "hepatic" in name for name in condition_names):
            findings.append(
                {
                    "type": "condition_warning",
                    "drug": normalized_new,
                    "severity": "high",
                    "message": f"{normalized_new.title()} may need liver-safety review because hepatic/liver history is recorded.",
                    "source": "liver_condition_rule",
                }
            )

        pregnancy_sensitive = {"warfarin", "telmisartan", "ramipril", "isotretinoin", "methotrexate"}
        if normalized_new in pregnancy_sensitive and any("pregnan" in name for name in condition_names):
            findings.append(
                {
                    "type": "condition_warning",
                    "drug": normalized_new,
                    "severity": "critical",
                    "message": f"{normalized_new.title()} requires urgent clinician review because pregnancy context is recorded.",
                    "source": "pregnancy_condition_rule",
                }
            )

        if normalized_new == "furosemide" and any("dehydration" in name for name in condition_names):
            findings.append(
                {
                    "type": "condition_warning",
                    "drug": normalized_new,
                    "severity": "medium",
                    "message": "Furosemide may worsen dehydration risk. Review fluid status before use.",
                    "source": "condition_rule",
                }
            )

        return self._sort_findings_by_severity(findings)

    def _max_severity(self, findings: list[dict]) -> str:
        if not findings:
            return "none"
        return max(
            (self._normalize_severity(item.get("severity")) for item in findings),
            key=lambda severity: SEVERITY_RANK.get(severity, 0),
            default="none",
        )

    def _normalize_drug(self, value: str) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        text = re.sub(r"\b(?:tab|tablet|cap|capsule|inj|syrup|fab|pab)\.?\s+", "", text)
        text = re.sub(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml|iu|units?)\b", "", text)
        text = re.sub(r"[^a-z0-9+ ]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        aliases = {
            "ecosprin": "aspirin",
            "ecosprin av": "aspirin+atorvastatin",
            "aspirin": "aspirin",
            "clopivas": "clopidogrel",
            "clopidogrel": "clopidogrel",
            "telma": "telmisartan",
            "telma am": "telmisartan+amlodipine",
            "telma-am": "telmisartan+amlodipine",
            "telmisartan": "telmisartan",
            "amlodipine": "amlodipine",
            "rosuvastatin": "rosuvastatin",
            "pantoprazode": "pantoprazole",
            "pantoprazole": "pantoprazole",
            "metformin": "metformin",
            "warfarin": "warfarin",
            "furosemide": "furosemide",
            "digoxin": "digoxin",
            "metoprolol": "metoprolol",
            "metoprolol succinate": "metoprolol",
            "metoprodol": "metoprolol",
            "metoprodol succinate": "metoprolol",
            "metolar": "metoprolol",
            "met xl": "metoprolol",
            "ivabradine": "ivabradine",
        }
        if text in aliases:
            return aliases[text]
        for alias, canonical in aliases.items():
            if alias in text:
                return canonical
        return text

    def _drug_components(self, normalized_value: str) -> list[str]:
        parts = [
            self._normalize_drug(part)
            for part in str(normalized_value or "").replace("/", "+").split("+")
            if str(part or "").strip()
        ]
        return parts or [normalized_value]

    def _normalize_feedback_pair(self, value: str) -> str:
        parts = [self._normalize_drug(part) for part in str(value or "").split("+")]
        parts = [part for part in parts if part]
        return "+".join(parts)

    def _feedback_pair_key(self, drug_a: str, drug_b: str) -> str:
        return f"{self._normalize_drug(drug_a)}+{self._normalize_drug(drug_b)}"

    def _normalize_severity(self, value: str) -> str:
        severity = str(value or "medium").strip().lower()
        return severity if severity in SEVERITY_RANK else "medium"

    def _render_message(self, template: str | None, drug_a: str, drug_b: str) -> str:
        if not template:
            return f"Potential interaction between {drug_a} and {drug_b}."
        try:
            return str(template).format(drug_a=drug_a, drug_b=drug_b)
        except Exception:
            return str(template)

    def _marker_value(self, renal_markers: dict, marker: str) -> float | None:
        try:
            item = renal_markers.get(marker)
            if not isinstance(item, dict):
                return None
            return float(item.get("value"))
        except Exception:
            return None

    def _sort_findings_by_severity(self, findings: list[dict]) -> list[dict]:
        return sorted(
            findings or [],
            key=lambda item: SEVERITY_RANK.get(self._normalize_severity(item.get("severity")), 0),
            reverse=True,
        )

    def _decision_key(self, patient_id: str, new_drug: str, active_meds: list[dict], renal_markers: dict | None) -> str:
        med_names = sorted(
            self._normalize_drug(med.get("drug_name"))
            for med in active_meds
            if isinstance(med, dict) and med.get("drug_name")
        )
        source = f"{patient_id}|{new_drug}|{','.join(med_names)}|{renal_markers or {}}"
        return hashlib.sha256(source.encode("utf-8")).hexdigest()


class PharmaExplainer:
    """Structured explanation generator. Uses llm_gateway ONLY. Fix 2 applied."""

    def __init__(self):
        self.schema = {
            "summary": "One clear sentence for caregivers",
            "risk": "What is the risk?",
            "why_it_matters": "Why does this matter?",
            "what_to_do_now": "What should the caregiver do now?",
            "mechanism": "Simple explanation of why this interaction matters",
            "monitoring": ["list", "of", "specific", "things", "to", "watch"],
            "action": "continue|monitor|consult|avoid",
            "confidence": 0.0,
        }

    def build_prompt(self, interaction: dict, patient_context: dict) -> str:
        """Build prompt for llm_gateway. Model-specific suffixes are added by ModelRouter."""
        return f"""
You are CareCircle's medication safety explainer for caregivers.
Explain the medication issue in plain, non-alarming language.
Do not diagnose, prescribe, or change treatment.
If the risk is serious, recommend contacting the doctor.

Patient context:
{json.dumps(patient_context or {}, ensure_ascii=False, default=str)}

Interaction or warning:
{json.dumps(interaction or {}, ensure_ascii=False, default=str)}

Return ONLY valid JSON matching this JSON schema exactly:
{json.dumps(self.schema, ensure_ascii=False)}

Rules:
- summary must be one clear caregiver-facing sentence.
- risk, why_it_matters, and what_to_do_now must form a plain-language 3-part safety summary.
- mechanism must explain why the interaction matters in simple terms.
- monitoring must be a list of concrete symptoms or checks.
- action must be one of: continue, monitor, consult, avoid.
- confidence must be a number between 0 and 1.
- Handle English, Hindi, and Hinglish caregiver wording without changing medical facts.
- Keep the JSON compact.
""".strip()

    def generate(self, interaction: dict, patient_context: dict, use_reasoning: bool = False) -> dict | None:
        """
        Call ModelRouter with structured prompt.
        Return parsed dict or None when confidence is below threshold.
        """
        try:
            prompt = self.build_prompt(interaction, patient_context)
            router = ModelRouter()
            parsed = None
            if use_reasoning:
                raw = router.call_reasoning(prompt, max_context=4000)
                parsed = self._parse_json(raw)
            if parsed is None:
                parsed = router.call_primary(prompt, self.schema)
            if parsed is None and not use_reasoning:
                raw = router.call_reasoning(prompt, max_context=4000)
                parsed = self._parse_json(raw)
            if not self._is_valid_explanation(parsed):
                return None

            confidence = float(parsed.get("confidence") or 0.0)
            if confidence < config.PHARMA_EXPLANATION_CONFIDENCE_THRESHOLD:
                return None

            for key in ("summary", "risk", "why_it_matters", "what_to_do_now", "mechanism"):
                if parsed.get(key):
                    parsed[key] = str(parsed[key])[: config.PHARMA_MAX_EXPLANATION_CHARS]
            return parsed
        except Exception:
            return None

    def _parse_json(self, raw: str | None) -> dict | None:
        try:
            cleaned = re.sub(
                r"^```json\s*|\s*```$",
                "",
                llm_gateway.strip_reasoning_artifacts(raw),
                flags=re.MULTILINE,
            )
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start == -1 or end == -1 or end < start:
                return None
            data = json.loads(cleaned[start : end + 1])
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _is_valid_explanation(self, data: dict | None) -> bool:
        if not isinstance(data, dict):
            return False
        required = {"summary", "risk", "why_it_matters", "what_to_do_now", "mechanism", "monitoring", "action", "confidence"}
        if not required.issubset(data):
            return False
        if data.get("action") not in {"continue", "monitor", "consult", "avoid"}:
            return False
        if not isinstance(data.get("monitoring"), list):
            return False
        try:
            confidence = float(data.get("confidence"))
            return 0.0 <= confidence <= 1.0
        except Exception:
            return False


def process_new_medication(
    patient_id: str,
    new_drug: str,
    dose_amount: float | None,
    prescribed_by: str | None,
    from_phone: str | None,
    trigger: str = "prescription_photo",
    force_recheck: bool = False,
    medication_id: str | None = None,
) -> dict:
    """
    Main entrypoint. Called from ingestion.py and pipeline.py.
    Contract: Input patient_id/new_drug, Output dict with status/severity.
    Self-learning: caregiver feedback can escalate repeated vetoed pairs.
    """
    try:
        if not getattr(config, "PHARMA_AGENT_ENABLED", True):
            return {
                "status": "skipped",
                "reason": "pharma_agent_disabled",
                "max_severity": "none",
                "new_drug": new_drug,
            }

        if not patient_id or not str(new_drug or "").strip():
            return {"status": "error", "max_severity": "none", "error": "patient_id and new_drug are required"}

        engine = PharmaSafetyEngine()
        normalized_new = engine._normalize_drug(new_drug)

        coarse_hash = hashlib.sha256(f"{patient_id}:{normalized_new}".encode()).hexdigest()

        active_meds = db.get_active_medications_schedule(patient_id)
        conditions = db.get_patient_conditions(patient_id)
        renal_markers = db.get_patient_latest_renal_markers(patient_id)
        patient_name = db.get_patient_name(patient_id) or "Patient"
        patient_context = {
            "active_meds": active_meds,
            "conditions": conditions,
            "renal_markers": renal_markers,
            "patient_name": patient_name,
            "dose_amount": dose_amount,
            "prescribed_by": prescribed_by,
            "trigger": trigger,
            "medication_id": medication_id,
            "skip_external": force_recheck or trigger in {"existing_medication_recheck", "document_pipeline", "async_pipeline"},
        }

        evaluation_result = engine.evaluate(patient_id, new_drug, patient_context)
        decision_hash = str(evaluation_result.get("idempotency_key") or "")
        if not force_recheck and decision_hash and db.is_pharma_decision_recent(patient_id, normalized_new, decision_hash):
            return {
                "status": "skipped",
                "reason": "recent_matching_pharma_decision",
                "max_severity": "none",
                "new_drug": new_drug,
                "decision_hash": decision_hash,
            }

        alert_ids = []
        approvals_created = []
        caregiver_notifications = []
        recent_alert_pairs = _recent_interaction_alert_pairs(patient_id)

        for interaction in evaluation_result.get("interactions", []):
            existing_drug = interaction.get("existing_drug") or interaction.get("drug_b") or ""
            this_severity = str(interaction.get("severity") or "medium").lower()
            interaction["severity"] = this_severity
            message = interaction.get("message") or f"Potential interaction between {new_drug} and {existing_drug}."
            three_part = _three_part_summary(new_drug, existing_drug, interaction)
            interaction["plain_language_summary"] = three_part
            alert_id = None

            if this_severity == "critical":
                if _alert_pair_key(normalized_new, existing_drug, "critical") not in recent_alert_pairs:
                    alert_id = db.create_alert(
                        patient_id,
                        "drug_interaction",
                        "critical",
                        message,
                        _alert_payload(new_drug, existing_drug, interaction, dose_amount, prescribed_by, trigger, three_part),
                    )
                    recent_alert_pairs.add(_alert_pair_key(normalized_new, existing_drug, "critical"))
                interaction_hash = _interaction_rule_hash(patient_id, normalized_new, existing_drug)
                approval_created = _insert_agent_approval(
                    interaction_hash,
                    new_drug,
                    existing_drug,
                    "critical",
                    evaluation_result,
                )
                if approval_created:
                    approvals_created.append(interaction_hash)
                approval_code = _ensure_approval_challenge(interaction_hash) if approval_created else None
                if approval_code:
                    three_part["what_to_do_now"] = (
                        f"{three_part.get('what_to_do_now', '').rstrip()} "
                        f"Primary caregiver can reply APPROVE {approval_code} or VETO {approval_code}."
                    )[:380]
                if alert_id:
                    caregiver_notifications.append(
                        _send_primary_caregiver_alert(
                            patient_id,
                            patient_name,
                            three_part,
                            from_phone,
                            severity=this_severity,
                            approval_code=approval_code,
                        )
                    )
                    caregiver_notifications.extend(
                        _queue_caregiver_notification(
                            patient_id,
                            patient_name,
                            force=True,
                            trigger_message=message,
                            triggered_by_phone=from_phone,
                        )
                    )
            elif this_severity == "high":
                if _alert_pair_key(normalized_new, existing_drug, "high") not in recent_alert_pairs:
                    alert_id = db.create_alert(
                        patient_id,
                        "drug_interaction",
                        "high",
                        message,
                        _alert_payload(new_drug, existing_drug, interaction, dose_amount, prescribed_by, trigger, three_part),
                    )
                    recent_alert_pairs.add(_alert_pair_key(normalized_new, existing_drug, "high"))
                if alert_id:
                    caregiver_notifications.append(
                        _send_primary_caregiver_alert(
                            patient_id,
                            patient_name,
                            three_part,
                            from_phone,
                            severity=this_severity,
                        )
                    )
                    caregiver_notifications.extend(
                        _queue_caregiver_notification(
                            patient_id,
                            patient_name,
                            trigger_message=message,
                            triggered_by_phone=from_phone,
                        )
                    )
            elif this_severity == "medium":
                if _alert_pair_key(normalized_new, existing_drug, "medium") not in recent_alert_pairs:
                    alert_id = db.create_alert(
                        patient_id,
                        "drug_interaction",
                        "medium",
                        message,
                        _alert_payload(new_drug, existing_drug, interaction, dose_amount, prescribed_by, trigger, three_part),
                    )
                    recent_alert_pairs.add(_alert_pair_key(normalized_new, existing_drug, "medium"))

            if alert_id:
                alert_ids.append(str(alert_id))

        for warning in (
            evaluation_result.get("medication_context_warnings", [])
            + evaluation_result.get("renal_warnings", [])
            + evaluation_result.get("condition_warnings", [])
        ):
            warning_type = str(warning.get("type") or "pharma_context_warning")
            warning_drug = warning.get("drug") or new_drug
            warning_severity = str(warning.get("severity") or "medium").lower()
            if _recent_context_alert_exists(patient_id, warning_type, warning_drug, warning_severity):
                continue
            warning_summary = _three_part_summary(warning_drug, None, warning)
            warning["plain_language_summary"] = warning_summary
            alert_id = db.create_alert(
                patient_id,
                warning_type,
                warning_severity,
                warning.get("message") or f"{warning_drug} needs medication safety review.",
                {
                    "new_drug": warning_drug,
                    "warning": warning,
                    "dose_amount": dose_amount,
                    "prescribed_by": prescribed_by,
                    "trigger": trigger,
                    "plain_language_summary": warning_summary,
                },
            )
            if alert_id:
                alert_ids.append(str(alert_id))
                if warning_severity in {"high", "critical"}:
                    caregiver_notifications.append(
                        _send_primary_caregiver_alert(
                            patient_id,
                            patient_name,
                            warning_summary,
                            from_phone,
                            severity=warning_severity,
                        )
                    )

        all_findings = (
            evaluation_result.get("interactions", [])
            + evaluation_result.get("medication_context_warnings", [])
            + evaluation_result.get("renal_warnings", [])
            + evaluation_result.get("condition_warnings", [])
        )
        evaluation_result["max_severity"] = engine._max_severity(all_findings)
        evaluation_result["requires_veto"] = evaluation_result["max_severity"] in config.PHARMA_CRITICAL_SEVERITIES

        if getattr(config, "PHARMA_SYNC_EXPLANATION_ENABLED", False) and evaluation_result.get("max_severity") in {"medium", "high", "critical"}:
            explainer = PharmaExplainer()
            explanation_target = _first_explanation_target(evaluation_result)
            interactions = evaluation_result.get("interactions", [])
            use_reasoning = len(interactions) > 2 or any(
                str(item.get("severity") or "").lower() == "critical" for item in interactions
            )
            explanation = explainer.generate(explanation_target, patient_context, use_reasoning=use_reasoning)
            threshold = 0.5 if evaluation_result.get("max_severity") == "critical" else config.PHARMA_EXPLANATION_CONFIDENCE_THRESHOLD
            if explanation and float(explanation.get("confidence") or 0.0) >= threshold:
                for alert_id in alert_ids:
                    _update_alert_with_explanation(alert_id, explanation)
                evaluation_result["explanation"] = explanation
                evaluation_result["explanation_model_route"] = "reasoning" if use_reasoning else "primary"

        _refresh_crisis_cache_async(patient_id, patient_name)
        research_tasks = _enqueue_research_for_unmatched_pairs(
            patient_id=patient_id,
            medication_id=medication_id,
            new_drug=new_drug,
            normalized_new=normalized_new,
            active_meds=active_meds,
            evaluation_result=evaluation_result,
            trigger=trigger,
            from_phone=from_phone,
        )

        envelope = {
            "status": "completed",
            "patient_id": patient_id,
            "patient_name": patient_name,
            "new_drug": new_drug,
            "dose_amount": dose_amount,
            "prescribed_by": prescribed_by,
            "trigger": trigger,
            "max_severity": evaluation_result.get("max_severity", "none"),
            "interactions_count": len(evaluation_result.get("interactions", [])),
            "alerts_created": alert_ids,
            "approvals_created": approvals_created,
            "caregiver_notifications": caregiver_notifications,
            "research_tasks_queued": research_tasks,
            "evaluation": evaluation_result,
        }

        db.write_audit(
            patient_id=patient_id,
            profile_id=None,
            entity_type="pharma_agent",
            entity_id=None,
            action="PHARMA_AGENT_DECISION",
            actor_role="system",
            new_value={
                "new_drug": normalized_new,
                "dose_amount": dose_amount,
                "prescribed_by": prescribed_by,
                "from_phone": from_phone,
                "trigger": trigger,
                "max_severity": envelope["max_severity"],
                "alerts_created": alert_ids,
                "approvals_created": approvals_created,
                "research_tasks_queued": research_tasks,
                "step_1_hash": coarse_hash,
                "decision_hash": decision_hash,
                "active_meds_checked": evaluation_result.get("active_meds_checked", []),
            },
        )
        return envelope
    except Exception as error:
        message = str(error)
        try:
            db.write_audit(
                patient_id=patient_id,
                profile_id=None,
                entity_type="pharma_agent",
                entity_id=None,
                action="PHARMA_AGENT_ERROR",
                actor_role="system",
                new_value={
                    "new_drug": new_drug,
                    "dose_amount": dose_amount,
                    "prescribed_by": prescribed_by,
                    "from_phone": from_phone,
                    "trigger": trigger,
                    "error": message,
                },
            )
        except Exception:
            pass
        return {"status": "error", "max_severity": "none", "error": message}


def recheck_existing_medications(patient_id: str | None = None) -> dict:
    """
    Replay active medication rows through PharmaAgent after rule/config fixes.
    This is safe to run repeatedly: process_new_medication dedupes recent open
    interaction alerts and agent approval hashes are conflict-protected.
    """
    summary = {
        "status": "completed",
        "patients_checked": 0,
        "medications_checked": 0,
        "results": [],
        "errors": [],
    }
    connection = None
    try:
        connection = db._connect()
        with connection.cursor() as cursor:
            if patient_id:
                cursor.execute(
                    """
                    SELECT DISTINCT patient_id::text
                    FROM medications
                    WHERE patient_id = %s
                      AND status = 'active';
                    """,
                    (patient_id,),
                )
            else:
                cursor.execute(
                    """
                    SELECT DISTINCT patient_id::text
                    FROM medications
                    WHERE status = 'active'
                    ORDER BY patient_id::text;
                    """
                )
            patient_ids = [row[0] for row in cursor.fetchall()]
    except Exception as error:
        summary["status"] = "error"
        summary["errors"].append(str(error))
        return summary
    finally:
        if connection is not None:
            connection.close()

    seen_drugs = set()
    for active_patient_id in patient_ids:
        summary["patients_checked"] += 1
        try:
            engine = PharmaSafetyEngine()
            active_meds = db.get_active_medications_schedule(active_patient_id)
            conditions = db.get_patient_conditions(active_patient_id)
            renal_markers = db.get_patient_latest_renal_markers(active_patient_id)
            patient_name = db.get_patient_name(active_patient_id) or "Patient"
            recent_alert_pairs = _recent_interaction_alert_pairs(active_patient_id)
            seen_pairs = set()
            patient_alerts = []
            patient_approvals = []

            shared_context = {
                "active_meds": active_meds,
                "conditions": conditions,
                "renal_markers": renal_markers,
                "patient_name": patient_name,
                "trigger": "existing_medication_recheck",
                "skip_external": True,
            }

            for med in active_meds:
                drug_name = med.get("drug_name")
                normalized_key = (active_patient_id, engine._normalize_drug(drug_name))
                if not drug_name or normalized_key in seen_drugs:
                    continue
                seen_drugs.add(normalized_key)
                evaluation = engine.evaluate(active_patient_id, drug_name, shared_context)
                summary["medications_checked"] += 1
                med_alerts = []

                for interaction in evaluation.get("interactions", []):
                    severity = str(interaction.get("severity") or "medium").lower()
                    if severity not in {"medium", "high", "critical"}:
                        continue
                    existing_drug = interaction.get("existing_drug") or interaction.get("drug_b")
                    pair_key = _alert_pair_key(drug_name, existing_drug, severity)
                    unordered_pair = tuple(sorted(pair_key[1:]))
                    if not all(unordered_pair) or unordered_pair in seen_pairs:
                        continue
                    seen_pairs.add(unordered_pair)
                    if pair_key in recent_alert_pairs:
                        continue

                    alert_id = db.create_alert(
                        active_patient_id,
                        "drug_interaction",
                        severity,
                        interaction.get("message") or f"Potential interaction between {drug_name} and {existing_drug}.",
                        _alert_payload(
                            drug_name,
                            existing_drug,
                            interaction,
                            _float_or_none(med.get("dose_amount")),
                            med.get("prescribed_by"),
                            "existing_medication_recheck",
                        ),
                    )
                    if alert_id:
                        recent_alert_pairs.add(pair_key)
                        patient_alerts.append(alert_id)
                        med_alerts.append(alert_id)
                        if severity == "critical":
                            rule_hash = _interaction_rule_hash(
                                active_patient_id,
                                engine._normalize_drug(drug_name),
                                existing_drug,
                            )
                            if _insert_agent_approval(rule_hash, drug_name, existing_drug, severity, evaluation):
                                patient_approvals.append(rule_hash)
                        if severity in {"high", "critical"}:
                            _queue_caregiver_notification(
                                active_patient_id,
                                patient_name,
                                force=severity == "critical",
                                trigger_message=interaction.get("message"),
                            )

                summary["results"].append(
                    {
                        "patient_id": active_patient_id,
                        "drug_name": drug_name,
                        "status": "completed",
                        "max_severity": evaluation.get("max_severity"),
                        "interactions_count": len(evaluation.get("interactions", [])),
                        "alerts_created": med_alerts,
                    }
                )

            db.write_audit(
                patient_id=active_patient_id,
                profile_id=None,
                entity_type="pharma_agent",
                entity_id=None,
                action="PHARMA_AGENT_EXISTING_MEDICATION_RECHECK",
                actor_role="system",
                new_value={
                    "medications_checked": summary["medications_checked"],
                    "alerts_created": patient_alerts,
                    "approvals_created": patient_approvals,
                },
            )
            _refresh_crisis_cache_async(active_patient_id, patient_name)
        except Exception as error:
            summary["errors"].append(f"{active_patient_id}:{error}")
    return summary


def _float_or_none(value) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None


def _enqueue_research_for_unmatched_pairs(
    patient_id: str,
    medication_id: str | None,
    new_drug: str,
    normalized_new: str,
    active_meds: list[dict],
    evaluation_result: dict,
    trigger: str,
    from_phone: str | None,
) -> list[str]:
    if not getattr(config, "PHARMA_RESEARCH_ENABLED", True):
        return []
    try:
        engine = PharmaSafetyEngine.__new__(PharmaSafetyEngine)
        queued = []
        max_pairs = max(0, int(getattr(config, "PHARMA_RESEARCH_MAX_PAIRS_PER_MEDICATION", 3)))
        seen_pairs = set()
        for med in active_meds or []:
            if len(queued) >= max_pairs:
                break
            if not isinstance(med, dict):
                continue
            existing = med.get("drug_name")
            normalized_existing = PharmaSafetyEngine._normalize_drug(engine, existing)
            if not normalized_existing or normalized_existing == normalized_new:
                continue
            pair = tuple(sorted([normalized_new, normalized_existing]))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            task_id = db.enqueue_pharma_interaction_research(
                patient_id=patient_id,
                medication_id=medication_id,
                drug_a=new_drug,
                drug_b=existing,
                trigger=trigger,
                from_phone=from_phone,
            )
            if task_id:
                queued.append(task_id)
        return queued
    except Exception as error:
        try:
            db.write_audit(
                patient_id=patient_id,
                profile_id=None,
                entity_type="pharma_research",
                entity_id=None,
                action="PHARMA_RESEARCH_ENQUEUE_FAILED",
                actor_role="system",
                new_value={"new_drug": new_drug, "trigger": trigger, "error": str(error)},
            )
        except Exception:
            pass
        return []


def enqueue_research_for_medication_candidate(
    patient_id: str,
    medication_id: str,
    drug_name: str,
    trigger: str = "medication_candidate",
    from_phone: str | None = None,
) -> list[str]:
    """
    Queue evidence-only interaction research for a non-rejected medication row.
    This is intentionally separate from activation: suspicious candidates remain
    held, but PharmaAgent can still build reports for caregiver review.
    """
    if not getattr(config, "PHARMA_RESEARCH_ENABLED", True):
        return []
    try:
        if not patient_id or not medication_id or not _researchable_drug_name(drug_name):
            return []
        engine = PharmaSafetyEngine.__new__(PharmaSafetyEngine)
        normalized_new = PharmaSafetyEngine._normalize_drug(engine, drug_name)
        queued = []
        seen_pairs = set()
        max_pairs = max(0, int(getattr(config, "PHARMA_RESEARCH_MAX_PAIRS_PER_MEDICATION", 10)))
        candidates = db.get_medication_research_candidates(
            patient_id,
            exclude_medication_id=medication_id,
            limit=max_pairs + 10,
        )
        for existing in candidates:
            if len(queued) >= max_pairs:
                break
            existing_name = existing.get("drug_name")
            if not _researchable_drug_name(existing_name):
                continue
            normalized_existing = PharmaSafetyEngine._normalize_drug(engine, existing_name)
            if not normalized_existing or normalized_existing == normalized_new:
                continue
            pair = tuple(sorted([normalized_new, normalized_existing]))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            task_id = db.enqueue_pharma_interaction_research(
                patient_id=patient_id,
                medication_id=medication_id,
                drug_a=drug_name,
                drug_b=existing_name,
                trigger=trigger,
                from_phone=from_phone,
            )
            if task_id:
                queued.append(task_id)
        return queued
    except Exception as error:
        try:
            db.write_audit(
                patient_id=patient_id,
                profile_id=None,
                entity_type="pharma_research",
                entity_id=None,
                action="PHARMA_CANDIDATE_RESEARCH_ENQUEUE_FAILED",
                actor_role="system",
                new_value={
                    "medication_id": medication_id,
                    "drug_name": drug_name,
                    "trigger": trigger,
                    "error": str(error),
                },
            )
        except Exception:
            pass
        return []


def _researchable_drug_name(value: str | None) -> bool:
    text = str(value or "").strip()
    lowered = text.lower()
    if len(text) < 3:
        return False
    if not re.search(r"[a-zA-Z]{3,}", text):
        return False
    non_drug_phrases = {
        "do not exceed",
        "after food",
        "before food",
        "with food",
        "empty stomach",
        "review after",
        "follow up",
        "avoid heavy",
    }
    if lowered in non_drug_phrases:
        return False
    if any(lowered.startswith(prefix) for prefix in ("do not ", "avoid ", "follow ", "review ")):
        return False
    return True


def _queue_caregiver_notification(
    patient_id: str,
    patient_name: str,
    force: bool = False,
    trigger_message: str | None = None,
    triggered_by_phone: str | None = None,
) -> list[dict]:
    """
    Queue caregiver notification work so PharmaAgent decisions never block on
    crisis-card construction, hospital lookup, or audit-log delivery.
    """
    def _worker():
        try:
            notifications.send_caregiver_notifications(
                patient_id=patient_id,
                patient_name=patient_name,
                force=force,
                trigger_message=trigger_message,
                triggered_by_phone=triggered_by_phone,
            )
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True).start()
    return [
        {
            "notification_status": "queued",
            "delivery_channel": "background_audit_log",
            "trigger_message": trigger_message,
        }
    ]


def _send_primary_caregiver_alert(
    patient_id: str,
    patient_name: str,
    summary: dict,
    triggered_by_phone: str | None,
    severity: str,
    approval_code: str | None = None,
) -> dict:
    """
    Synchronously log/send the short caregiver-facing medication safety alert.
    """
    try:
        import notification_dispatcher

        caregivers = db.get_caregivers(patient_id)
        primary = next(
            (
                caregiver
                for caregiver in caregivers
                if str(caregiver.get("role") or "").lower().replace(" ", "_") == "primary_caregiver"
            ),
            None,
        )
        target = primary or (caregivers[0] if caregivers else {})
        message = _format_three_part_alert(patient_name, summary, severity, approval_code=approval_code)
        delivery = notification_dispatcher.dispatch_user_message(
            target.get("phone"),
            message,
            patient_id=patient_id,
            priority="critical" if severity == "critical" else "high",
        )
        db.write_audit(
            patient_id=patient_id,
            profile_id=None,
            entity_type="pharma_agent_notification",
            entity_id=None,
            action="PRIMARY_CAREGIVER_MEDICATION_ALERT",
            actor_role="system",
            new_value={
                "severity": severity,
                "to_phone": target.get("phone"),
                "triggered_by_phone": triggered_by_phone,
                "summary": summary,
                "approval_code_issued": bool(approval_code),
                "delivery": delivery,
            },
        )
        return {"notification_status": delivery.get("status"), "delivery": delivery, "primary_phone": target.get("phone")}
    except Exception as error:
        return {"notification_status": "failed", "error": str(error)}


def _three_part_summary(new_drug: str | None, existing_drug: str | None, finding: dict) -> dict:
    severity = str(finding.get("severity") or "medium").lower()
    message = str(finding.get("message") or "").strip()
    if existing_drug:
        risk = f"{new_drug} with {existing_drug} is flagged as {severity} risk."
    else:
        risk = f"{new_drug or 'This medicine'} is flagged as {severity} risk."
    if message:
        why = message
    elif finding.get("type") == "renal_context_missing":
        why = "Kidney-function data is missing, so dosing safety cannot be fully verified."
    else:
        why = "CareCircle found a medication safety item that needs review."

    if severity == "critical":
        action = "Contact the doctor or primary caregiver urgently before continuing this combination unless already approved."
    elif severity == "high":
        action = "Contact the doctor or pharmacist soon and monitor closely."
    elif severity == "medium":
        action = "Review this with the caregiver team and include it in the next doctor discussion."
    else:
        action = "Monitor and keep the medication record updated."
    return {
        "severity": severity,
        "risk": risk[:380],
        "why_it_matters": why[:380],
        "what_to_do_now": action[:380],
    }


def _format_three_part_alert(patient_name: str, summary: dict, severity: str, approval_code: str | None = None) -> str:
    lines = [
        f"PharmaAgent alert for {patient_name}",
        f"Severity: {str(severity or summary.get('severity') or 'medium').upper()}",
        f"1. Risk: {summary.get('risk')}",
        f"2. Why it matters: {summary.get('why_it_matters')}",
        f"3. What to do now: {summary.get('what_to_do_now')}",
    ]
    if approval_code:
        lines.append(f"Primary caregiver command: APPROVE {approval_code} or VETO {approval_code}")
    return "\n".join(lines)


def _refresh_crisis_cache_async(patient_id: str, patient_name: str) -> None:
    """
    Refresh emergency card cache in the background. The interaction decision is
    safety-critical and must not wait for OSM/hospital lookup or cache rebuilds.
    """
    def _worker():
        try:
            card = crisis.build_crisis_card(patient_id, patient_name)
            db.upsert_crisis_cache(patient_id, card)
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True).start()


def _agent_approval_exists(rule_hash: str) -> bool:
    connection = None
    try:
        connection = db._connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM agent_approvals
                WHERE rule_hash = %s
                LIMIT 1;
                """,
                (rule_hash,),
            )
            return cursor.fetchone() is not None
    except Exception:
        return False
    finally:
        if connection is not None:
            connection.close()


def _interaction_rule_hash(patient_id: str, normalized_new: str, existing_drug: str) -> str:
    engine = PharmaSafetyEngine.__new__(PharmaSafetyEngine)
    drugs = sorted(
        drug
        for drug in [
            PharmaSafetyEngine._normalize_drug(engine, normalized_new),
            PharmaSafetyEngine._normalize_drug(engine, existing_drug),
        ]
        if drug
    )
    return hashlib.sha256(f"{patient_id}:{':'.join(drugs)}".encode()).hexdigest()


def _alert_pair_key(drug_a: str, drug_b: str, severity: str) -> tuple[str, str, str]:
    engine = PharmaSafetyEngine.__new__(PharmaSafetyEngine)
    pair = sorted(
        drug
        for drug in [
            PharmaSafetyEngine._normalize_drug(engine, drug_a),
            PharmaSafetyEngine._normalize_drug(engine, drug_b),
        ]
        if drug
    )
    if len(pair) != 2:
        return (str(severity or "").lower(), "", "")
    return (str(severity or "").lower(), pair[0], pair[1])


def _recent_interaction_alert_pairs(patient_id: str, hours: int = 24) -> set[tuple[str, str, str]]:
    connection = None
    try:
        connection = db._connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT severity, data_payload
                FROM alerts
                WHERE patient_id = %s
                  AND type = 'drug_interaction'
                  AND status = 'open'
                  AND created_at > NOW() - (%s || ' hours')::interval
                ORDER BY created_at DESC
                LIMIT 200;
                """,
                (patient_id, hours),
            )
            rows = cursor.fetchall()

        pairs = set()
        for severity, payload in rows:
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = {}
            if not isinstance(payload, dict):
                continue
            pairs.add(_alert_pair_key(payload.get("new_drug"), payload.get("existing_drug"), severity))
        return pairs
    except Exception:
        return set()
    finally:
        if connection is not None:
            connection.close()


def _recent_context_alert_exists(patient_id: str, alert_type: str, drug_name: str, severity: str, hours: int = 24) -> bool:
    connection = None
    try:
        engine = PharmaSafetyEngine.__new__(PharmaSafetyEngine)
        target = PharmaSafetyEngine._normalize_drug(engine, drug_name)
        connection = db._connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT data_payload
                FROM alerts
                WHERE patient_id = %s
                  AND type = %s
                  AND severity = %s
                  AND status = 'open'
                  AND created_at > NOW() - (%s || ' hours')::interval
                ORDER BY created_at DESC
                LIMIT 100;
                """,
                (patient_id, alert_type, severity, hours),
            )
            rows = cursor.fetchall()
        for row in rows:
            payload = row[0] if row else {}
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = {}
            if not isinstance(payload, dict):
                continue
            candidate = (
                payload.get("new_drug")
                or (payload.get("warning") or {}).get("drug")
                or (payload.get("interaction") or {}).get("drug")
            )
            if PharmaSafetyEngine._normalize_drug(engine, candidate) == target:
                return True
        return False
    except Exception:
        return False
    finally:
        if connection is not None:
            connection.close()


def _recent_interaction_alert_exists(
    patient_id: str,
    normalized_new: str,
    existing_drug: str,
    severity: str,
    hours: int = 24,
) -> bool:
    connection = None
    try:
        engine = PharmaSafetyEngine.__new__(PharmaSafetyEngine)
        target_pair = sorted(
            drug
            for drug in [
                PharmaSafetyEngine._normalize_drug(engine, normalized_new),
                PharmaSafetyEngine._normalize_drug(engine, existing_drug),
            ]
            if drug
        )
        if len(target_pair) != 2:
            return False

        connection = db._connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT data_payload
                FROM alerts
                WHERE patient_id = %s
                  AND type = 'drug_interaction'
                  AND severity = %s
                  AND status = 'open'
                  AND created_at > NOW() - (%s || ' hours')::interval
                ORDER BY created_at DESC
                LIMIT 100;
                """,
                (patient_id, severity, hours),
            )
            rows = cursor.fetchall()

        for row in rows:
            payload = row[0] if row else {}
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = {}
            if not isinstance(payload, dict):
                continue
            pair = sorted(
                drug
                for drug in [
                    PharmaSafetyEngine._normalize_drug(engine, payload.get("new_drug")),
                    PharmaSafetyEngine._normalize_drug(engine, payload.get("existing_drug")),
                ]
                if drug
            )
            if pair == target_pair:
                return True
        return False
    except Exception:
        return False
    finally:
        if connection is not None:
            connection.close()


def _insert_agent_approval(rule_hash: str, drug_a: str, drug_b: str, severity: str, evaluation_result: dict) -> bool:
    connection = None
    try:
        connection = db._connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO agent_approvals (
                    rule_hash,
                    drug_a,
                    drug_b,
                    severity,
                    status,
                    auto_approve_eligible,
                    veto_expiry,
                    confidence,
                    gates_result
                )
                VALUES (%s, %s, %s, %s, 'pending', false, NOW() + INTERVAL '48 hours', %s, %s::jsonb)
                ON CONFLICT (rule_hash) DO NOTHING
                RETURNING id;
                """,
                (
                    rule_hash,
                    drug_a,
                    drug_b,
                    severity,
                    1.0,
                    json.dumps(evaluation_result, default=str),
                ),
            )
            row = cursor.fetchone()
        connection.commit()
        return row is not None
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
        return False
    finally:
        if connection is not None:
            connection.close()


def _ensure_approval_challenge(rule_hash: str) -> str | None:
    try:
        code = f"{secrets.randbelow(900000) + 100000}"
        hours = int(getattr(config, "PHARMAGENT_VETO_EXPIRY_HOURS", 48))
        if db.set_agent_approval_challenge(rule_hash, code, expiry_hours=hours):
            return code
    except Exception:
        pass
    return None


def _update_alert_with_explanation(alert_id: str, explanation: dict) -> None:
    connection = None
    try:
        connection = db._connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE alerts
                SET data_payload = COALESCE(data_payload, '{}'::jsonb) || %s::jsonb
                WHERE id = %s;
                """,
                (
                    json.dumps({"pharma_explanation": explanation}, default=str),
                    alert_id,
                ),
            )
        connection.commit()
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
    finally:
        if connection is not None:
            connection.close()


def _alert_payload(
    new_drug: str,
    existing_drug: str,
    interaction: dict,
    dose_amount: float | None,
    prescribed_by: str | None,
    trigger: str,
    plain_language_summary: dict | None = None,
) -> dict:
    return {
        "new_drug": new_drug,
        "existing_drug": existing_drug,
        "interaction": interaction,
        "dose_amount": dose_amount,
        "prescribed_by": prescribed_by,
        "trigger": trigger,
        "plain_language_summary": plain_language_summary or {},
    }


def _first_explanation_target(evaluation_result: dict) -> dict:
    for key in ("interactions", "medication_context_warnings", "renal_warnings", "condition_warnings"):
        values = evaluation_result.get(key) or []
        if values:
            return values[0]
    return evaluation_result
