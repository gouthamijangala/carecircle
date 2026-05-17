"""
Validation helpers for LLM-produced medical extraction JSON.

The ingestion pipeline's canonical schemas are:
- prescription: {"medications": [...]}
- lab_report: {"lab_values": [...]}

This module also accepts legacy prompt aliases such as "drug_name" and "tests"
so older extraction prompts can be cleaned into the canonical shape before DB
write functions consume them.
"""

from __future__ import annotations

import json
import re
from typing import Any

import config


def validate_and_clean(llm_json_str: str, raw_text: str, doc_type: str) -> tuple:
    """
    Contract:
      Input: llm_json_str (str), raw_text (str), doc_type (str)
      Output: tuple (cleaned_dict, errors_list, needs_review_bool)
      Error: Returns empty dict and critical error on parse failure.
    """
    try:
        data = _parse_json_payload(llm_json_str)
    except Exception:
        return {}, [{"field": "json_parse", "severity": "critical", "message": "Invalid JSON"}], True

    errors = []
    lower_raw = (raw_text or "").lower()

    if doc_type == "prescription":
        medications = _as_list(data.get("medications"))
        cleaned_meds = []
        for index, med in enumerate(medications):
            if not isinstance(med, dict):
                errors.append({"field": f"medications[{index}]", "severity": "critical", "message": "Medication item is not an object"})
                continue

            med = dict(med)
            if med.get("drug_name") and not med.get("drug_name_raw"):
                med["drug_name_raw"] = med.get("drug_name")
            if med.get("drug_name_raw") and not med.get("drug_name"):
                med["drug_name"] = med.get("drug_name_raw")

            dose = med.get("dose_amount")
            if dose is not None:
                dose_value = _to_float(dose)
                if dose_value is None:
                    errors.append({"field": "dose_amount", "severity": "critical", "message": "Dose is not numeric"})
                else:
                    med["dose_amount"] = dose_value
                    if dose_value <= 0 or dose_value > 10000:
                        errors.append({"field": "dose_amount", "value": dose_value, "severity": "critical"})
                    if not _number_appears_in_source(dose, raw_text):
                        errors.append({"field": "dose_amount", "severity": "high", "message": "Numeric value not found in source"})

            drug_name = med.get("drug_name_raw") or med.get("drug_name")
            if drug_name and str(drug_name).lower() not in lower_raw:
                errors.append({"field": "drug_name", "value": drug_name, "severity": "high", "message": "Not found in source"})

            cleaned_meds.append(med)

        data["medications"] = cleaned_meds

    elif doc_type == "lab_report":
        labs = _as_list(data.get("lab_values") if data.get("lab_values") is not None else data.get("tests"))
        cleaned_labs = []
        for index, lab in enumerate(labs):
            if not isinstance(lab, dict):
                errors.append({"field": f"lab_values[{index}]", "severity": "critical", "message": "Lab item is not an object"})
                continue

            lab = dict(lab)
            if lab.get("value") is not None and lab.get("test_value") is None:
                lab["test_value"] = lab.get("value")

            test_name = str(lab.get("test_name") or "").strip()
            value = lab.get("test_value")
            if value is not None:
                value_float = _to_float(value)
                if value_float is None:
                    errors.append({"field": "test_value", "severity": "critical", "message": "Test value is not numeric"})
                else:
                    lab["test_value"] = value_float
                    normalized_name = test_name.lower().replace(" ", "_")
                    limits = _limits_for_test(normalized_name)
                    if limits and (value_float < limits[0] or value_float > limits[1]):
                        errors.append({"field": "test_value", "value": value_float, "severity": "critical"})
                    if not _number_appears_in_source(value, raw_text):
                        errors.append({"field": "test_value", "severity": "high", "message": "Numeric value not found in source"})

            cleaned_labs.append(lab)

        data["lab_values"] = cleaned_labs

    needs_review = bool(errors)
    return data, errors, needs_review


def _parse_json_payload(llm_json_str: str) -> dict:
    cleaned = re.sub(r"^```json\s*|\s*```$", "", str(llm_json_str or "").strip(), flags=re.MULTILINE)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("JSON object not found")
    parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("JSON payload is not an object")
    return parsed


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _to_float(value: Any) -> float | None:
    try:
        if isinstance(value, str):
            match = re.search(r"-?\d+(?:\.\d+)?", value)
            if not match:
                return None
            return float(match.group(0))
        return float(value)
    except Exception:
        return None


def _number_appears_in_source(value: Any, raw_text: str) -> bool:
    raw = str(raw_text or "").lower()
    value_text = str(value).strip().lower()
    if value_text and value_text in raw:
        return True

    numeric = _to_float(value)
    if numeric is None:
        return False

    candidates = {
        str(value_text),
        str(numeric),
        str(int(numeric)) if numeric.is_integer() else "",
        f"{numeric:g}",
    }
    return any(candidate and candidate in raw for candidate in candidates)


def _limits_for_test(test_name: str) -> tuple[float, float] | None:
    normalized = (test_name or "").lower()
    for key, limits in config.LAB_PHYSICAL_LIMITS.items():
        if key.lower() in normalized:
            return limits
    return None

