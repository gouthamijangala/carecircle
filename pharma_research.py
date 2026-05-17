"""
Shadow-mode PharmaAgent research pipeline.

Layer 2 enriches unknown or weak drug pairs with external evidence, synthesis,
critic checks, and safety gates. It does not activate production rules yet.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import requests

import config
import db
from drug_resolver import resolve_drug_name
from pharma_tools import DrugInteractionTool, ModelRouter


SEVERITY_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def verify_drug_interaction_registry(limit: int | None = None, force: bool = False) -> dict:
    """
    Re-check active drug_interactions rules against live evidence tools.
    This never deletes rules; it annotates confidence, evidence, and review status.
    """
    summary = {
        "status": "completed",
        "checked": 0,
        "updated": 0,
        "skipped_recent": 0,
        "errors": [],
        "results": [],
    }
    try:
        db.ensure_pharma_rule_registry_schema()
        rows = db.get_all_drug_interactions()
        max_rows = int(limit or getattr(config, "PHARMA_RULE_LIVE_VERIFICATION_LIMIT", 20))
        ttl_hours = int(getattr(config, "PHARMA_RULE_LIVE_VERIFICATION_TTL_HOURS", 168))
        due_rows = []
        now = datetime.now(timezone.utc)
        for row in rows:
            last_verified = _parse_datetime(row.get("last_verified_at"))
            if not force and last_verified and now - last_verified < timedelta(hours=ttl_hours):
                summary["skipped_recent"] += 1
                continue
            due_rows.append(row)
            if len(due_rows) >= max_rows:
                break

        tool = DrugInteractionTool()
        for row in due_rows:
            drug_a = row.get("drug_a")
            drug_b = row.get("drug_b")
            try:
                live_results = tool.check_all_interactions(drug_a, drug_b)
                pubmed_result = _pubmed_search_with_status(drug_a, drug_b)
                verification = _verification_decision(row, live_results, pubmed_result)
                ok = db.update_drug_interaction_verification(
                    drug_a=drug_a,
                    drug_b=drug_b,
                    verification_status=verification["verification_status"],
                    confidence_score=verification["confidence_score"],
                    tool_results=verification["tool_results"],
                    evidence_urls=verification["evidence_urls"],
                    review_required=verification["review_required"],
                )
                summary["checked"] += 1
                summary["updated"] += 1 if ok else 0
                summary["results"].append(
                    {
                        "drug_a": drug_a,
                        "drug_b": drug_b,
                        "verification_status": verification["verification_status"],
                        "confidence_score": verification["confidence_score"],
                        "review_required": verification["review_required"],
                        "tool_statuses": verification["tool_statuses"],
                    }
                )
            except Exception as row_error:
                summary["errors"].append({"drug_a": drug_a, "drug_b": drug_b, "error": str(row_error)})
        return summary
    except Exception as error:
        summary["status"] = "error"
        summary["errors"].append(str(error))
        return summary


def _parse_datetime(value) -> datetime | None:
    try:
        if not value:
            return None
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _verification_decision(rule: dict, live_results: dict, pubmed_result: dict) -> dict:
    attempts = list((live_results or {}).get("tool_attempts") or [])
    attempts.append(_attempt_summary(pubmed_result))
    rxnav = (live_results or {}).get("rxnav") or {}
    openfda = (live_results or {}).get("openfda") or {}
    merged = (live_results or {}).get("merged")
    pubmed_evidence = (pubmed_result or {}).get("evidence") or []
    direct_live_ok = any(
        isinstance(item, dict) and item.get("status") == "ok" and _evidence_count(item.get("evidence")) > 0
        for item in (rxnav, openfda)
    )
    literature_ok = bool(pubmed_evidence)
    confidence = float(rule.get("confidence_score") or 0.60)
    if direct_live_ok:
        confidence = max(confidence, 0.88)
    elif literature_ok:
        confidence = max(confidence, 0.70)
    else:
        confidence = min(confidence, 0.55)

    evidence_urls = [
        str(item.get("url"))
        for item in pubmed_evidence
        if isinstance(item, dict) and str(item.get("url") or "").strip()
    ]
    verification_status = "live_verified" if direct_live_ok else "review_required"
    review_required = not direct_live_ok
    tool_results = {
        "local_rule": {
            "drug_a": rule.get("drug_a"),
            "drug_b": rule.get("drug_b"),
            "severity": rule.get("severity"),
            "source": rule.get("source"),
            "message_template": rule.get("message_template"),
        },
        "rxnav": rxnav,
        "openfda": openfda,
        "merged": merged,
        "pubmed_status": pubmed_result,
        "tool_attempts": attempts,
        "verification_reason": (
            "direct_live_evidence" if direct_live_ok else
            "literature_only_needs_review" if literature_ok else
            "no_external_confirmation"
        ),
    }
    return {
        "verification_status": verification_status,
        "confidence_score": round(max(0.0, min(1.0, confidence)), 2),
        "review_required": review_required,
        "tool_results": tool_results,
        "evidence_urls": evidence_urls,
        "tool_statuses": {item.get("source", "unknown"): item.get("status") for item in attempts if isinstance(item, dict)},
    }


def run_interaction_research(
    patient_id: str,
    medication_id: str | None,
    drug_a: str,
    drug_b: str,
    trigger: str = "pharm_research",
) -> dict:
    """
    Run resolver -> planner -> tools -> synthesis -> critic -> gates.
    Returns a structured envelope and persists it to pharma_research_reports.
    """
    try:
        db.ensure_pharma_research_tables()
        research_key = _research_key(patient_id, drug_a, drug_b)
        resolver_result = _resolve_pair(drug_a, drug_b)
        resolved_a = resolver_result["drug_a"]["canonical"] or drug_a
        resolved_b = resolver_result["drug_b"]["canonical"] or drug_b

        patient_context = _patient_context(patient_id)
        planner_result = _plan_tools(resolved_a, resolved_b, patient_context)
        tool_results = _execute_tools(resolved_a, resolved_b, planner_result, patient_context)
        synthesis = _synthesize_report(resolved_a, resolved_b, tool_results, patient_context)
        critic = _critic_check(synthesis, tool_results, patient_context)
        gates = _run_safety_gates(synthesis, critic, tool_results, patient_context)

        status = "shadow_passed" if gates.get("all_passed") else "shadow_failed"
        if not getattr(config, "PHARMA_RESEARCH_SHADOW_MODE", True) and gates.get("all_passed"):
            status = "ready_for_veto"

        report = {
            "research_key": research_key,
            "patient_id": patient_id,
            "medication_id": medication_id,
            "drug_a": resolved_a,
            "drug_b": resolved_b,
            "resolver_result": resolver_result,
            "planner_result": planner_result,
            "tool_results": tool_results,
            "synthesis": synthesis,
            "critic": critic,
            "gates_result": gates,
            "status": status,
            "severity": synthesis.get("severity"),
            "confidence": synthesis.get("confidence"),
            "evidence_count": len(synthesis.get("evidence") or []),
            "error_message": None,
        }
        report_id = db.upsert_pharma_research_report(report)
        report["id"] = report_id
        promotion_result = None
        if gates.get("all_passed"):
            try:
                import pharma_promotion

                promotion_result = pharma_promotion.stage_research_report_for_approval(research_key)
            except Exception as promotion_error:
                promotion_result = {"status": "error", "error": str(promotion_error)}
        db.write_audit(
            patient_id=patient_id,
            profile_id=None,
            entity_type="pharma_research",
            entity_id=None,
            action="PHARMA_RESEARCH_COMPLETED",
            actor_role="system",
            new_value={
                "research_key": research_key,
                "report_id": report_id,
                "drug_a": resolved_a,
                "drug_b": resolved_b,
                "status": status,
                "severity": synthesis.get("severity"),
                "confidence": synthesis.get("confidence"),
                "gates": gates,
                "trigger": trigger,
                "promotion": promotion_result,
            },
        )
        return {
            "success": True,
            "report_id": report_id,
            "research_key": research_key,
            "status": status,
            "severity": synthesis.get("severity"),
            "confidence": synthesis.get("confidence"),
            "gates": gates,
            "synthesis": synthesis,
            "promotion": promotion_result,
        }
    except Exception as error:
        message = str(error)
        try:
            db.write_audit(
                patient_id=patient_id,
                profile_id=None,
                entity_type="pharma_research",
                entity_id=None,
                action="PHARMA_RESEARCH_ERROR",
                actor_role="system",
                new_value={
                    "drug_a": drug_a,
                    "drug_b": drug_b,
                    "medication_id": medication_id,
                    "trigger": trigger,
                    "error": message,
                },
            )
        except Exception:
            pass
        return {"success": False, "error_message": message}


def _research_key(patient_id: str, drug_a: str, drug_b: str) -> str:
    pair = sorted([_normalize(drug_a), _normalize(drug_b)])
    return hashlib.sha256(f"{patient_id}:{pair[0]}:{pair[1]}".encode("utf-8")).hexdigest()


def _resolve_pair(drug_a: str, drug_b: str) -> dict:
    return {
        "drug_a": _resolve_one(drug_a),
        "drug_b": _resolve_one(drug_b),
    }


def _resolve_one(raw: str) -> dict:
    cleaned = _regex_drug_cleanup(raw)
    canonical, confidence = resolve_drug_name(cleaned)
    method = "local_or_formulary" if confidence >= 0.8 else "regex"
    review_required = confidence < 0.6

    if review_required and getattr(config, "PHARMA_RESEARCH_RXNAV_APPROX_ENABLED", False):
        rxnav_name = _rxnav_approximate_name(cleaned)
        if rxnav_name:
            canonical = rxnav_name
            confidence = 0.72
            method = "rxnav_approximate"
            review_required = False

    return {
        "raw": raw,
        "cleaned": cleaned,
        "canonical": canonical or cleaned,
        "confidence": confidence,
        "method": method,
        "human_review_required": review_required,
    }


def _regex_drug_cleanup(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    text = re.sub(r"^(?:tab|tablet|cap|capsule|inj|syrup|susp|fab|pab)\.?\s+", "", text, flags=re.I)
    text = re.sub(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml|iu|units?)\b", "", text, flags=re.I)
    return text.strip(" .:-")


def _rxnav_approximate_name(drug: str) -> str | None:
    try:
        response = requests.get(
            f"{config.RXNAV_API_BASE}/approximateTerm.json",
            params={"term": drug, "maxEntries": 1},
            timeout=5,
        )
        if response.status_code != 200:
            return None
        group = (response.json().get("approximateGroup") or {})
        candidates = group.get("candidate") or []
        if candidates and candidates[0].get("name"):
            return str(candidates[0]["name"])
    except Exception:
        return None
    return None


def _patient_context(patient_id: str) -> dict:
    return {
        "active_meds": db.get_active_medications_schedule(patient_id),
        "conditions": db.get_patient_conditions(patient_id),
        "renal_markers": db.get_patient_latest_renal_markers(patient_id),
        "recent_labs": db.get_recent_labs(patient_id, limit=20),
        "recent_vitals": db.get_recent_vitals(patient_id, limit=20),
        "patient_name": db.get_patient_name(patient_id) or "Patient",
    }


def _plan_tools(drug_a: str, drug_b: str, patient_context: dict) -> dict:
    default_tools = ["rxnav", "openfda"]
    if getattr(config, "PHARMA_RESEARCH_PUBMED_ENABLED", True):
        default_tools.append("pubmed")
    if _looks_like_herb(drug_a) or _looks_like_herb(drug_b):
        default_tools.append("herb_checker")

    if getattr(config, "PHARMA_RESEARCH_LLM_PLANNER_ENABLED", False):
        try:
            router = ModelRouter()
            prompt = (
                "Choose medication safety tools for this pair. Always include all reliable evidence tools "
                "that can apply; do not choose only one source unless the drug type makes other tools impossible. "
                "Return JSON with keys tools, rationale. Allowed tools: rxnav, openfda, pubmed, herb_checker.\n"
                f"Drug A: {drug_a}\nDrug B: {drug_b}\n"
                f"Patient context: {json.dumps(patient_context, default=str)}"
            )
            planned = router.call_primary(prompt, {"tools": ["rxnav", "openfda", "pubmed"], "rationale": ""})
            tools = planned.get("tools") if isinstance(planned, dict) else None
            if isinstance(tools, list) and tools:
                return {"method": "llm_planner", "tools": [str(tool) for tool in tools], "rationale": planned.get("rationale")}
        except Exception:
            pass

    return {"method": "deterministic_planner", "tools": default_tools, "rationale": "Baseline interaction evidence sweep."}


def _execute_tools(drug_a: str, drug_b: str, planner_result: dict, patient_context: dict) -> dict:
    tools = set(planner_result.get("tools") or [])
    results = {"tool_attempts": []}
    local_result = _local_rule_lookup(drug_a, drug_b)
    results["local_rules"] = local_result
    results["tool_attempts"].append(_attempt_summary(local_result))
    if "rxnav" in tools or "openfda" in tools:
        interaction_results = DrugInteractionTool().check_all_interactions(drug_a, drug_b)
        results["rxnav"] = interaction_results.get("rxnav")
        results["openfda"] = interaction_results.get("openfda")
        results["interaction_lookup"] = _stronger_interaction(
            local_result if local_result.get("status") == "ok" else None,
            interaction_results.get("merged"),
        )
        if interaction_results.get("cached"):
            results["cached_interaction"] = interaction_results.get("cached")
        attempts = interaction_results.get("tool_attempts") or [
            interaction_results.get("cached"),
            interaction_results.get("rxnav"),
            interaction_results.get("openfda"),
        ]
        for attempt in attempts:
            results["tool_attempts"].append(_attempt_summary(attempt))
    else:
        results["interaction_lookup"] = local_result if local_result.get("status") == "ok" else None
    if "pubmed" in tools:
        pubmed_result = _pubmed_search_with_status(drug_a, drug_b)
        results["pubmed"] = pubmed_result.get("evidence") or []
        results["pubmed_status"] = pubmed_result
        results["tool_attempts"].append(_attempt_summary(pubmed_result))
    if "herb_checker" in tools:
        herb_result = _herb_check_with_status(drug_a, drug_b)
        results["herb_checker"] = herb_result if herb_result.get("status") == "ok" else None
        results["herb_checker_status"] = herb_result
        results["tool_attempts"].append(_attempt_summary(herb_result))
    results["patient_context_flags"] = _context_flags(drug_a, drug_b, patient_context)
    return results


def _tool_result(source: str, status: str, severity: str = "none", description: str = "", evidence=None, error: str | None = None, latency_ms: int = 0) -> dict:
    return {
        "source": source,
        "status": status,
        "severity": severity,
        "description": description,
        "evidence": evidence,
        "error": error,
        "latency_ms": latency_ms,
    }


def _attempt_summary(result: dict | None) -> dict:
    item = result if isinstance(result, dict) else {}
    return {
        "source": item.get("source") or "unknown",
        "status": item.get("status") or "no_data",
        "severity": item.get("severity") or "none",
        "error": item.get("error"),
        "latency_ms": item.get("latency_ms") or 0,
        "evidence_count": _evidence_count(item.get("evidence")),
    }


def _stronger_interaction(left: dict | None, right: dict | None) -> dict | None:
    if not isinstance(left, dict):
        return right if isinstance(right, dict) else None
    if not isinstance(right, dict):
        return left
    left_rank = SEVERITY_RANK.get(str(left.get("severity") or "none").lower(), 0)
    right_rank = SEVERITY_RANK.get(str(right.get("severity") or "none").lower(), 0)
    return left if left_rank >= right_rank else right


def _evidence_count(evidence) -> int:
    if isinstance(evidence, list):
        return len(evidence)
    if isinstance(evidence, dict):
        return len([value for value in evidence.values() if value])
    return 1 if evidence else 0


def _local_rule_lookup(drug_a: str, drug_b: str) -> dict:
    start = time.time()
    try:
        engine_a = _normalize(drug_a)
        engine_b = _normalize(drug_b)
        for row in db.get_all_drug_interactions():
            a = _normalize(row.get("drug_a"))
            b = _normalize(row.get("drug_b"))
            if {a, b} == {engine_a, engine_b}:
                latency = int((time.time() - start) * 1000)
                return _tool_result(
                    "local_drug_interactions",
                    "ok",
                    severity=str(row.get("severity") or "medium").lower(),
                    description=row.get("message_template") or f"Known interaction between {drug_a} and {drug_b}.",
                    evidence={"drug_a": row.get("drug_a"), "drug_b": row.get("drug_b"), "table": "drug_interactions"},
                    latency_ms=latency,
                )
        return _tool_result("local_drug_interactions", "no_data", latency_ms=int((time.time() - start) * 1000))
    except Exception as error:
        return _tool_result("local_drug_interactions", "error", error=str(error), latency_ms=int((time.time() - start) * 1000))


def _pubmed_search_with_status(drug_a: str, drug_b: str) -> dict:
    start = time.time()
    try:
        evidence = _pubmed_search(drug_a, drug_b)
        status = "ok" if evidence else "no_data"
        return _tool_result("pubmed", status, evidence=evidence, latency_ms=int((time.time() - start) * 1000))
    except Exception as error:
        return _tool_result("pubmed", "error", error=str(error), latency_ms=int((time.time() - start) * 1000))


def _herb_check_with_status(drug_a: str, drug_b: str) -> dict:
    start = time.time()
    try:
        result = _herb_check(drug_a, drug_b)
        if isinstance(result, dict):
            result = dict(result)
            result.setdefault("source", "herb_interactions")
            result.setdefault("status", "ok")
            result.setdefault("severity", result.get("severity") or "medium")
            result.setdefault("evidence", {"table": "herb_interactions"})
            result.setdefault("error", None)
            result["latency_ms"] = int((time.time() - start) * 1000)
            return result
        return _tool_result("herb_interactions", "no_data", latency_ms=int((time.time() - start) * 1000))
    except Exception as error:
        return _tool_result("herb_interactions", "error", error=str(error), latency_ms=int((time.time() - start) * 1000))


def _pubmed_search(drug_a: str, drug_b: str) -> list[dict]:
    try:
        term = f'("{drug_a}"[Title/Abstract]) AND ("{drug_b}"[Title/Abstract]) AND interaction'
        response = requests.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            params={"db": "pubmed", "term": term, "retmode": "json", "retmax": 3},
            timeout=8,
        )
        if response.status_code != 200:
            return []
        ids = ((response.json().get("esearchresult") or {}).get("idlist") or [])[:3]
        return [
            {
                "source": "pubmed",
                "pmid": pmid,
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                "title": f"PubMed record {pmid}",
            }
            for pmid in ids
        ]
    except Exception:
        return []


def _herb_check(drug_a: str, drug_b: str) -> dict | None:
    connection = None
    try:
        connection = db._connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT herb_name, drug_name, severity, message
                FROM herb_interactions
                WHERE LOWER(herb_name) IN (LOWER(%s), LOWER(%s))
                   OR LOWER(drug_name) IN (LOWER(%s), LOWER(%s))
                LIMIT 1;
                """,
                (drug_a, drug_b, drug_a, drug_b),
            )
            row = cursor.fetchone()
        if not row:
            return None
        return {"herb_name": row[0], "drug_name": row[1], "severity": row[2], "description": row[3], "source": "herb_interactions"}
    except Exception:
        return None
    finally:
        if connection is not None:
            connection.close()


def _context_flags(drug_a: str, drug_b: str, patient_context: dict) -> list[dict]:
    flags = []
    active_meds = patient_context.get("active_meds") or []
    normalized_a = _normalize(drug_a)
    normalized_b = _normalize(drug_b)

    if not active_meds:
        flags.append({
            "type": "medication_context",
            "category": "medication_history",
            "status": "missing",
            "message": "No active medication list was available for this patient.",
        })
    else:
        same_drug_matches = [
            med for med in active_meds
            if isinstance(med, dict) and _normalize(med.get("drug_name")) in {normalized_a, normalized_b}
        ]
        if len(same_drug_matches) > 1:
            prescribers = sorted({str(med.get("prescribed_by") or "").strip() for med in same_drug_matches if med.get("prescribed_by")})
            flags.append({
                "type": "duplicate_therapy_context",
                "category": "duplicate_therapy",
                "status": "risk",
                "message": (
                    "The same medication appears more than once in active records"
                    + (f" from: {', '.join(prescribers[:3])}." if prescribers else ".")
                ),
            })

    renal_relevant = _renal_context_relevant(normalized_a, normalized_b, patient_context)
    renal = patient_context.get("renal_markers") or {}
    egfr = _marker_value(renal, "egfr")
    if egfr is None and renal_relevant:
        flags.append({
            "type": "kidney_function_context",
            "category": "renal",
            "status": "missing",
            "message": "No recent eGFR is available for kidney-related medication checks.",
        })
    elif egfr is not None and egfr < config.PHARMA_EGFR_WARNING_THRESHOLD:
        flags.append({
            "type": "kidney_function_context",
            "category": "renal",
            "status": "risk",
            "message": f"Latest eGFR is {egfr}, which may affect medication safety.",
        })
    conditions = [str(item.get("condition_name") or "").lower() for item in patient_context.get("conditions") or [] if isinstance(item, dict)]
    if not conditions:
        flags.append({
            "type": "condition_context",
            "category": "medical_history",
            "status": "missing",
            "message": "No patient condition list was available for contraindication checks.",
        })
    if any("kidney" in item or "renal" in item or "ckd" in item for item in conditions):
        flags.append({"type": "condition_context", "category": "renal", "status": "risk", "message": "Kidney/renal condition is recorded."})
    if any("liver" in item or "hepatic" in item for item in conditions):
        flags.append({"type": "condition_context", "category": "liver", "status": "risk", "message": "Liver/hepatic condition is recorded."})
    if any("diabetes" in item for item in conditions):
        flags.append({"type": "condition_context", "category": "diabetes", "status": "present", "message": "Diabetes is recorded; glucose-related medication effects need attention."})
    if any("hypertension" in item or "blood pressure" in item or "bp" == item.strip() for item in conditions):
        flags.append({"type": "condition_context", "category": "blood_pressure", "status": "present", "message": "Hypertension/blood-pressure context is recorded."})
    if any("allerg" in item and (normalized_a in item or normalized_b in item) for item in conditions):
        flags.append({"type": "allergy_context", "category": "allergy", "status": "risk", "message": "A recorded allergy appears to mention one of these medicines."})

    recent_labs = patient_context.get("recent_labs") or []
    abnormal_labs = [
        lab for lab in recent_labs
        if isinstance(lab, dict) and str(lab.get("severity") or "").lower() not in {"", "normal", "none"}
    ]
    if abnormal_labs:
        names = ", ".join(str(lab.get("test_name") or "lab") for lab in abnormal_labs[:3])
        flags.append({"type": "lab_context", "category": "abnormal_labs", "status": "risk", "message": f"Recent abnormal lab context exists: {names}."})

    bleeding_terms = {"warfarin", "aspirin", "clopidogrel", "apixaban", "rivaroxaban", "dabigatran", "heparin", "ibuprofen", "diclofenac", "naproxen"}
    if normalized_a in bleeding_terms and normalized_b in bleeding_terms:
        flags.append({
            "type": "bleeding_risk_context",
            "category": "bleeding",
            "status": "risk",
            "message": "Both medicines can contribute to bleeding risk; caregiver/doctor review is important.",
        })
    return flags


def _renal_context_relevant(normalized_a: str, normalized_b: str, patient_context: dict) -> bool:
    renal_drugs = {str(item).lower() for item in getattr(config, "PHARMA_RENAL_CLEARED_DRUGS", set())}
    if normalized_a in renal_drugs or normalized_b in renal_drugs:
        return True
    conditions = [
        str(item.get("condition_name") or "").lower()
        for item in patient_context.get("conditions") or []
        if isinstance(item, dict)
    ]
    return any("kidney" in item or "renal" in item or "ckd" in item for item in conditions)


def _synthesize_report(drug_a: str, drug_b: str, tool_results: dict, patient_context: dict) -> dict:
    deterministic = _deterministic_synthesis(drug_a, drug_b, tool_results)
    if not getattr(config, "PHARMA_RESEARCH_LLM_SYNTHESIS_ENABLED", True):
        return deterministic

    try:
        router = ModelRouter()
        schema = {
            "summary": "",
            "risk": "",
            "why_it_matters": "",
            "what_to_do_now": "",
            "severity": "none|low|medium|high|critical",
            "mechanism": "",
            "recommended_action": "monitor|consult|avoid|urgent_review",
            "evidence": [],
            "confidence": 0.0,
        }
        prompt = (
            "Create a compact structured drug-interaction research report. "
            "Do not invent evidence. Use only provided tool results and explicitly respect tool status/error fields. "
            "If every external source has no_data/error, say evidence is insufficient rather than guessing.\n"
            "Return a caregiver-safe 3-part summary in risk, why_it_matters, and what_to_do_now. "
            "Never advise stopping or starting medicine without clinician review.\n"
            f"Drug A: {drug_a}\nDrug B: {drug_b}\n"
            f"Tool results: {json.dumps(tool_results, default=str)}\n"
            f"Patient context: {json.dumps(patient_context, default=str)}"
        )
        parsed = router.call_primary(prompt, schema)
        if isinstance(parsed, dict) and parsed.get("summary"):
            return _merge_synthesis(deterministic, parsed)
    except Exception:
        pass
    return deterministic


def _deterministic_synthesis(drug_a: str, drug_b: str, tool_results: dict) -> dict:
    evidence = []
    severity = "none"
    local_rule = tool_results.get("local_rules")
    if isinstance(local_rule, dict) and local_rule.get("status") == "ok":
        severity = _max_severity(severity, local_rule.get("severity") or "medium")
        evidence.append(
            {
                "source": local_rule.get("source") or "local_drug_interactions",
                "url": "",
                "text": local_rule.get("description") or "",
            }
        )
    interaction = tool_results.get("interaction_lookup")
    if isinstance(interaction, dict) and interaction.get("status", "ok") == "ok":
        severity = _max_severity(severity, interaction.get("severity") or "medium")
        evidence.append(
            {
                "source": interaction.get("source") or "interaction_lookup",
                "url": interaction.get("url") or "",
                "text": interaction.get("description") or interaction.get("message") or "",
            }
        )
    herb = tool_results.get("herb_checker")
    if isinstance(herb, dict):
        severity = _max_severity(severity, herb.get("severity") or "medium")
        evidence.append({"source": "herb_checker", "url": "", "text": herb.get("description") or ""})
    for item in tool_results.get("pubmed") or []:
        if isinstance(item, dict):
            evidence.append({"source": "pubmed", "url": item.get("url") or "", "text": item.get("title") or ""})
    for flag in tool_results.get("patient_context_flags") or []:
        if isinstance(flag, dict) and flag.get("status") == "risk":
            severity = _max_severity(severity, "medium")
            evidence.append({"source": flag.get("type") or "patient_context", "url": "", "text": flag.get("message") or ""})
        elif isinstance(flag, dict) and flag.get("status") == "missing":
            severity = _max_severity(severity, "medium")
            evidence.append({"source": flag.get("type") or "patient_context", "url": "", "text": flag.get("message") or ""})

    confidence = min(0.95, 0.35 + 0.2 * len([item for item in evidence if item.get("source") != "pubmed"]) + 0.1 * len(evidence))
    if severity == "none":
        confidence = min(confidence, 0.45)
    risk = f"{drug_a} with {drug_b} is rated {severity} based on available evidence."
    why = evidence[0]["text"] if evidence else "No reliable interaction evidence was found from the attempted tools."
    action = _plain_action_for_severity(severity)
    return {
        "summary": f"Research found {severity} interaction evidence for {drug_a} with {drug_b}.",
        "risk": risk[:380],
        "why_it_matters": why[:380],
        "what_to_do_now": action[:380],
        "severity": severity,
        "mechanism": evidence[0]["text"] if evidence else "",
        "recommended_action": _action_for_severity(severity),
        "evidence": evidence,
        "confidence": round(confidence, 2),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _merge_synthesis(deterministic: dict, llm_data: dict) -> dict:
    merged = dict(deterministic)
    for key in ("summary", "risk", "why_it_matters", "what_to_do_now", "mechanism", "recommended_action"):
        if llm_data.get(key):
            merged[key] = str(llm_data[key])[:1000]
    merged["severity"] = _max_severity(deterministic.get("severity"), llm_data.get("severity"))
    try:
        merged["confidence"] = min(float(llm_data.get("confidence") or 0.0), float(deterministic.get("confidence") or 0.0) + 0.1)
    except Exception:
        pass
    return merged


def _critic_check(synthesis: dict, tool_results: dict, patient_context: dict) -> dict:
    evidence = synthesis.get("evidence") or []
    has_evidence = any(item.get("text") or item.get("url") for item in evidence if isinstance(item, dict))
    severity = str(synthesis.get("severity") or "none").lower()
    contradictions = []
    if severity in {"high", "critical"} and not has_evidence:
        contradictions.append("High severity requires evidence.")
    if synthesis.get("confidence", 0) and not has_evidence and float(synthesis.get("confidence") or 0) > 0.5:
        contradictions.append("Confidence too high without evidence.")
    return {
        "passed": not contradictions,
        "contradictions": contradictions,
        "method": "deterministic_critic",
    }


def _run_safety_gates(synthesis: dict, critic: dict, tool_results: dict, patient_context: dict) -> dict:
    evidence = synthesis.get("evidence") or []
    confidence = float(synthesis.get("confidence") or 0.0)
    safety = _nvidia_safety_gate(synthesis)
    attempts = tool_results.get("tool_attempts") or []
    attempted_sources = {str(item.get("source") or "") for item in attempts if isinstance(item, dict)}
    required_sources = {"local_drug_interactions", "rxnav", "openfda"}
    gates = {
        "evidence": any((item.get("url") or item.get("text")) for item in evidence if isinstance(item, dict)),
        "context": "patient_context_flags" in tool_results,
        "tool_ledger": required_sources.issubset(attempted_sources),
        "three_part_summary": all(synthesis.get(key) for key in ("risk", "why_it_matters", "what_to_do_now")),
        "contraindications": isinstance(patient_context.get("conditions"), list),
        "critic": bool(critic.get("passed")),
        "nvidia_safety": safety.get("passed"),
        "confidence": confidence >= float(getattr(config, "PHARMA_RESEARCH_MIN_CONFIDENCE", 0.85)),
    }
    gates["all_passed"] = all(gates.values())
    gates["safety_detail"] = safety
    return gates


def _nvidia_safety_gate(synthesis: dict) -> dict:
    if not getattr(config, "PHARMA_RESEARCH_NVIDIA_SAFETY_REQUIRED", False):
        return {"passed": True, "method": "not_required"}
    try:
        result = ModelRouter().call_safety_check(json.dumps(synthesis, default=str))
        allowed = bool(result.get("allowed") or result.get("safe"))
        return {"passed": allowed, "method": "nvidia_safety", "result": result}
    except Exception as error:
        return {"passed": False, "method": "nvidia_safety", "error": str(error)}


def _marker_value(markers: dict, key: str) -> float | None:
    try:
        item = markers.get(key)
        if isinstance(item, dict):
            return float(item.get("value"))
    except Exception:
        return None
    return None


def _looks_like_herb(value: str) -> bool:
    text = str(value or "").lower()
    return any(term in text for term in ("ashwagandha", "ginkgo", "garlic", "turmeric", "st john", "herb"))


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _max_severity(a: str | None, b: str | None) -> str:
    left = str(a or "none").lower()
    right = str(b or "none").lower()
    if left not in SEVERITY_RANK:
        left = "medium"
    if right not in SEVERITY_RANK:
        right = "medium"
    return left if SEVERITY_RANK[left] >= SEVERITY_RANK[right] else right


def _action_for_severity(severity: str) -> str:
    return {
        "critical": "urgent_review",
        "high": "consult",
        "medium": "monitor",
        "low": "monitor",
        "none": "monitor",
    }.get(str(severity or "none").lower(), "monitor")


def _plain_action_for_severity(severity: str) -> str:
    severity = str(severity or "none").lower()
    if severity == "critical":
        return "Contact the doctor or primary caregiver urgently before continuing this combination unless already approved."
    if severity == "high":
        return "Contact the doctor or pharmacist soon and monitor closely."
    if severity == "medium":
        return "Review this with the caregiver team and include it in the next doctor discussion."
    if severity == "low":
        return "Monitor routinely and keep the medication record updated."
    return "No immediate action is suggested from current evidence; keep records updated."
