import difflib
import re
from datetime import date, datetime, time, timedelta
from typing import Any


DEBUG_NLP = False
MIN_CONFIDENCE = 0.75
SAFE_CLARIFICATION = "I'm not sure. Can you rephrase?"


NOW_KEYWORDS = ["now", "abhi", "right now", "iss waqt", "just now", "currently", "turant"]
HINDI_NUMBERS = {
    "ek": 1,
    "do": 2,
    "teen": 3,
    "chaar": 4,
    "char": 4,
    "paanch": 5,
    "panch": 5,
    "che": 6,
    "chhe": 6,
    "saat": 7,
    "aath": 8,
    "nau": 9,
    "das": 10,
}
BUCKET_RANGES = {
    "morning": (6, 11),
    "afternoon": (12, 17),
    "evening": (18, 21),
    "night": (22, 5),
}
FREQUENCY_TIMES = {
    "OD": ["08:00"],
    "ONCE DAILY": ["08:00"],
    "BD": ["08:00", "20:00"],
    "BID": ["08:00", "20:00"],
    "TDS": ["08:00", "14:00", "20:00"],
    "TID": ["08:00", "14:00", "20:00"],
    "1-0-1": ["08:00", "20:00"],
    "0-1-0": ["14:00"],
    "1-1-1": ["08:00", "14:00", "20:00"],
    "SOS": [],
}
DRUG_SYNONYMS = {
    "glucophage": "metformin",
    "norvasc": "amlodipine",
    "cardace": "ramipril",
    "glycomet": "metformin",
    "ecosprin": "aspirin",
    "lipitor": "atorvastatin",
    "crestor": "rosuvastatin",
    "lantus": "insulin",
    "humalog": "insulin",
    "telma": "telmisartan",
}
FORMULARY_ALIASES = {
    "metformin hydrochloride": "metformin",
    "metformin hcl": "metformin",
    "amlodipine besylate": "amlodipine",
    "ramipril capsule": "ramipril",
    "atorvastatin calcium": "atorvastatin",
    "acetylsalicylic acid": "aspirin",
}
VOICE_FILLER_WORDS = [
    "um",
    "umm",
    "uh",
    "haan ji",
    "haan haan",
    "matlab",
    "actually",
    "please note",
]


def _safe_now(current_time: datetime | None = None) -> datetime:
    return current_time if current_time is not None else datetime.now()


def _clean_text(message: str) -> str:
    lowered = str(message or "").lower()
    for filler in VOICE_FILLER_WORDS:
        lowered = re.sub(rf"\b{re.escape(filler)}\b", " ", lowered)
    lowered = re.sub(r"\b(\w+)(\s+\1\b)+", r"\1", lowered)
    return re.sub(r"[^\w\s:/.-]", " ", lowered).strip()


def _contains_any(text: str, keywords: list[str]) -> bool:
    return any(keyword.lower() in text for keyword in keywords)


def _bounded_confidence(value: float) -> float:
    return max(0.0, min(1.0, round(value, 2)))


def _safe_confidence(*values: float, default: float = 0.0) -> float:
    usable = [float(value) for value in values if value is not None]
    if not usable:
        return default
    return _bounded_confidence(sum(usable) / len(usable))


def _soundex(text: str) -> str:
    cleaned = re.sub(r"[^a-z]", "", text.lower())
    if not cleaned:
        return ""
    codes = {
        **dict.fromkeys("bfpv", "1"),
        **dict.fromkeys("cgjkqsxz", "2"),
        **dict.fromkeys("dt", "3"),
        "l": "4",
        **dict.fromkeys("mn", "5"),
        "r": "6",
    }
    first = cleaned[0].upper()
    encoded = []
    previous = codes.get(cleaned[0], "")
    for char in cleaned[1:]:
        code = codes.get(char, "")
        if code and code != previous:
            encoded.append(code)
        previous = code
    return (first + "".join(encoded) + "000")[:4]


def _bucket_for_hour(hour: int) -> str:
    if 6 <= hour <= 11:
        return "morning"
    if 12 <= hour <= 17:
        return "afternoon"
    if 18 <= hour <= 21:
        return "evening"
    return "night"


def _hour_in_bucket(hour: int, bucket: str) -> bool:
    if bucket not in BUCKET_RANGES:
        return False
    start, end = BUCKET_RANGES[bucket]
    if start <= end:
        return start <= hour <= end
    return hour >= start or hour <= end


def _parse_int_token(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    return HINDI_NUMBERS.get(token)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _time_from_hhmm(value: str, target: date) -> datetime | None:
    try:
        hour, minute = value.split(":", 1)
        return datetime.combine(target, time(int(hour), int(minute)))
    except Exception:
        return None


def _scheduled_times_for_med(med: dict) -> list[str]:
    try:
        explicit = med.get("scheduled_times") or []
        if isinstance(explicit, list) and explicit:
            return [str(item) for item in explicit if isinstance(item, str)]

        frequency = str(med.get("frequency", "")).upper().strip()
        return FREQUENCY_TIMES.get(frequency, [])
    except Exception:
        return []


def _truncate_words(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    clipped = text[: max(0, max_chars - 1)].rsplit(" ", 1)[0].rstrip()
    return f"{clipped}..." if clipped else text[:max_chars]


def _temporal_default(now: datetime, confidence: float = 0.5) -> dict:
    return {
        "time_bucket": "today" if confidence == 0.5 else _bucket_for_hour(now.hour),
        "is_now": False,
        "confidence": confidence,
        "target_date": now.date().isoformat(),
    }


def _parse_hour_offset(text: str, now: datetime) -> dict | None:
    pattern = r"(\d+|ek|do|teen|chaar|char|paanch|panch|che|chhe|saat|aath|nau|das)\s*(ghante|hours?)\s*(baad|later)"
    match = re.search(pattern, text)
    if not match:
        return None
    hours = _parse_int_token(match.group(1))
    if hours is None:
        return None
    shifted = now + timedelta(hours=hours)
    return {
        "time_bucket": _bucket_for_hour(shifted.hour),
        "is_now": False,
        "confidence": 0.85,
        "target_date": shifted.date().isoformat(),
    }


def _target_date_from_text(text: str, now: datetime) -> date:
    if "yesterday" in text:
        return now.date() - timedelta(days=1)
    if "parso" in text:
        return now.date() + timedelta(days=2)
    if "kal" in text or "tomorrow" in text:
        return now.date() + timedelta(days=1)
    return now.date()


def _bucket_from_text(text: str) -> str | None:
    bucket_keywords = [
        ("morning", ["subah", "savere", "breakfast", "nashta"]),
        ("afternoon", ["dopahar", "lunch", "dopahar ke baad", "2 baje", "do baje"]),
        ("evening", ["shaam", "shaam ko", "6 baje", "7 baje", "chey baje"]),
        ("night", ["raat", "raat ko", "sone se pehle", "bedtime", "10 baje"]),
    ]
    for bucket, keywords in bucket_keywords:
        if _contains_any(text, keywords):
            return bucket
    return None


def parse_temporal_query(message: str, current_time: datetime | None = None) -> dict:
    """
    Extract time intent from natural language.
    Returns {"time_bucket", "is_now", "confidence", "target_date"}.
    """
    try:
        now = _safe_now(current_time)
        text = _clean_text(message)
        target = now.date()

        if not text:
            return _temporal_default(now)

        if _contains_any(text, NOW_KEYWORDS):
            return {
                "time_bucket": _bucket_for_hour(now.hour),
                "is_now": True,
                "confidence": 0.95,
                "target_date": target.isoformat(),
            }

        offset = _parse_hour_offset(text, now)
        if offset is not None:
            return offset

        target = _target_date_from_text(text, now)
        bucket = _bucket_from_text(text)
        if bucket is not None:
            return {"time_bucket": bucket, "is_now": False, "confidence": 0.85, "target_date": target.isoformat()}

        return {
            "time_bucket": _bucket_for_hour(now.hour),
            "is_now": False,
            "confidence": 0.6,
            "target_date": target.isoformat(),
        }
    except Exception:
        return _temporal_default(_safe_now(current_time))


def filter_medications_by_time(meds_list: list[dict], time_context: dict, current_time: datetime) -> list[dict]:
    """Filter medications to those matching the provided time context."""
    try:
        if not meds_list:
            return []

        matched: list[dict] = []
        bucket = str(time_context.get("time_bucket", "today"))
        is_now = bool(time_context.get("is_now"))
        context_confidence = float(time_context.get("confidence", 0.5) or 0.5)

        for med in meds_list:
            scheduled = _scheduled_times_for_med(med)
            matching_datetimes = []
            explicit_schedule = bool(med.get("scheduled_times"))

            for scheduled_time in scheduled:
                dose_dt = _time_from_hhmm(scheduled_time, current_time.date())
                if dose_dt is None:
                    continue
                if is_now and abs((dose_dt - current_time).total_seconds()) <= 2 * 3600:
                    matching_datetimes.append(dose_dt)
                elif not is_now and _hour_in_bucket(dose_dt.hour, bucket):
                    matching_datetimes.append(dose_dt)

            if not matching_datetimes:
                continue

            future_doses = [dose for dose in matching_datetimes if dose >= current_time]
            next_dose = min(future_doses) if future_doses else None
            enriched = dict(med)
            enriched["scheduled_times"] = scheduled
            enriched["next_dose_time"] = next_dose.strftime("%H:%M") if next_dose else None
            schedule_confidence = 0.95 if explicit_schedule else 0.78
            enriched["confidence"] = _safe_confidence(context_confidence, schedule_confidence)
            enriched["match_reason"] = "within_now_window" if is_now else f"bucket:{bucket}"
            matched.append(enriched)

        return matched
    except Exception:
        return []


def _drug_candidates(query: str) -> tuple[str, list[str]]:
    normalized = _clean_text(query)
    tokens = [re.sub(r"(na|ne|ko)$", "", token) for token in normalized.split()]
    normalized = " ".join(tokens)
    candidates = [normalized, normalized.replace(" ", "")]
    for size in (2, 3):
        for index in range(0, max(len(tokens) - size + 1, 0)):
            phrase = " ".join(tokens[index:index + size])
            candidates.extend([phrase, phrase.replace(" ", "")])
    return normalized, [candidate for candidate in dict.fromkeys(candidates) if candidate]


def _drug_alias_match(candidates: list[str], drug_lookup: dict[str, str]) -> tuple[str | None, float]:
    for drug_lower, original in drug_lookup.items():
        if any(drug_lower == candidate or drug_lower in candidate for candidate in candidates):
            return original, 1.0
    for alias, mapped in {**FORMULARY_ALIASES, **DRUG_SYNONYMS}.items():
        alias_compact = alias.replace(" ", "")
        if any(alias in candidate or alias_compact in candidate for candidate in candidates) and mapped in drug_lookup:
            return drug_lookup[mapped], 0.96 if alias in FORMULARY_ALIASES else 0.95
    return None, 0.0


def _fuzzywuzzy_drug(candidates: list[str], drugs: list[str], threshold: int) -> tuple[str | None, float]:
    try:
        from fuzzywuzzy import fuzz

        best_name = None
        best_score = 0
        for drug in drugs:
            score = max(fuzz.partial_ratio(candidate, drug.lower()) for candidate in candidates)
            if score > best_score:
                best_name = drug
                best_score = score
        confidence = best_score / 100
        return (best_name, confidence) if best_score >= threshold else (None, confidence)
    except ImportError:
        return None, -1.0


def _difflib_drug(candidates: list[str], drugs: list[str], threshold: int) -> tuple[str | None, float]:
    drug_lookup = {drug.lower(): drug for drug in drugs}
    drug_names = list(drug_lookup.keys())
    for candidate in candidates:
        matches = difflib.get_close_matches(candidate, drug_names, n=1, cutoff=MIN_CONFIDENCE)
        if matches:
            confidence = difflib.SequenceMatcher(None, candidate, matches[0]).ratio()
            matched = drug_lookup[matches[0]]
            return (matched, confidence) if confidence >= threshold / 100 else (None, confidence)

    best_name = None
    best_confidence = 0.0
    for candidate in candidates:
        if len(candidate) < 4 or " " in candidate:
            continue
        for drug in drugs:
            if _soundex(candidate) == _soundex(drug):
                confidence = max(0.76, difflib.SequenceMatcher(None, candidate, drug.lower()).ratio() * 0.9)
                if confidence > best_confidence:
                    best_name = drug
                    best_confidence = confidence
    if best_name and best_confidence >= max(MIN_CONFIDENCE, threshold / 100):
        return best_name, _bounded_confidence(best_confidence)
    return None, _bounded_confidence(best_confidence)


def fuzzy_drug_match(query: str, known_drugs: list[str] | None = None, threshold: int = 80) -> tuple[str | None, float]:
    """Match a drug mention to a known medication name with typo tolerance."""
    try:
        drugs = known_drugs or ["metformin", "amlodipine", "ramipril", "aspirin", "atorvastatin", "rosuvastatin", "insulin"]
        _, candidates = _drug_candidates(query)
        alias_name, alias_confidence = _drug_alias_match(candidates, {drug.lower(): drug for drug in drugs})
        if alias_name is not None:
            return alias_name, alias_confidence

        fuzzy_name, fuzzy_confidence = _fuzzywuzzy_drug(candidates, drugs, threshold)
        if fuzzy_confidence >= 0:
            return fuzzy_name, _bounded_confidence(fuzzy_confidence)

        difflib_name, difflib_confidence = _difflib_drug(candidates, drugs, threshold)
        return difflib_name, _bounded_confidence(difflib_confidence)
    except Exception:
        return None, 0.0


def _bp_severity(systolic: float, diastolic: float) -> str:
    if systolic >= 180 or diastolic >= 120:
        return "critical"
    if systolic >= 140 or diastolic >= 90:
        return "advisory"
    return "normal"


def _age_years(patient_context: dict | None) -> int | None:
    if not patient_context:
        return None
    age = patient_context.get("age") or patient_context.get("age_years")
    try:
        return int(age)
    except Exception:
        return None


def _severity_for(test_name: str, value: float, secondary: float | None = None, patient_context: dict | None = None) -> str:
    age = _age_years(patient_context)
    older_adult = age is not None and age >= 65

    if test_name == "bp" and secondary is not None:
        return _bp_severity(value, secondary)
    if test_name == "glucose":
        if value >= 250 or value < 70:
            return "critical"
        if value >= 180:
            return "advisory"
    if test_name == "pulse":
        high_critical = 115 if older_adult else 120
        if value >= high_critical or value < 50:
            return "critical"
        if value >= 100 or value < 60:
            return "advisory"
    if test_name == "spo2":
        chronic = " ".join(str(item).lower() for item in patient_context.get("conditions", [])) if patient_context else ""
        advisory_threshold = 92 if "copd" in chronic else 95
        if value < 90:
            return "critical"
        if value < advisory_threshold:
            return "advisory"
    if test_name == "temperature":
        critical_temp = 38.8 if older_adult else 39.0
        if value >= critical_temp:
            return "critical"
        if value >= 38.0:
            return "advisory"
    return "normal"


def _extract_bp(text: str, patient_context: dict | None = None) -> list[dict]:
    results = []
    for match in re.finditer(r"bp\s*[:\-]?\s*(\d{2,3})\s*/\s*(\d{2,3})", text, re.IGNORECASE):
        systolic = float(match.group(1))
        diastolic = float(match.group(2))
        results.append({
            "test_name": "bp",
            "value": systolic,
            "unit": f"/{int(diastolic)} mmHg",
            "severity": _severity_for("bp", systolic, diastolic, patient_context),
            "confidence": 0.98,
            "raw_text": match.group(0),
        })
    return results


def _extract_glucose(text: str, patient_context: dict | None = None) -> list[dict]:
    results = []
    pattern = r"(sugar|glucose|blood\s*sugar)\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*(mg/dl|mmol)?"
    for match in re.finditer(pattern, text, re.IGNORECASE):
        value = float(match.group(2))
        unit = (match.group(3) or "mg/dL").lower()
        if unit == "mmol":
            value *= 18.0
            unit = "mg/dL"
        results.append({
            "test_name": "glucose",
            "value": round(value, 2),
            "unit": unit,
            "severity": _severity_for("glucose", value, patient_context=patient_context),
            "confidence": 0.95 if match.group(3) else 0.9,
            "raw_text": match.group(0),
        })
    return results


def _extract_simple_vital(text: str, pattern: str, test_name: str, unit: str, patient_context: dict | None = None) -> list[dict]:
    results = []
    for match in re.finditer(pattern, text, re.IGNORECASE):
        value = float(match.group(2))
        results.append({
            "test_name": test_name,
            "value": value,
            "unit": unit,
            "severity": _severity_for(test_name, value, patient_context=patient_context),
            "confidence": 0.95 if unit in match.group(0).lower() or test_name == "spo2" else 0.9,
            "raw_text": match.group(0),
        })
    return results


def _extract_temperature(text: str, patient_context: dict | None = None) -> list[dict]:
    results = []
    pattern = r"(temp|temperature|bukhar)\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*(°?f|°?c|f|c)?"
    for match in re.finditer(pattern, text, re.IGNORECASE):
        value = float(match.group(2))
        unit = (match.group(3) or "c").lower().replace("°", "")
        if unit == "f":
            value = (value - 32) * 5 / 9
        results.append({
            "test_name": "temperature",
            "value": round(value, 2),
            "unit": "C",
            "severity": _severity_for("temperature", value, patient_context=patient_context),
            "confidence": 0.95,
            "raw_text": match.group(0),
        })
    return results


def parse_vital_values(message: str, patient_context: dict | None = None) -> list[dict]:
    """Extract and triage vital signs from text."""
    try:
        text = str(message or "")
        results = _extract_bp(text, patient_context)
        results.extend(_extract_glucose(text, patient_context))
        results.extend(_extract_simple_vital(text, r"(pulse|heart\s*rate)\s*[:\-]?\s*(\d+)\s*(bpm)?", "pulse", "bpm", patient_context))
        results.extend(_extract_simple_vital(text, r"(spo2|oxygen|saturation)\s*[:\-]?\s*(\d+)\s*%?", "spo2", "%", patient_context))
        results.extend(_extract_temperature(text, patient_context))
        return results
    except Exception:
        return []


def parse_caregiver_query_intent(message: str, pending_context: dict | None = None) -> dict | None:
    """Classify caregiver-specific intents with priority and confidence."""
    try:
        text = _clean_text(message)
        context_type = (pending_context or {}).get("type")
        checks = [
            ("approval_action", ["approve", "veto", "confirm", "theek hai", "cancel", "nahi chahiye", "reject"]),
            ("adherence_check", ["did he take", "morning liya", "missed kya", "kab li", "schedule check", "dawai li", "took", "skip"]),
            ("lab_check", ["report aaya", "test results", "hba1c kya hai", "lab pending", "blood test", "test update"]),
            ("status_summary", ["kaisa hai", "aaj ka haal", "health update", "today's status", "what needs attention", "how is", "update do"]),
            ("schedule_update", ["main nahi hoon", "leave", "rahu nahi", "covering", "shift change", "available nahi", "aaj nahi aa"]),
            ("notification_rule", ["notify me if", "alert me when", "miss hone par", "jab tak", "when to alert"]),
        ]

        for intent, keywords in checks:
            if not _contains_any(text, keywords):
                continue
            aligned = intent == "approval_action" and context_type in ["interaction_alert", "new_med"]
            confidence = 0.95 if aligned else 0.85
            if intent == "approval_action" and not aligned:
                confidence = 0.85
            if confidence < 0.75:
                return None
            return {"intent": intent, "target": None, "confidence": confidence}

        return None
    except Exception:
        return None


def _expand_expected_doses(scheduled_meds: list[dict], target_date: date) -> list[dict]:
    doses = []
    for med in scheduled_meds:
        for scheduled_time in _scheduled_times_for_med(med):
            dose_at = _time_from_hhmm(scheduled_time, target_date)
            if dose_at is None:
                continue
            doses.append({
                "medication_id": med.get("id") or med.get("medication_id"),
                "drug_name": med.get("drug_name"),
                "dose_at": dose_at,
                "scheduled_time": scheduled_time,
            })
    return doses


def _prepare_med_logs(entries: list[dict]) -> list[dict]:
    logs = []
    for entry in entries or []:
        reported_at = _parse_datetime(entry.get("reported_at"))
        if reported_at is not None:
            logs.append({**entry, "reported_dt": reported_at})
    return logs


def _find_matching_log(dose: dict, logs: list[dict], matched_log_ids: set[int]) -> tuple[int, dict] | None:
    for index, log in enumerate(logs):
        if index in matched_log_ids:
            continue
        if log.get("medication_id") != dose["medication_id"]:
            continue
        delta = abs((log["reported_dt"] - dose["dose_at"]).total_seconds())
        if delta <= 3 * 3600:
            return index, log
    return None


def _last_reported_iso(logs: list[dict]) -> str | None:
    last_log = max(logs, key=lambda item: item["reported_dt"], default=None)
    return last_log["reported_dt"].isoformat() if last_log else None


def _next_dose_for_today(expected_doses: list[dict], target_date: date, now: datetime) -> str | None:
    if target_date != now.date():
        return None
    future = [dose["dose_at"] for dose in expected_doses if dose["dose_at"] > now]
    return min(future).strftime("%H:%M") if future else None


def _empty_adherence() -> dict:
    return {
        "total_scheduled": 0,
        "taken": 0,
        "missed": 0,
        "pending": 0,
        "gap_detected": False,
        "last_reported": None,
        "next_dose": None,
        "confidence": 0.0,
    }


def compute_adherence_snapshot(patient_id: str, med_log_entries: list[dict], scheduled_meds: list[dict], target_date: date) -> dict:
    """Calculate adherence stats for a specific date."""
    safe = _empty_adherence()
    try:
        expected_doses = _expand_expected_doses(scheduled_meds or [], target_date)
        if not expected_doses:
            return safe

        logs = _prepare_med_logs(med_log_entries or [])
        matched_log_ids = set()
        taken = 0
        missed = 0
        pending = 0
        gap_detected = False
        now = datetime.now()

        for dose in expected_doses:
            matching_log = _find_matching_log(dose, logs, matched_log_ids)
            if matching_log is None:
                pending += 1
                if now - dose["dose_at"] > timedelta(hours=24):
                    gap_detected = True
                continue

            matched_log_ids.add(matching_log[0])
            event_type = str(matching_log[1].get("event_type", "")).lower()
            if event_type == "missed":
                missed += 1
            else:
                taken += 1

        safe.update({
            "total_scheduled": len(expected_doses),
            "taken": taken,
            "missed": missed,
            "pending": pending,
            "gap_detected": gap_detected,
            "last_reported": _last_reported_iso(logs),
            "next_dose": _next_dose_for_today(expected_doses, target_date, now),
            "confidence": 0.9 if logs else 0.78,
        })
        return safe
    except Exception:
        return safe


def _has_severity(items: list[dict], severity: str) -> bool:
    return any(str(item.get("severity", "")).lower() == severity for item in items or [])


def _alert_has_level(alerts: list[dict], levels: list[str]) -> bool:
    return any(str(alert.get("severity") or alert.get("level") or alert.get("priority", "")).lower() in levels for alert in alerts or [])


def _empty_care_summary() -> dict:
    return {
        "status_color": "green",
        "status_line": "No current care issues are recorded.",
        "critical_items": [],
        "attention_items": [],
        "next_actions": ["Check in again when new information is available."],
        "confidence": 0.5,
    }


def _critical_items(open_alerts: list[dict], recent_vitals: list[dict], adherence: dict) -> list[str]:
    items = []
    for alert in open_alerts or []:
        severity = str(alert.get("severity") or "").lower()
        if severity not in {"critical", "high", "red"}:
            continue
        payload = alert.get("data_payload") if isinstance(alert.get("data_payload"), dict) else {}
        summary = payload.get("plain_language_summary") if isinstance(payload.get("plain_language_summary"), dict) else {}
        if summary.get("risk"):
            items.append(str(summary.get("risk")))
        elif alert.get("type") == "drug_interaction":
            items.append(alert.get("message") or "A medication interaction alert is open.")
        else:
            items.append(alert.get("message") or "A critical alert is open.")
    if adherence.get("gap_detected"):
        items.append("A medication reporting gap was detected.")
    if _has_severity(recent_vitals, "critical"):
        items.append("A recent vital reading is critical.")
    return items[:3]


def _attention_items(open_alerts: list[dict], recent_labs: list[dict], adherence: dict) -> list[str]:
    items = []
    for alert in open_alerts or []:
        severity = str(alert.get("severity") or "").lower()
        if severity not in {"advisory", "medium", "yellow"}:
            continue
        payload = alert.get("data_payload") if isinstance(alert.get("data_payload"), dict) else {}
        summary = payload.get("plain_language_summary") if isinstance(payload.get("plain_language_summary"), dict) else {}
        if summary.get("risk"):
            items.append(str(summary.get("risk")))
        else:
            items.append(alert.get("message") or "An advisory alert needs review.")
    if int(adherence.get("missed", 0) or 0) >= 2:
        items.append("Two or more medication doses were missed.")
    if _has_severity(recent_labs, "advisory") or _has_severity(recent_labs, "critical"):
        items.append("A recent lab result is out of range.")
    return items[:3]


def _next_actions(active_meds: list[dict], open_alerts: list[dict], adherence: dict) -> list[str]:
    actions = []
    for alert in open_alerts or []:
        payload = alert.get("data_payload") if isinstance(alert.get("data_payload"), dict) else {}
        summary = payload.get("plain_language_summary") if isinstance(payload.get("plain_language_summary"), dict) else {}
        if summary.get("what_to_do_now"):
            actions.append(str(summary.get("what_to_do_now")))
    if active_meds:
        actions.append("Review today's medication schedule.")
    if open_alerts:
        actions.append("Review open alerts.")
    if adherence.get("next_dose"):
        actions.append(f"Next dose is at {adherence['next_dose']}.")
    return actions[:3]


def _status_color_line(critical_items: list[str], attention_items: list[str]) -> tuple[str, str]:
    if critical_items:
        return "red", "Urgent care items need attention today."
    if attention_items:
        return "yellow", "Some care items need attention today."
    return "green", "Care status looks stable based on current records."


def _summary_confidence(
    active_meds: list[dict],
    recent_vitals: list[dict],
    recent_labs: list[dict],
    open_alerts: list[dict],
    adherence: dict,
) -> float:
    evidence = 0
    if active_meds:
        evidence += 1
    if recent_vitals:
        evidence += 1
    if recent_labs:
        evidence += 1
    if open_alerts:
        evidence += 1
    if adherence and adherence.get("total_scheduled", 0) > 0:
        evidence += 1

    base = 0.55 + min(evidence, 4) * 0.08
    adherence_confidence = float(adherence.get("confidence", 0.5) or 0.5) if adherence else 0.5
    return _safe_confidence(base, adherence_confidence)


def assemble_care_summary(
    patient_id: str,
    active_meds: list[dict],
    recent_vitals: list[dict],
    recent_labs: list[dict],
    open_alerts: list[dict],
    adherence: dict,
) -> dict:
    """Create a deterministic care status snapshot."""
    try:
        if not any([active_meds, recent_vitals, recent_labs, open_alerts, adherence]):
            return _empty_care_summary()

        critical = _critical_items(open_alerts, recent_vitals, adherence)
        attention = _attention_items(open_alerts, recent_labs, adherence)
        actions = _next_actions(active_meds, open_alerts, adherence)
        color, line = _status_color_line(critical, attention)

        return {
            "status_color": color,
            "status_line": line[:119],
            "critical_items": critical,
            "attention_items": attention,
            "next_actions": actions,
            "confidence": _summary_confidence(active_meds, recent_vitals, recent_labs, open_alerts, adherence),
        }
    except Exception:
        return {
            "status_color": "green",
            "status_line": "I do not have enough information for a care summary right now.",
            "critical_items": [],
            "attention_items": [],
            "next_actions": [],
            "confidence": 0.0,
        }


def _format_list(items: list[Any], max_items: int = 5) -> list[str]:
    lines = []
    for index, item in enumerate(items[:max_items], start=1):
        if isinstance(item, dict):
            label = item.get("drug_name") or item.get("test_name") or item.get("status_line") or str(item)
            detail = item.get("next_dose_time") or item.get("severity") or item.get("unit") or ""
            line = f"{index}. {label} {detail}".strip()
        else:
            line = f"{index}. {item}"
        lines.append(line)
    return lines


def format_care_response(data: dict, query_type: str, max_chars: int = 380) -> str:
    """Format a WhatsApp-safe reply with truncation and fallback."""
    try:
        suffix = "\n\nReply HELP for menu."
        if not data:
            return _truncate_words("I don't have that information right now. Please try again or contact your doctor." + suffix, max_chars)

        confidence = data.get("confidence") if isinstance(data, dict) else None
        if confidence is not None and float(confidence) < MIN_CONFIDENCE:
            return _truncate_words(SAFE_CLARIFICATION + suffix, max_chars)

        lines = []
        if query_type == "summary":
            color_icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(data.get("status_color"), "🟡")
            lines.append(f"{color_icon} {data.get('status_line', 'Care summary is available.')}")
            items = (data.get("critical_items") or []) + (data.get("attention_items") or []) + (data.get("next_actions") or [])
            lines.extend(_format_list(items, 5))
        elif query_type == "meds":
            lines.append("💊 Medications:")
            lines.extend(_format_list(data.get("meds", data if isinstance(data, list) else []), 5))
        elif query_type == "vitals":
            lines.append("⚠️ Recent vitals:")
            lines.extend(_format_list(data.get("vitals", data if isinstance(data, list) else []), 5))
        elif query_type == "adherence":
            lines.append("⏰ Medication adherence:")
            lines.append(f"1. Taken: {data.get('taken', 0)}")
            lines.append(f"2. Missed: {data.get('missed', 0)}")
            lines.append(f"3. Pending: {data.get('pending', 0)}")
        else:
            lines.append(str(data.get("message", "I'm not sure. Can you rephrase?")))

        text = "\n".join(line for line in lines if line).strip()
        return _truncate_words(text + suffix, max_chars)
    except Exception:
        return _truncate_words("I'm not sure. Can you rephrase?\n\nReply HELP for menu.", max_chars)


# ENHANCEMENT IDEA: Phonetic drug matching for Hinglish accents.
# This would help with voice-to-text spellings like "met far min" or "amlodepin".
# import phonetics  # optional dependency
# if phonetics.metaphone(query) == phonetics.metaphone(drug):
#     return drug

# ENHANCEMENT IDEA: RxNorm or local formulary mapping.
# A curated medication dictionary could map brands, salts, and common misspellings
# to normalized generic names before fuzzy matching.

# ENHANCEMENT IDEA: Voice-note text cleaning.
# A deterministic cleanup layer could remove filler words, repeated words, and
# transcription artifacts before running the classifier.

# ENHANCEMENT IDEA: Age-aware vital thresholds.
# Some thresholds may need stricter or looser interpretation depending on age,
# pregnancy status, COPD, kidney disease, or clinician-set rules.
