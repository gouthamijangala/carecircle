"""
Async document processing pipeline for parsed medical media.

This module composes the parser, document classifier, context reducer, prompt
templates, validator, LLM gateway, and DB writers into one safe orchestration
class. It never raises to callers; failures are returned as structured results.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import config
import db
from context_manager import MedicalContextManager
from doc_classifier import DocumentTypeClassifier
from extraction_engine import DocumentExtractor
from llm_gateway import call_chat_completion
from notifications import send_caregiver_notifications
from pharma_prompts import get_prompt_for_type
from validators import validate_and_clean


class AsyncDocumentPipeline:
    """
    Contract:
      Input: file_bytes (bytes), media_hint (str), patient_id (str), profile_id (str), file_hash (str)
      Output: dict {"status": str, "document_type": str, "extracted_data": dict, "validation_errors": list, "needs_review": bool, "review_reason": str|None, "processing_time_ms": int}
      Error: Returns status='error' with error_message, never raises.
    """

    def __init__(self):
        self.extractor = DocumentExtractor()
        self.classifier = DocumentTypeClassifier()

    def process(
        self,
        file_bytes: bytes,
        media_hint: str,
        patient_id: str,
        profile_id: str,
        file_hash: str,
        pending_task_id: str | None = None,
        file_path: str | None = None,
    ) -> dict:
        start = time.time()
        try:
            extract_res = self.extractor.extract(file_bytes, media_hint)
            raw_text = str(extract_res.get("text") or "")
            if not raw_text:
                return self._result(
                    start,
                    status="error",
                    document_type="unknown",
                    extracted_data={},
                    validation_errors=[],
                    needs_review=True,
                    review_reason="extraction_failed",
                    error_message="Extraction failed",
                )

            doc_type_res = self.classifier.classify(raw_text)
            doc_type = doc_type_res.get("document_type") or "unknown"
            if doc_type == "unknown":
                doc_type = "general_note"

            processed_text, truncated = MedicalContextManager.truncate_with_preservation(
                raw_text,
                config.MAX_OCR_CONTEXT_TOKENS,
                doc_type,
            )

            prompt = self._render_prompt(get_prompt_for_type(doc_type), processed_text)
            llm_response = self._call_extraction_llm(prompt)
            cleaned, errors, needs_review = validate_and_clean(llm_response or "", raw_text, doc_type)
            cleaned = self._normalize_for_doc_type(cleaned, doc_type)
            if self._extraction_is_empty(cleaned, doc_type):
                fallback = self._deterministic_extract(raw_text, doc_type)
                if not self._extraction_is_empty(fallback, doc_type):
                    cleaned = self._normalize_for_doc_type(fallback, doc_type)
                    errors = []
                    needs_review = False
            elif doc_type == "lab_report" and not any(
                self._valid_lab_for_insert(test)
                for test in self._items(cleaned.get("lab_values") or cleaned.get("tests"))
            ):
                fallback = self._deterministic_extract(raw_text, doc_type)
                if not self._extraction_is_empty(fallback, doc_type):
                    cleaned = self._normalize_for_doc_type(fallback, doc_type)
                    errors = []
                    needs_review = False

            media_upload_id = self._create_media_upload(
                patient_id=patient_id,
                profile_id=profile_id,
                pending_task_id=pending_task_id,
                file_path=file_path or file_hash,
                media_hint=media_hint,
                extract_res=extract_res,
                cleaned=cleaned,
                doc_type=doc_type,
                doc_type_res=doc_type_res,
                errors=errors,
                needs_review=needs_review,
                file_hash=file_hash,
            )
            if not media_upload_id:
                return self._result(
                    start,
                    status="error",
                    document_type=doc_type,
                    extracted_data=cleaned,
                    validation_errors=[
                        {
                            "field": "media_uploads",
                            "severity": "critical",
                            "message": "Could not create media audit row; entity inserts were skipped.",
                        }
                    ],
                    needs_review=True,
                    review_reason="media_upload_insert_failed",
                    error_message="Could not save parser audit trail. Please retry the upload.",
                    extraction_method=extract_res.get("method"),
                    classification=doc_type_res,
                )

            insert_result = self._insert_to_db(cleaned, doc_type, patient_id, file_hash, media_upload_id, raw_text=raw_text)
            if insert_result.get("errors"):
                errors = errors + [
                    {"field": "database_write", "severity": "critical", "message": str(item)}
                    for item in insert_result.get("errors", [])
                ]
                needs_review = True
            inserted_count = int(insert_result.get("count") or 0)
            if inserted_count == 0:
                if self._extraction_is_empty(cleaned, doc_type):
                    message = "No structured data could be extracted from parser text."
                elif doc_type in {"prescription", "lab_report"}:
                    message = "Structured data was extracted, but no safe database rows passed validation."
                else:
                    message = ""
                if message:
                    needs_review = True
                    errors = errors or [{"field": "database_write", "severity": "high", "message": message}]
            if media_upload_id:
                self._update_media_upload_result(
                    media_upload_id,
                    cleaned,
                    doc_type,
                    insert_result,
                    file_hash,
                    errors=errors,
                    needs_review=needs_review,
                )
            if inserted_count > 0:
                summary_msg = self._build_caregiver_summary(doc_type, cleaned, errors, needs_review)
                self._notify_processing_result(
                    patient_id=patient_id,
                    profile_id=profile_id,
                    doc_type=doc_type,
                    summary_msg=summary_msg,
                    cleaned=cleaned,
                    file_hash=file_hash,
                )

            review_reason = self._review_reason(
                errors=errors,
                needs_review=needs_review,
                confidence=float(doc_type_res.get("confidence") or 0.0),
                truncated=truncated,
            )

            return self._result(
                start,
                status="success",
                document_type=doc_type,
                extracted_data=cleaned,
                validation_errors=errors,
                needs_review=needs_review,
                review_reason=review_reason,
                inserted_count=inserted_count,
                inserted_ids=insert_result,
                media_upload_id=media_upload_id,
                pharma_agent_triggered=bool(insert_result.get("pharma_agent_triggered")),
                extraction_method=extract_res.get("method"),
                classification=doc_type_res,
            )
        except Exception as error:
            return self._result(
                start,
                status="error",
                document_type="unknown",
                extracted_data={},
                validation_errors=[],
                needs_review=True,
                review_reason="processing_error",
                error_message=str(error),
            )

    def _call_extraction_llm(self, prompt: str) -> str | None:
        return call_chat_completion(
            base_url=config.LLM_EXTRACTION_PRIMARY,
            model_id=config.LLM_EXTRACTION_PRIMARY_MODEL,
            api_key="lm-studio",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800,
            temperature=0,
            timeout=config.LLM_EXTRACTION_TIMEOUT,
            top_p=getattr(config, "LLM_EXTRACTION_TOP_P", 0.75),
            response_format={"type": "json_object"},
        )

    def _render_prompt(self, template: str, raw_text: str) -> str:
        return str(template or "").replace("{raw_text}", raw_text or "")

    def _deterministic_extract(self, raw_text: str, doc_type: str) -> dict:
        if doc_type == "prescription":
            return self._deterministic_prescription(raw_text)
        if doc_type == "lab_report":
            return self._deterministic_lab_report(raw_text)
        if doc_type in {"voice_note", "general_note"}:
            return self._deterministic_events(raw_text)
        return {}

    def _deterministic_prescription(self, raw_text: str) -> dict:
        meds = []
        current = None
        prefix_re = re.compile(
            r"^\s*(?:rx\s*)?(?:tab|tablet|cap|capsule|inj|injection|syrup|syp|syr|susp|suspension|drop|drops|cream|ointment|gel|lotion|spray|inhaler|neb|nebulization|nebulisation|nebuliser|nebulizer|solution|soln|sachet|vial|amp|ampoule|fab|pab)\.?\s*:?\s*(?P<body>.*)$",
            flags=re.IGNORECASE,
        )

        for line in self._candidate_lines(raw_text):
            match = prefix_re.match(line)
            starts_new_without_marker = self._looks_like_drug_dose_line(line)
            if match or starts_new_without_marker:
                body = (match.group("body") if match else line).strip()
                if current and current.get("drug_name_raw"):
                    meds.append(current)
                current = self._med_from_line(body, line) if body else self._empty_med_shell(line)
                continue

            if not current:
                continue

            if not current.get("drug_name_raw") and self._looks_like_drug_name_line(line):
                current.update(self._med_from_line(line, line))
                current["raw_segment"] = f"{current.get('raw_segment') or ''} {line}".strip()
                continue

            if current.get("drug_name_raw") and not current.get("dose_amount"):
                dose_amount, dose_unit = self._dose_from_line(line)
                if dose_amount is not None:
                    current["dose_amount"] = dose_amount
                    current["dose_unit"] = dose_unit

            freq_match = re.search(r"\b([01]-[01]-[01]|OD|BD|TDS|QID|HS|SOS)\b", line, flags=re.IGNORECASE)
            if freq_match and not current.get("frequency"):
                current["frequency"] = self._normalize_frequency(freq_match.group(1))

            instructions = self._extract_instructions(line)
            if instructions:
                existing = current.get("instructions")
                current["instructions"] = ", ".join(part for part in [existing, instructions] if part)
            advice = self._extract_advice(line)
            if advice:
                existing = current.get("advice")
                current["advice"] = "; ".join(part for part in [existing, advice] if part)

        if current:
            meds.append(current)
        document_date = self._extract_document_date(raw_text)
        return {
            "type": "prescription",
            "medications": self._dedupe_items(meds, "drug_name_raw"),
            "doctor_name": self._extract_doctor_name(raw_text),
            "date": document_date,
            "start_date": document_date,
        }

    def _empty_med_shell(self, raw_line: str) -> dict:
        return {
            "drug_name_raw": None,
            "drug_name_canonical": None,
            "dose_amount": None,
            "dose_unit": None,
            "frequency": None,
            "instructions": self._extract_instructions(raw_line),
            "advice": self._extract_advice(raw_line),
            "prescribed_by": None,
            "date": None,
            "extraction_method": "deterministic_fallback",
            "raw_segment": raw_line,
        }

    def _looks_like_drug_dose_line(self, line: str) -> bool:
        text = str(line or "").strip()
        lower = text.lower()
        if not text or self._is_prescription_context_line(text):
            return False
        if re.match(r"^\d+[\).]?$", text):
            return False
        return bool(
            re.search(r"[A-Za-z][A-Za-z0-9+\-/ ]{2,}\s+\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml|iu|units?|i\.?v\.?)\b", text, flags=re.IGNORECASE)
            or ("suspension" in lower and len(text.split()) <= 5)
        )

    def _looks_like_drug_name_line(self, line: str) -> bool:
        text = str(line or "").strip()
        if not text or self._is_prescription_context_line(text):
            return False
        if re.match(r"^\d+[\).]?$", text):
            return False
        if len(text) < 3 or len(text.split()) > 6:
            return False
        return bool(re.search(r"[A-Za-z]{3,}", text))

    def _is_prescription_context_line(self, line: str) -> bool:
        lower = str(line or "").strip().lower()
        if not lower:
            return True
        if re.fullmatch(
            r"(?:[01]-[01]-[01]|od|bd|tds|tid|qid|hs|sos)?\s*(?:after food|before food|with food|empty stomach)?\s*(?:[x×]\s*\d+\s*(?:days?|months?|weeks?))?\s*(?:\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml))?\s*",
            lower,
        ):
            return True
        context_prefixes = (
            "date", "name of the patient", "opno", "age", "address", "reg", "ph:",
            "advice", "follow up", "note:", "h/0", "s/p", "c/o", "no severe",
            "dr.", "md,", "consultant", "super speciality", "d.no", "sri ",
        )
        context_exact = {
            "after food", "before food", "with food", "empty stomach", "morning",
            "night", "sos", "od", "bd", "tds", "qid", "hs", "i.", "r", ":",
            "inj:", "in case of emergency, take this prescription to the nearest hospital.",
        }
        return lower in context_exact or lower.startswith(context_prefixes) or bool(re.match(r"^[x×]\s*\d+\s*(days?|months?|weeks?)$", lower))

    def _med_from_line(self, body: str, raw_line: str) -> dict:
        fraction_match = re.search(r"(\d+)\s*/\s*(\d+)\s*(tsp|teaspoon|tbsp|tablespoon|spoon)", body, flags=re.IGNORECASE)
        dose_match = re.search(
            r"(\d+(?:\.\d+)?)\s*(mg|mcg|g|ml|iu|unit|units|tsp|teaspoon|tbsp|tablespoon|spoon|drops?|puffs?|sprays?|sachet|vial|amp|ampoule)\b",
            body,
            flags=re.IGNORECASE,
        )
        if not dose_match:
            dose_match = re.search(r"(\d+(?:\.\d+)?)(?=\s+[01]-[01]-[01]\b)", body, flags=re.IGNORECASE)
        freq_match = re.search(r"\b([01]-[01]-[01]|OD|BD|TDS|QID|HS|SOS)\b", body, flags=re.IGNORECASE)
        drug_part = body
        if fraction_match:
            drug_part = body[: fraction_match.start()]
        elif dose_match:
            drug_part = body[: dose_match.start()]
        elif freq_match:
            drug_part = body[: freq_match.start()]
        drug_part = self._strip_advice_from_drug_name(drug_part)
        drug_part = re.sub(r"[-:]+$", "", drug_part).strip()
        if not drug_part:
            drug_part = body.strip()

        dose_amount = None
        dose_unit = None
        if fraction_match and float(fraction_match.group(2)) != 0:
            dose_amount = float(fraction_match.group(1)) / float(fraction_match.group(2))
            dose_unit = fraction_match.group(3)
        elif dose_match:
            dose_amount = float(dose_match.group(1))
            dose_unit = (
                dose_match.group(2)
                if getattr(dose_match, "lastindex", 0) and dose_match.lastindex >= 2 and dose_match.group(2)
                else "mg"
            )

        return {
            "drug_name_raw": drug_part,
            "drug_name_canonical": None,
            "dose_amount": dose_amount,
            "dose_unit": dose_unit,
            "frequency": self._normalize_frequency(freq_match.group(1) if freq_match else None),
            "instructions": self._extract_instructions(raw_line),
            "advice": self._extract_advice(raw_line),
            "prescribed_by": None,
            "date": None,
            "extraction_method": "deterministic_fallback",
            "raw_segment": raw_line,
        }

    def _dose_from_line(self, line: str) -> tuple[float | None, str | None]:
        fraction_match = re.search(r"(\d+)\s*/\s*(\d+)\s*(tsp|teaspoon|tbsp|tablespoon|spoon)", line, flags=re.IGNORECASE)
        if fraction_match and float(fraction_match.group(2)) != 0:
            return float(fraction_match.group(1)) / float(fraction_match.group(2)), fraction_match.group(3)
        match = re.search(
            r"(\d+(?:\.\d+)?)\s*(mg|mcg|g|ml|iu|unit|units|tsp|teaspoon|tbsp|tablespoon|spoon|drops?|puffs?|sprays?|sachet|vial|amp|ampoule)\b",
            line,
            flags=re.IGNORECASE,
        )
        if match:
            return float(match.group(1)), match.group(2)
        return None, None

    def _deterministic_lab_report(self, raw_text: str) -> dict:
        labs = []
        lines = self._candidate_lines(raw_text)
        for index, line in enumerate(lines):
            lab = self._parse_known_lab_line(line)
            if not lab:
                lab = self._parse_vertical_lab_row(lines, index)
            if lab:
                labs.append(lab)
        return {"type": "lab_report", "lab_values": self._dedupe_items(labs, "test_name"), "lab_name": None, "report_date": None}

    def _known_lab_aliases(self) -> dict[str, str]:
        return {
            "estimated average glucose": "estimated_average_glucose",
            "fasting blood sugar": "glucose",
            "random blood sugar": "glucose",
            "blood glucose": "glucose",
            "blood sugar": "glucose",
            "serum creatinine": "creatinine",
            "total leucocyte count": "wbc",
            "total leukocyte count": "wbc",
            "total wbc count": "wbc",
            "wbc count": "wbc",
            "platelet count": "platelets",
            "total cholesterol / hdl ratio": "cholesterol_hdl_ratio",
            "ldl cholesterol": "ldl_cholesterol",
            "hdl cholesterol": "hdl_cholesterol",
            "vldl cholesterol": "vldl_cholesterol",
            "total cholesterol": "cholesterol",
            "bun / creatinine ratio": "bun_creatinine_ratio",
            "alkaline phosphatase": "alkaline_phosphatase",
            "total bilirubin": "bilirubin",
            "total protein": "total_protein",
            "a/g ratio": "albumin_globulin_ratio",
            "pcv / hematocrit": "hematocrit",
            "packed cell volume": "hematocrit",
            "pcv": "hematocrit",
            "mean corpuscular volume": "mcv",
            "glycated hemoglobin": "hba1c",
            "hb a1c": "hba1c",
            "hba1c": "hba1c",
            "hemoglobin": "hemoglobin",
            "haemoglobin": "hemoglobin",
            "glucose": "glucose",
            "creatinine": "creatinine",
            "blood urea": "blood_urea",
            "egfr": "egfr",
            "triglycerides": "triglycerides",
            "neutrophils": "neutrophils",
            "lymphocytes": "lymphocytes",
            "eosinophils": "eosinophils",
            "monocytes": "monocytes",
            "basophils": "basophils",
            "rbc count": "rbc",
            "mcv": "mcv",
            "mchc": "mchc",
            "mch": "mch",
            "rdw": "rdw",
            "sgot": "sgot_ast",
            "ast": "sgot_ast",
            "sgpt": "sgpt_alt",
            "alt": "sgpt_alt",
            "albumin": "albumin",
            "globulin": "globulin",
            "sodium": "sodium",
            "potassium": "potassium",
            "tsh": "tsh",
            "free t3": "free_t3",
            "t3": "free_t3",
            "free t4": "free_t4",
            "t4": "free_t4",
            "pulse": "pulse",
            "spo2": "spo2",
            "temperature": "temperature",
        }

    def _match_lab_alias(self, line: str) -> tuple[str | None, str | None]:
        lower = re.sub(r"[^a-z0-9]+", " ", str(line or "").lower()).strip()
        for alias, normalized in sorted(self._known_lab_aliases().items(), key=lambda item: len(item[0]), reverse=True):
            clean_alias = re.sub(r"[^a-z0-9]+", " ", alias.lower()).strip()
            if re.search(rf"(?<![a-z0-9]){re.escape(clean_alias)}(?![a-z0-9])", lower):
                return alias, normalized
        return None, None

    def _parse_known_lab_line(self, line: str) -> dict | None:
        lower = line.lower()
        matched_alias, test_name = self._match_lab_alias(line)
        if not matched_alias or not test_name:
            return None

        after_name = line[lower.find(matched_alias) + len(matched_alias) :]
        value_match = re.search(r"(\d+(?:\.\d+)?)", after_name)
        if not value_match:
            return None
        value = float(value_match.group(1))

        unit_match = re.search(
            r"(mg/dL|g/dL|mmol/L|%|uIU/mL|mIU/L|pg/mL|ng/dL|x10\^?3/uL|cells/uL|lakhs/cumm|mill/cumm|/cumm|cumm|fL|pg|IU/L|U/L|/min|bpm)",
            after_name,
            flags=re.IGNORECASE,
        )
        range_match = re.search(r"(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)", after_name)
        flag_match = re.search(r"\b(low|high|borderline|[HL])\b", after_name, flags=re.IGNORECASE)
        return {
            "test_name": test_name,
            "test_value": value,
            "unit": unit_match.group(1) if unit_match else None,
            "reference_range_low": float(range_match.group(1)) if range_match else None,
            "reference_range_high": float(range_match.group(2)) if range_match else None,
            "flag": flag_match.group(1).upper() if flag_match else None,
            "extraction_method": "deterministic_fallback",
            "raw_segment": line,
        }

    def _parse_vertical_lab_row(self, lines: list[str], index: int) -> dict | None:
        alias, test_name = self._match_lab_alias(lines[index])
        if not alias or not test_name:
            return None

        value = None
        value_index = None
        for lookahead in range(index + 1, min(index + 5, len(lines))):
            candidate = lines[lookahead].strip()
            if self._match_lab_alias(candidate)[0]:
                break
            value_match = re.fullmatch(r"[<>]?\s*(\d+(?:\.\d+)?)", candidate)
            if value_match:
                value = float(value_match.group(1))
                value_index = lookahead
                break
        if value is None or value_index is None:
            return None

        unit = None
        reference_low = None
        reference_high = None
        flag = None
        raw_parts = [lines[index], lines[value_index]]
        for lookahead in range(value_index + 1, min(value_index + 5, len(lines))):
            candidate = lines[lookahead].strip()
            if self._match_lab_alias(candidate)[0]:
                break
            raw_parts.append(candidate)
            if re.fullmatch(r"[HL]", candidate, flags=re.IGNORECASE):
                flag = candidate.upper()
                continue
            if unit is None and re.search(
                r"(mg/dl|g/dl|/cumm|cumm|lakhs/cumm|mill/cumm|ml/min|u/l|iu/l|fl|pg|%|ratio|ng/dl|uiu/ml)",
                candidate,
                flags=re.IGNORECASE,
            ):
                unit = candidate
                continue
            range_low, range_high = self._reference_from_text(candidate)
            if range_low is not None or range_high is not None:
                reference_low = range_low
                reference_high = range_high

        return {
            "test_name": test_name,
            "test_value": value,
            "unit": unit,
            "reference_range_low": reference_low,
            "reference_range_high": reference_high,
            "flag": flag,
            "extraction_method": "deterministic_vertical_table",
            "raw_segment": " | ".join(raw_parts),
        }

    def _reference_from_text(self, text: str) -> tuple[float | None, float | None]:
        range_match = re.search(r"(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)", str(text or ""))
        if range_match:
            return float(range_match.group(1)), float(range_match.group(2))
        less_than = re.search(r"<\s*(\d+(?:\.\d+)?)", str(text or ""))
        if less_than:
            return None, float(less_than.group(1))
        greater_than = re.search(r">\s*(\d+(?:\.\d+)?)", str(text or ""))
        if greater_than:
            return float(greater_than.group(1)), None
        return None, None
    def _deterministic_events(self, raw_text: str) -> dict:
        text = str(raw_text or "").strip()
        if not text:
            return {"type": "general_note", "events": []}
        return {
            "type": "general_note",
            "events": [
                {
                    "event_type": "other",
                    "description": text[:500],
                    "time_of_day": None,
                    "extraction_method": "deterministic_fallback",
                }
            ],
        }

    def _candidate_lines(self, raw_text: str) -> list[str]:
        lines = []
        for line in str(raw_text or "").replace("\r", "\n").split("\n"):
            clean = re.sub(r"\s+", " ", line).strip()
            if clean:
                lines.append(clean)
        if len(lines) <= 1:
            lines = [part.strip() for part in re.split(r"[;|]", str(raw_text or "")) if part.strip()]
        return lines

    def _normalize_frequency(self, value: str | None) -> str | None:
        if not value:
            return None
        text = str(value).upper()
        return {
            "1-0-0": "OD",
            "0-0-1": "HS",
            "1-0-1": "BD",
            "1-1-1": "TDS",
        }.get(text, text)

    def _extract_instructions(self, line: str) -> str | None:
        lower = line.lower()
        parts = []
        for phrase in ("after food", "before food", "morning", "night", "sos", "empty stomach", "with food"):
            if phrase in lower:
                parts.append(phrase)
        return ", ".join(parts) if parts else None

    def _extract_advice(self, line: str) -> str | None:
        lower = line.lower()
        parts = []
        for phrase in (
            "shake well",
            "complete course",
            "avoid alcohol",
            "do not drive",
            "apply locally",
            "gargle",
            "steam inhalation",
            "dilute before use",
            "rinse mouth",
            "keep refrigerated",
        ):
            if phrase in lower:
                parts.append(phrase)
        return ", ".join(parts) if parts else None

    def _strip_advice_from_drug_name(self, value: str) -> str:
        text = str(value or "")
        for phrase in (
            "shake well",
            "complete course",
            "avoid alcohol",
            "do not drive",
            "apply locally",
            "gargle",
            "steam inhalation",
            "dilute before use",
            "rinse mouth",
            "keep refrigerated",
        ):
            text = re.sub(rf"\b{re.escape(phrase)}\b", " ", text, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", text).strip()

    def _normalize_test_name(self, name: str) -> str:
        text = re.sub(r"[^a-zA-Z0-9% ]+", " ", str(name or "").lower())
        text = re.sub(r"\s+", " ", text).strip()
        aliases = {
            "hb": "hemoglobin",
            "haemoglobin": "hemoglobin",
            "hemoglobin": "hemoglobin",
            "hba1c": "hba1c",
            "hb a1c": "hba1c",
            "triiodothyronine": "free_t3",
            "triiodothyronine t3": "free_t3",
            "free t3": "free_t3",
            "t3": "free_t3",
            "thyroxine": "free_t4",
            "thyroxine t4": "free_t4",
            "free t4": "free_t4",
            "t4": "free_t4",
            "thyroid stimulating hormone": "tsh",
            "creatinine": "creatinine",
            "serum creatinine": "creatinine",
            "glucose": "glucose",
            "blood glucose": "glucose",
            "blood sugar": "glucose",
            "tsh": "tsh",
            "cholesterol": "cholesterol",
        }
        return aliases.get(text, text.replace(" ", "_"))

    def _dedupe_items(self, items: list[dict], key_name: str) -> list[dict]:
        seen = set()
        deduped = []
        for item in items:
            key = (str(item.get(key_name) or "").lower().strip(), str(item.get("dose_amount") or item.get("test_value") or ""))
            if not key[0] or key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _extraction_is_empty(self, data: dict, doc_type: str) -> bool:
        if not isinstance(data, dict) or not data:
            return True
        if doc_type == "prescription":
            return not self._items(data.get("medications"))
        if doc_type == "lab_report":
            return not self._items(data.get("lab_values") or data.get("tests"))
        if doc_type in {"voice_note", "general_note"}:
            return not self._items(data.get("events"))
        return False

    def _create_media_upload(
        self,
        patient_id: str,
        profile_id: str | None,
        pending_task_id: str | None,
        file_path: str,
        media_hint: str,
        extract_res: dict,
        cleaned: dict,
        doc_type: str,
        doc_type_res: dict,
        errors: list,
        needs_review: bool,
        file_hash: str,
    ) -> str | None:
        structured = {
            "file_hash": file_hash,
            "document_type": doc_type,
            "classification": doc_type_res,
            "extracted_data": cleaned,
            "validation_errors": errors,
            "needs_review": needs_review,
            "parser_method": extract_res.get("method"),
        }
        final_status = "suspicious" if needs_review or errors else "active"
        return db.insert_media_upload(
            patient_id=patient_id,
            profile_id=profile_id,
            pending_task_id=pending_task_id,
            file_path=file_path,
            file_type=self._file_type(media_hint),
            parser_type=self._parser_type(media_hint, extract_res),
            raw_text=str(extract_res.get("text") or ""),
            structured_json=structured,
            parser_confidence=self._parser_confidence(extract_res),
            final_status=final_status,
        )

    def _insert_to_db(
        self,
        data: dict,
        doc_type: str,
        patient_id: str,
        file_hash: str,
        media_upload_id: str | None = None,
        raw_text: str | None = None,
    ) -> dict:
        count = 0
        result = {
            "count": 0,
            "medication_ids": [],
            "active_medication_ids": [],
            "held_medication_ids": [],
            "medication_candidate_results": [],
            "errors": [],
            "lab_ids": [],
            "care_event_count": 0,
            "pharma_agent_triggered": False,
        }
        try:
            if doc_type == "prescription":
                from ingestion import process_medication_candidate_after_llm

                structured_context = dict(data or {})
                structured_context["_source_raw_text"] = raw_text or ""
                for med in self._items(data.get("medications")):
                    candidate = process_medication_candidate_after_llm(
                        patient_id=patient_id,
                        profile_id=None,
                        med=med,
                        structured_json=structured_context,
                        media_upload_id=media_upload_id,
                        from_phone=None,
                        source_type="ocr_extraction",
                    )
                    result["medication_candidate_results"].append(self._json_safe(candidate))
                    med_id = candidate.get("medication_id")
                    if med_id:
                        count += 1
                        result["medication_ids"].append(str(med_id))
                        med["medication_id"] = str(med_id)
                        med["activation_status"] = candidate.get("status")
                        if candidate.get("active"):
                            result["active_medication_ids"].append(str(med_id))
                        else:
                            result["held_medication_ids"].append(str(med_id))
                        if candidate.get("pharma_agent"):
                            result["pharma_agent_triggered"] = True
                            med["pharma_agent_triggered"] = True
                        self._compact_medication_candidate_in_place(med)

            elif doc_type == "lab_report":
                for test in self._items(data.get("lab_values") or data.get("tests")):
                    if not self._valid_lab_for_insert(test):
                        test["_db_status"] = "rejected_invalid_lab"
                        continue
                    lab_id = db.insert_lab_report(
                        patient_id=patient_id,
                        test_name=test.get("test_name"),
                        test_value=test.get("test_value"),
                        unit=test.get("unit"),
                        reference_low=test.get("reference_range_low"),
                        reference_high=test.get("reference_range_high"),
                        confidence=0.85,
                        status="active",
                        media_upload_id=media_upload_id,
                    )
                    if lab_id:
                        count += 1
                        result["lab_ids"].append(str(lab_id))
                        test["lab_report_id"] = str(lab_id)

            elif doc_type == "voice_note":
                for event in self._items(data.get("events")):
                    db.insert_caregiver_event(
                        patient_id=patient_id,
                        event_type=event.get("event_type") or "other",
                        details=event.get("details") or event.get("description") or "",
                        source_type="voice_transcript",
                        file_hash=file_hash,
                    )
                    count += 1
                    result["care_event_count"] += 1

            elif doc_type == "advice_note":
                db.insert_care_note(
                    patient_id=patient_id,
                    note_type="advice",
                    content=json.dumps(data, ensure_ascii=True),
                    source_type="ocr_extraction",
                    file_hash=file_hash,
                )
                count = 1

            elif doc_type == "discharge_summary":
                db.insert_care_note(
                    patient_id=patient_id,
                    note_type="discharge",
                    content=json.dumps(data, ensure_ascii=True),
                    source_type="ocr_extraction",
                    file_hash=file_hash,
                )
                count = 1

            elif doc_type == "referral_letter":
                db.insert_referral(
                    patient_id=patient_id,
                    specialist=data.get("specialist_name"),
                    reason=data.get("reason_for_referral"),
                    urgency=data.get("urgency"),
                    source_type="ocr_extraction",
                    file_hash=file_hash,
                )
                count = 1

            elif doc_type == "medical_history":
                db.insert_care_note(
                    patient_id=patient_id,
                    note_type="medical_history",
                    content=json.dumps(data, ensure_ascii=True),
                    source_type="ocr_extraction",
                    file_hash=file_hash,
                )
                count = 1

            elif doc_type == "general_note":
                db.insert_care_note(
                    patient_id=patient_id,
                    note_type="general",
                    content=json.dumps(data, ensure_ascii=True),
                    source_type="ocr_extraction",
                    file_hash=file_hash,
                )
                count = 1
        except Exception as error:
            print(f"DB insert failed: {error}")
            result["errors"].append(str(error))
        result["count"] = count
        return result

    def _build_caregiver_summary(self, doc_type: str, data: dict, errors: list, needs_review: bool) -> str:
        review_text = " Review flagged." if needs_review else " Verified."
        if doc_type == "prescription":
            return f"{len(self._items(data.get('medications')))} medication candidate(s) processed. Active only after safety clearance.{review_text}"
        if doc_type == "lab_report":
            return f"{len(self._items(data.get('lab_values') or data.get('tests')))} lab test(s) recorded.{review_text}"
        if doc_type in {"discharge_summary", "referral_letter", "medical_history", "general_note", "advice_note"}:
            return f"{doc_type.replace('_', ' ').title()} saved successfully.{review_text if needs_review else ''}"
        if doc_type == "voice_note":
            return f"{len(self._items(data.get('events')))} caregiver event(s) saved.{review_text}"
        return "Document processed successfully."

    def _file_type(self, media_hint: str) -> str:
        media_hint = str(media_hint or "").lower()
        if media_hint == "pdf":
            return "pdf"
        if media_hint == "audio":
            return "audio"
        return "image"

    def _parser_type(self, media_hint: str, extract_res: dict) -> str:
        """
        Normalize parser names to the media_uploads CHECK constraint.
        """
        hint = str(media_hint or "").lower()
        method = str((extract_res or {}).get("method") or "").lower()
        if hint == "audio" or method == "asr":
            return "whisper"
        if hint == "pdf" and method in {"structured", "pymupdf", "pdfplumber", "pypdf"}:
            return "pdfplumber"
        return "paddleocr"

    def _parser_confidence(self, extract_res: dict) -> float:
        method = str((extract_res or {}).get("method") or "").lower()
        if method in {"structured", "pymupdf", "pdfplumber", "pypdf"}:
            return 0.90
        if method in {"ocr", "asr"}:
            return 0.85
        return 0.0

    def _valid_lab_for_insert(self, test: dict) -> bool:
        name = str((test or {}).get("test_name") or "").strip().lower()
        value = test.get("test_value")
        if not name or value is None:
            return False
        try:
            numeric = float(value)
        except Exception:
            return False

        unit = str((test or {}).get("unit") or "").lower()
        if name == "wbc" and "/cumm" in unit:
            return 1000 <= numeric <= 50000
        if name == "platelets" and "lakh" in unit:
            return 0.5 <= numeric <= 10
        if name == "platelets" and "cumm" in unit:
            return 50000 <= numeric <= 1000000

        limits = None
        for key, candidate in config.LAB_PHYSICAL_LIMITS.items():
            if key.lower() in name:
                limits = candidate
                break
        if limits and (numeric < limits[0] or numeric > limits[1]):
            return False
        return self._is_known_lab_name(name)

    def _is_known_lab_name(self, name: str) -> bool:
        known = {
            "creatinine",
            "glucose",
            "hba1c",
            "free_t3",
            "free_t4",
            "hemoglobin",
            "sodium",
            "potassium",
            "wbc",
            "platelets",
            "tsh",
            "cholesterol",
            "pulse",
            "spo2",
            "temperature",
            "blood_pressure_systolic",
            "blood_pressure_diastolic",
            "estimated_average_glucose",
            "wbc",
            "neutrophils",
            "lymphocytes",
            "eosinophils",
            "monocytes",
            "basophils",
            "rbc",
            "hematocrit",
            "mcv",
            "mch",
            "mchc",
            "rdw",
            "hdl_cholesterol",
            "ldl_cholesterol",
            "vldl_cholesterol",
            "triglycerides",
            "cholesterol_hdl_ratio",
            "blood_urea",
            "egfr",
            "bun_creatinine_ratio",
            "bilirubin",
            "sgot_ast",
            "sgpt_alt",
            "alkaline_phosphatase",
            "total_protein",
            "albumin",
            "globulin",
            "albumin_globulin_ratio",
        }
        return any(key in name for key in known)

    def _trigger_pharma_agent(self, patient_id: str, med: dict) -> bool:
        try:
            drug_name = med.get("drug_name_raw") or med.get("drug_name")
            if not patient_id or not drug_name:
                return False
            task_id = db.enqueue_pharma_medication_check(
                patient_id=patient_id,
                medication_id=med.get("medication_id"),
                drug_name=str(drug_name),
                dose_amount=med.get("dose_amount"),
                prescribed_by=med.get("prescribed_by"),
                from_phone=None,
                source_type="document_pipeline",
            )
            med["pharma_agent_task_id"] = task_id
            return bool(task_id)
        except Exception as error:
            print(f"PharmaAgent document trigger failed: {error}")
            return False

    def _update_media_upload_result(
        self,
        media_upload_id: str,
        data: dict,
        doc_type: str,
        insert_result: dict,
        file_hash: str,
        errors: list | None = None,
        needs_review: bool | None = None,
    ) -> None:
        try:
            extracted_data = {
                key: value
                for key, value in dict(data or {}).items()
                if not str(key).startswith("_source_")
            }
            extracted_data = self._json_safe(extracted_data)
            structured = {
                "file_hash": file_hash,
                "document_type": doc_type,
                "extracted_data": extracted_data,
                "insert_result": self._compact_insert_result(insert_result),
                "validation_errors": self._json_safe(errors or []),
                "needs_review": bool(needs_review),
            }
            # Keep the canonical arrays available at the top level for existing
            # UI/reporting code while preserving the full audit envelope above.
            structured.update(extracted_data)
            db.update_media_upload_structured(media_upload_id, structured)
        except Exception as error:
            print(f"Media upload structured update failed: {error}")

    def _compact_insert_result(self, insert_result: dict | None) -> dict:
        result = dict(insert_result or {})
        compact_candidates = []
        for item in self._items(result.get("medication_candidate_results")):
            validation = item.get("validation") if isinstance(item.get("validation"), dict) else {}
            pharma = item.get("pharma_agent") if isinstance(item.get("pharma_agent"), dict) else {}
            compact_candidates.append(
                self._json_safe(
                    {
                        "medication_id": item.get("medication_id"),
                        "status": item.get("status"),
                        "active": item.get("active"),
                        "validation_confidence": validation.get("validation_confidence"),
                        "unresolved_fields": validation.get("unresolved_fields") or [],
                        "start_date": validation.get("start_date"),
                        "scheduled_times": validation.get("scheduled_times") or [],
                        "prescriber_specialty": validation.get("prescriber_specialty"),
                        "max_severity": pharma.get("max_severity"),
                        "interactions_count": pharma.get("interactions_count"),
                        "approval_hashes": item.get("approval_hashes") or [],
                        "alert_ids": item.get("alert_ids") or [],
                    }
                )
            )
        if compact_candidates:
            result["medication_candidate_results"] = compact_candidates
        return self._json_safe(result)

    def _compact_medication_candidate_in_place(self, med: dict) -> None:
        validation = med.get("deterministic_validation") if isinstance(med.get("deterministic_validation"), dict) else {}
        pharma = med.get("pharma_agent_result") if isinstance(med.get("pharma_agent_result"), dict) else {}
        if validation:
            med["deterministic_validation"] = {
                "canonical_drug_name": validation.get("canonical_drug_name"),
                "canonical_dose_amount": validation.get("canonical_dose_amount"),
                "canonical_dose_unit": validation.get("canonical_dose_unit"),
                "canonical_frequency": validation.get("canonical_frequency"),
                "validation_confidence": validation.get("validation_confidence"),
                "unresolved_fields": validation.get("unresolved_fields") or [],
                "final_validation_status": validation.get("final_validation_status"),
                "start_date": validation.get("start_date"),
                "scheduled_times": validation.get("scheduled_times") or [],
                "prescriber_specialty": validation.get("prescriber_specialty"),
            }
        if pharma:
            med["pharma_agent_result"] = {
                "status": pharma.get("status"),
                "max_severity": pharma.get("max_severity"),
                "interactions_count": pharma.get("interactions_count"),
            }

    def _compact_pharma_result(self, result: dict | None) -> dict:
        if not isinstance(result, dict):
            return {}
        evaluation = result.get("evaluation") if isinstance(result.get("evaluation"), dict) else {}
        return self._json_safe(
            {
                "status": result.get("status"),
                "max_severity": result.get("max_severity"),
                "interactions_count": result.get("interactions_count"),
                "alerts_created": result.get("alerts_created") or [],
                "approvals_created": result.get("approvals_created") or [],
                "normalized_new_drug": evaluation.get("normalized_new_drug"),
                "active_meds_checked": evaluation.get("active_meds_checked") or [],
            }
        )

    def _json_safe(self, value):
        if isinstance(value, dict):
            return {str(key): self._json_safe(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._json_safe(item) for item in value]
        if isinstance(value, tuple):
            return [self._json_safe(item) for item in value]
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    def _notify_processing_result(
        self,
        patient_id: str,
        profile_id: str,
        doc_type: str,
        summary_msg: str,
        cleaned: dict,
        file_hash: str,
    ) -> None:
        try:
            db.write_audit(
                patient_id=patient_id,
                profile_id=profile_id,
                entity_type="document_pipeline",
                entity_id=None,
                action="DOCUMENT_PROCESSED",
                actor_role="system",
                new_value={
                    "document_type": doc_type,
                    "summary": summary_msg,
                    "file_hash": file_hash,
                },
            )

            if doc_type == "referral_letter" and str(cleaned.get("urgency") or "").lower() == "emergency":
                patient_name = db.get_patient_name(patient_id) or "Patient"
                send_caregiver_notifications(
                    patient_id=patient_id,
                    patient_name=patient_name,
                    force=True,
                    trigger_message=summary_msg,
                )
        except Exception as error:
            print(f"Notification logging failed: {error}")

    def _normalize_for_doc_type(self, data: dict, doc_type: str) -> dict:
        data = dict(data or {})
        if doc_type == "lab_report" and data.get("tests") and not data.get("lab_values"):
            data["lab_values"] = data.get("tests")
        if doc_type == "prescription":
            for med in self._items(data.get("medications")):
                if med.get("drug_name") and not med.get("drug_name_raw"):
                    med["drug_name_raw"] = med.get("drug_name")
        return data

    def _extract_document_date(self, raw_text: str) -> str | None:
        try:
            from ingestion import extract_prescription_start_date

            return extract_prescription_start_date({}, {"_source_raw_text": raw_text})
        except Exception:
            return None

    def _extract_doctor_name(self, raw_text: str) -> str | None:
        try:
            match = re.search(r"\bDr\.?\s+([A-Za-z][A-Za-z .-]{2,50})", raw_text or "", flags=re.IGNORECASE)
            if match:
                return "Dr. " + re.sub(r"\s+", " ", match.group(1)).strip()
        except Exception:
            pass
        return None

    def _review_reason(self, errors: list, needs_review: bool, confidence: float, truncated: bool) -> str | None:
        if errors:
            return "validation_errors"
        if confidence < config.DOC_TYPE_CONFIDENCE_THRESHOLD:
            return "low_confidence"
        if truncated:
            return "context_truncated"
        if needs_review:
            return "needs_review"
        return None

    def _result(self, start: float, **kwargs) -> dict:
        result = {
            "status": kwargs.pop("status", "error"),
            "document_type": kwargs.pop("document_type", "unknown"),
            "extracted_data": kwargs.pop("extracted_data", {}),
            "validation_errors": kwargs.pop("validation_errors", []),
            "needs_review": kwargs.pop("needs_review", True),
            "review_reason": kwargs.pop("review_reason", None),
            "processing_time_ms": int((time.time() - start) * 1000),
        }
        result.update(kwargs)
        return result

    def _items(self, value: Any) -> list[dict]:
        if value is None:
            return []
        if isinstance(value, dict):
            return [value]
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        return []
