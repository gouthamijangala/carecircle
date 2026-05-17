import re
from datetime import date, datetime

import config
import alerting
import appointment_manager
import crisis
import crisis_runtime
import db
import intent as intent_classifier
import llm
from config import SIDE_EFFECT_HINTS
from ingestion import process_side_effect_lookup
from nlp_deterministic import (
    assemble_care_summary,
    compute_adherence_snapshot,
    filter_medications_by_time,
    format_care_response,
    parse_caregiver_query_intent,
    parse_temporal_query,
    parse_vital_values,
)


SAFE_FALLBACK = "I'm not sure. Can you rephrase? Reply HELP for menu."
APPROVAL_CONTEXT_FALLBACK = (
    "I could not find an active 3-minute approval or veto window. "
    "Please request a new veto code to activate veto mode and complete verification."
)
APPROVAL_CONTEXT_EXPIRED_FALLBACK = (
    "This approval or veto window has expired. "
    "Please request a new veto code to activate veto mode and complete verification."
)
APPROVAL_UNAUTHORIZED_FALLBACK = (
    "Only the primary caregiver can approve, deny, or veto this item."
)
MEDICATION_CONTEXT_FALLBACK = (
    "I understood this as a medicine update, but I could not identify which scheduled medicine it refers to. "
    "Please mention the medicine name or ask what is due now."
)
APPOINTMENT_CONTEXT_FALLBACK = (
    "I understood this as an appointment question. Please ask for the next appointment or share the appointment date."
)
PAIN_NON_EMERGENCY_FALLBACK = "It sounds like pain but not an emergency. Please contact your caregiver."
LLM_EMERGENCY_KEYWORDS = [
    "ambulance",
    "hospital",
    "chest pain",
    "cannot breathe",
    "died",
    "dead",
    "heart attack",
    "liver failure",
    "help me",
    "bachao",
    "jaldi",
    "kill me",
    "rape",
    "suicide",
]

HEALTH_REPORT_INTENTS = {
    "symptom_report",
    "mood_report",
    "sleep_report",
    "diet_report",
    "exercise_report",
}
MILD_HEALTH_MARKERS = {
    "mild",
    "slight",
    "little",
    "minor",
    "halka",
    "thoda",
    "thodi",
    "thoda sa",
}
MODERATE_HEALTH_MARKERS = {
    "fever",
    "bukhar",
    "vomiting",
    "ulti",
    "dizzy",
    "dizziness",
    "chakkar",
    "weak",
    "kamzori",
    "swelling",
    "sojan",
    "not slept",
    "neend nahi",
    "nahi khaya",
    "skipped food",
    "anxious",
    "confused",
    "pareshan",
}
SEVERE_HEALTH_MARKERS = {
    "severe",
    "unbearable",
    "very bad",
    "bahut",
    "bohot",
    "zyada",
    "zyaada",
    "intense",
    "worst",
    "tez",
    "jyada",
}
ACUTE_HEALTH_MARKERS = {
    "now",
    "right now",
    "abhi",
    "currently",
    "suddenly",
    "just now",
    "ho raha",
    "ho rha",
    "iss waqt",
    "this moment",
    "aaj",
}
CHRONIC_HEALTH_MARKERS = {
    "for days",
    "many days",
    "since yesterday",
    "since last week",
    "last week",
    "last month",
    "kal se",
    "hafta",
    "weeks",
    "months",
    "chronic",
    "roz",
    "bar bar",
    "baar baar",
    "again and again",
}
CRITICAL_ACUTE_SYMPTOMS = {
    "chest pain",
    "seene mein dard",
    "heart pain",
    "breathing problem",
    "cannot breathe",
    "saans",
    "breathing",
    "collapse",
    "collapsed",
    "unconscious",
    "behosh",
    "severe pain",
    "bleeding",
    "stroke",
    "paralysis",
}

# FUTURE SAFETY WORDS TO REVIEW FOR LLM OUTPUT SCANNING:
# "saans nahi", "saansein atak gayi", "dam ghut raha hai", "behosh",
# "gir gaya", "jaan ja rahi hai", "turant", "abhi abhi"


def _bounded_confidence(value, default: float = 0.0) -> float:
    try:
        numeric = float(value)
    except Exception:
        numeric = default
    return round(max(0.0, min(1.0, numeric)), 2)


def _confidence_label(confidence: float) -> str:
    if confidence >= 0.9:
        return "high"
    if confidence >= 0.7:
        return "medium"
    if confidence > 0:
        return "low"
    return "none"


def _classifier_confidence(classifier: dict | None, default: float) -> float:
    """Use classifier confidence as the response confidence when available."""
    try:
        if not isinstance(classifier, dict) or classifier.get("confidence") is None:
            return _bounded_confidence(default)
        return _bounded_confidence(classifier.get("confidence"), default)
    except Exception:
        return _bounded_confidence(default)


def _has_any(text: str, patterns: set[str] | list[str]) -> bool:
    return any(pattern in text for pattern in patterns)


def _response(
    reply: str,
    intent: str,
    confidence: float,
    source: str,
    reason: str,
    layer: str = "handler",
    classifier: dict | None = None,
    extra: dict | None = None,
) -> dict:
    bounded = _bounded_confidence(confidence)
    classifier_confidence = _bounded_confidence((classifier or {}).get("confidence"), bounded)
    payload = {
        "reply": str(reply or SAFE_FALLBACK),
        "intent": str(intent or "unknown"),
        "confidence": bounded,
        "confidence_score": bounded,
        "confidence_label": _confidence_label(bounded),
        "confidence_reason": reason,
        "classifier_confidence": classifier_confidence,
        "classifier_reason": (classifier or {}).get("reason", reason),
        "normalized_text": (classifier or {}).get("normalized_text", ""),
        "source": str(source or "fallback"),
        "layer": layer,
    }
    if isinstance(extra, dict):
        payload.update(extra)
    return payload


def _merge_compound_replies(primary_reply: str, secondary_results: list[dict]) -> str:
    try:
        if not secondary_results:
            return primary_reply
        parts = [str(primary_reply or "").strip()]
        for result in secondary_results:
            intent_name = result.get("intent", "update")
            reply = str(result.get("reply") or "").strip()
            if reply:
                parts.append(f"Also handled {intent_name}: {reply}")
        return "\n\n".join(part for part in parts if part)
    except Exception:
        return primary_reply


def _safe_profile(profile: dict | None) -> dict:
    if profile:
        return profile
    return {"full_name": "there", "role": "unknown", "patient_id": None}


def _format_medication_list(medications: list[dict]) -> str:
    try:
        if not medications:
            return "No active medications recorded yet."

        lines = ["Active medications:"]
        for index, medication in enumerate(medications[:5], start=1):
            drug_name = medication.get("drug_name", "Unknown medicine")
            dose_amount = medication.get("dose_amount", "")
            dose_unit = medication.get("dose_unit", "")
            frequency = medication.get("frequency", "")
            next_dose = medication.get("next_dose_time")
            detail = f"{dose_amount}{dose_unit} {frequency}".strip()
            if next_dose:
                detail = f"{detail} at {next_dose}".strip()
            lines.append(f"{index}. {drug_name} {detail}".strip())
        lines.append("\nReply HELP for menu.")
        return "\n".join(lines)
    except Exception:
        return "No active medications recorded yet."


def _format_time(value: str | None) -> str:
    try:
        if not value:
            return "unknown time"
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.strftime("%b %d, %I:%M %p")
    except Exception:
        return str(value or "unknown time")


def _patient_id(profile: dict) -> str | None:
    patient_id = profile.get("patient_id")
    return str(patient_id) if patient_id else None


def _has_veto_power(profile: dict) -> bool:
    try:
        role = str(profile.get("role") or "").strip().lower().replace("-", "_").replace(" ", "_")
        return role in {"primary_caregiver", "primary_care_giver"}
    except Exception:
        return False


def _approval_window_state(pending_task: dict | None) -> str:
    """Return active, expired, or missing for approval/veto command handling."""
    try:
        context = pending_task or {}
        approval_types = getattr(intent_classifier, "APPROVAL_CONTEXT_TYPES", set())
        if context.get("type") not in approval_types:
            return "missing"
        state_getter = getattr(intent_classifier, "_approval_context_state", None)
        if callable(state_getter):
            return state_getter(context)
        return "active"
    except Exception:
        return "missing"


def _contains_any(text: str, keywords: list[str]) -> bool:
    lowered = str(text or "").lower()
    return any(keyword in lowered for keyword in keywords)


def _extract_observation_flags(message: str) -> dict:
    text = str(message or "").lower()
    return {
        "symptoms": [
            label
            for label, keywords in {
                "dizziness": ["dizziness", "dizzy", "chakkar"],
                "weakness": ["weak", "kamzori", "tired", "thakaan"],
                "confusion": ["confused", "confusion", "ulta seedha"],
                "fever": ["fever", "bukhar"],
                "pain": ["pain", "dard"],
            }.items()
            if _contains_any(text, keywords)
        ],
        "diet": {
            "missed": _contains_any(text, ["did not eat", "didn't eat", "nahi khaya", "skip breakfast", "skipped breakfast", "skipped lunch", "skipped dinner"]),
            "ate": _contains_any(text, ["ate", "kha liya", "khana khaya", "finished breakfast", "finished lunch", "finished dinner"]),
        },
        "medication": {
            "taken": _contains_any(text, ["took all", "took medicine", "took medicines", "dawai le li", "medicine le li", "tablets le li"]),
            "missed": _contains_any(text, ["did not take", "didn't take", "missed medicine", "missed medicines", "skipped medicine", "skipped medicines", "dawai nahi li"]),
        },
    }


def _detect_observation_conflicts(flags: dict, patient_reports: list[dict]) -> list[dict]:
    conflicts: list[dict] = []
    try:
        for report in patient_reports:
            body = str(report.get("body") or "").lower()
            when = report.get("received_at")
            if flags.get("medication", {}).get("taken") and _contains_any(body, ["nahi li", "not taken", "missed", "skipped"]):
                conflicts.append({"type": "medication", "caregiver_claim": "taken", "patient_claim": "missed_or_not_taken", "patient_message": body[:160], "patient_reported_at": when})
            if flags.get("medication", {}).get("missed") and _contains_any(body, ["le li", "taken", "took", "done"]):
                conflicts.append({"type": "medication", "caregiver_claim": "missed", "patient_claim": "taken", "patient_message": body[:160], "patient_reported_at": when})
            if flags.get("diet", {}).get("missed") and _contains_any(body, ["khaya", "ate", "breakfast done", "lunch done", "dinner done"]):
                conflicts.append({"type": "diet", "caregiver_claim": "missed_meal", "patient_claim": "ate", "patient_message": body[:160], "patient_reported_at": when})
            if flags.get("symptoms") and _contains_any(body, ["sab theek", "fine", "no pain", "no dizziness", "chakkar nahi", "theek hoon"]):
                conflicts.append({"type": "symptom", "caregiver_claim": ",".join(flags.get("symptoms", [])), "patient_claim": "no_issue", "patient_message": body[:160], "patient_reported_at": when})
        return conflicts[:3]
    except Exception:
        return []


def handle_caregiver_observation(profile: dict, message: str) -> str:
    try:
        patient_id = _patient_id(profile)
        if not patient_id:
            return "Observation noted, but I do not have a linked patient record yet."

        flags = _extract_observation_flags(message)
        patient_reports = db.get_recent_patient_self_reports(patient_id, limit=5)
        conflicts = _detect_observation_conflicts(flags, patient_reports)
        stored = db.log_caregiver_observation(
            patient_id,
            profile.get("id"),
            message,
            flags,
            conflicts,
            source_type="web_caregiver_observation",
        )
        observation_id = stored.get("id") if isinstance(stored, dict) else None
        if conflicts:
            db.create_observation_conflict_alert(patient_id, profile.get("id"), observation_id, conflicts)
            return "Observation saved. I found a possible mismatch with the patient's recent self-report and opened an alert for the primary caregiver to review."
        return "Observation saved. I will use this in the care timeline and future summaries."
    except Exception:
        return "Observation noted, but I could not fully process it right now."


def handle_status_query(profile: dict, message: str = "") -> str:
    try:
        patient_id = _patient_id(profile)
        if not patient_id:
            return "I don't have a linked patient record yet. Reply HELP for menu."

        medications = db.get_active_medications_schedule(patient_id)
        time_context = parse_temporal_query(message, datetime.now())
        should_filter = time_context.get("is_now") or time_context.get("confidence", 0) >= 0.75
        if message and should_filter and time_context.get("time_bucket") != "today":
            filtered = filter_medications_by_time(medications, time_context, datetime.now())
            if filtered:
                return _format_medication_list(filtered)

        return _format_medication_list(medications)
    except Exception:
        return "No active medications recorded yet."


def handle_latest_medication(profile: dict) -> str:
    try:
        patient_id = _patient_id(profile)
        if not patient_id:
            return "I don't have a linked patient record yet. Reply HELP for menu."

        latest = db.get_latest_medication_log(patient_id)
        if not latest:
            return "No medication activity has been recorded yet. Reply HELP for menu."

        drug = latest.get("drug_name") or "Medicine"
        event = latest.get("event_type") or "reported"
        reported_at = _format_time(latest.get("reported_at"))
        return f"Latest medication update: {drug} was marked {event} at {reported_at}.\n\nReply HELP for menu."
    except Exception:
        return "I don't have the latest medication update right now. Reply HELP for menu."


def handle_appointment(
    profile: dict,
    message: str,
    intent: str,
    pending_context: dict | None = None,
) -> str:
    try:
        return appointment_manager.handle_appointment_message(profile, message, intent, pending_context)
    except Exception as error:
        print(f"Appointment handler failed: {error}")
        return APPOINTMENT_CONTEXT_FALLBACK


def _extract_drug_name(message: str, medications: list[dict]) -> str | None:
    text = str(message or "").lower()
    for medication in medications:
        drug_name = str(medication.get("drug_name") or "").strip()
        if drug_name and drug_name.lower() in text:
            return drug_name
        brand_name = str(medication.get("brand_name") or "").strip()
        if brand_name and brand_name.lower() in text:
            return drug_name or brand_name
    aliases = {
        "glycomet": "Metformin",
        "glucophage": "Metformin",
        "amlokind": "Amlodipine",
        "norvasc": "Amlodipine",
        "cardace": "Ramipril",
        "gluconorm": "Glimepiride",
    }
    for alias, canonical in aliases.items():
        if alias in text:
            return canonical
    return None


def _extract_side_effect_symptom(message: str) -> str | None:
    text = str(message or "").lower()
    symptoms = [
        "dizziness",
        "chakkar",
        "swelling",
        "sojan",
        "headache",
        "nausea",
        "vomiting",
        "ulti",
        "diarrhea",
        "loose motion",
        "cough",
        "sweating",
        "weak",
        "kamzori",
        "allergy",
        "rash",
    ]
    for symptom in symptoms:
        if symptom in text:
            return symptom
    return None


def _doctor_name(profile: dict) -> str:
    try:
        caregivers = db.get_caregivers(profile.get("patient_id"))
        for caregiver in caregivers:
            if caregiver.get("role") == "doctor" and caregiver.get("name"):
                return caregiver["name"]
    except Exception:
        pass
    return "your doctor"


def handle_medication_side_effect(profile: dict, message: str) -> str:
    from config import SIDE_EFFECT_HINTS
    from ingestion import process_side_effect_lookup

    try:
        patient_id = (profile or {}).get("patient_id") or _patient_id(profile)
        if not patient_id:
            return "I can check possible side effects once this chat is linked to a patient record."

        result = process_side_effect_lookup(
            patient_id=patient_id,
            drug_name=None,
            symptom=None,
            raw_text=message,
        )
        if result.get("source") == "known_hint":
            return result.get("reply") or "This can be a known side effect. Please monitor and contact the doctor if severe."

        drug_name = result.get("drug_name")
        symptom = result.get("symptom")

        db.enqueue_pharmagent_side_effect_lookup(
            patient_id,
            profile.get("id"),
            drug_name,
            symptom,
            message,
            from_phone=profile.get("phone"),
        )

        hint = SIDE_EFFECT_HINTS.get((str(drug_name or "").lower(), str(symptom or "").lower()))
        if hint:
            return hint.strip()
        return "I've asked PharmAgent to research this. I'll update you shortly."
    except Exception:
        return "I queued this for review. If symptoms are severe, contact your doctor."


def handle_lab_query(profile: dict, message: str = "") -> str:
    try:
        patient_id = _patient_id(profile)
        if not patient_id:
            return "I don't have a linked patient record yet. Reply HELP for menu."

        labs = db.get_recent_labs(patient_id, limit=5)
        if not labs:
            return "No recent lab reports are recorded yet. Reply HELP for menu."

        text = str(message or "").lower()
        filtered = []
        for lab in labs:
            name = str(lab.get("test_name") or "").lower()
            if name and name in text:
                filtered.append(lab)
        selected = filtered or labs[:3]

        lines = ["Latest lab reports:"]
        for index, lab in enumerate(selected[:3], start=1):
            name = lab.get("test_name") or "Lab"
            value = lab.get("value")
            unit = lab.get("unit") or ""
            date_text = lab.get("report_date") or "recent"
            severity = lab.get("severity") or "normal"
            lines.append(f"{index}. {name}: {value}{unit} ({severity}, {date_text})")
        lines.append("\nReply HELP for menu.")
        return "\n".join(lines)
    except Exception:
        return "I don't have the latest lab report right now. Reply HELP for menu."


def handle_greeting(profile: dict) -> str:
    try:
        safe = _safe_profile(profile)
        return (
            f"Hello {safe['full_name']}! CareCircle is here to help. "
            "You can ask: 'What meds are active?', 'How is the patient?', "
            "or send BP/sugar readings."
        )
    except Exception:
        return "Hello! CareCircle is here to help. Reply HELP for menu."


def handle_crisis(profile: dict) -> str:
    try:
        _ = profile
        return (
            "Emergency mode activated. Please seek immediate medical help if needed. "
            "This demo has not sent alerts yet, but the crisis workflow is now detected."
        )
    except Exception:
        return "Emergency mode activated. Please seek immediate medical help if needed."


def handle_medication_report(profile: dict, message: str, pending_context: dict | None = None) -> str:
    try:
        _ = profile
        lowered = message.lower().strip()
        if pending_context and lowered in ["yes", "haan", "took it"]:
            return "Recorded. Thank you!"
        if any(word in lowered for word in ["took", "missed", "skipped", "liya", "nahi liya"]):
            return "Noted. Thank you for the update!"
        return "I didn't catch that. Please reply YES or NO to confirm."
    except Exception:
        return "I didn't catch that. Please reply YES or NO to confirm."


def handle_medication_confirmation(
    profile: dict,
    message: str,
    intent: str,
    pending_context: dict | None = None,
) -> str:
    try:
        if not pending_context or not pending_context.get("target_id"):
            return MEDICATION_CONTEXT_FALLBACK

        patient_id = profile.get("patient_id")
        profile_id = profile.get("id")
        medication_id = pending_context["target_id"]
        if not patient_id or not profile_id:
            return "I understood your reply, but I could not link it to a patient record."

        if intent == "medication_missed_confirm":
            event_type = "missed"
        elif intent == "medication_taken_incorrect":
            event_type = "taken"
        else:
            event_type = "taken"

        new_id = db.log_medication_event(
            str(patient_id),
            str(medication_id),
            str(profile_id),
            event_type,
            message,
        )
        db.write_audit(
            str(patient_id),
            str(profile_id),
            "medication_log",
            new_id,
            "MEDICATION_EVENT_RECORDED",
            str(profile.get("role") or "unknown"),
            {
                "event_type": event_type,
                "raw_text": message,
                "pending_task": pending_context,
            },
        )

        if intent == "medication_taken_incorrect":
            return "Recorded. I noted that the timing may have been different."
        if event_type == "missed":
            return "Recorded as missed. Thank you for the update."
        return "Recorded as taken. Thank you!"
    except Exception:
        return "I understood your reply, but could not record it right now."


def handle_vital_report(message: str) -> str:
    try:
        vitals = parse_vital_values(message)
        if not vitals:
            return "I could not read the vital values clearly. Please send like: BP 140/90 or Sugar 180."
        return format_care_response({"vitals": vitals, "confidence": 0.95}, "vitals")
    except Exception:
        return "I could not read the vital values clearly. Please send like: BP 140/90 or Sugar 180."


def classify_health_report_severity(message: str, intent: str) -> dict:
    """
    Second-stage deterministic severity scoring for health narrative intents.
    Returns level: mild | moderate | severe_chronic | severe_acute.
    """
    try:
        normalized = intent_classifier.normalize_message(message)
        text = f" {normalized.lower()} "
        is_symptom = intent == "symptom_report"
        has_mild = _has_any(text, MILD_HEALTH_MARKERS)
        has_moderate = _has_any(text, MODERATE_HEALTH_MARKERS)
        has_severe = _has_any(text, SEVERE_HEALTH_MARKERS)
        has_acute = _has_any(text, ACUTE_HEALTH_MARKERS)
        has_chronic = _has_any(text, CHRONIC_HEALTH_MARKERS)
        has_critical_symptom = _has_any(text, CRITICAL_ACUTE_SYMPTOMS)

        is_valid_emergency, emergency_reason = intent_classifier.is_valid_emergency(message)
        if is_valid_emergency:
            return {
                "level": "severe_acute",
                "confidence": 1.0,
                "reason": f"emergency_detector:{emergency_reason}",
            }

        if is_symptom and has_critical_symptom and (has_acute or has_severe):
            return {
                "level": "severe_acute",
                "confidence": 0.95,
                "reason": "critical_symptom_with_acute_or_severe_marker",
            }

        if has_severe and (has_chronic or not has_acute):
            return {
                "level": "severe_chronic",
                "confidence": 0.86,
                "reason": "severe_marker_without_acute_emergency",
            }

        if has_moderate or (has_severe and intent in {"mood_report", "emotional_checkin", "sleep_report"}):
            return {
                "level": "moderate",
                "confidence": 0.78,
                "reason": "moderate_health_marker",
            }

        if has_mild:
            return {"level": "mild", "confidence": 0.72, "reason": "mild_marker"}

        if intent in {"diet_report", "exercise_report"}:
            return {"level": "mild", "confidence": 0.68, "reason": f"{intent}:routine_update"}

        return {"level": "moderate" if is_symptom else "mild", "confidence": 0.65, "reason": "default_by_intent"}
    except Exception:
        return {"level": "moderate", "confidence": 0.5, "reason": "severity_scoring_error"}


def handle_health_report(profile: dict, message: str, intent: str) -> tuple[str, float, dict]:
    try:
        _ = profile
        severity = "mild"
        msg_lower = message.lower()
        severe_words = ["severe", "bahut", "zyada", "tez", "critical", "extreme", "bad", "bura"]
        moderate_words = ["moderate", "thoda zyada", "more than usual", "worsening"]

        if any(w in msg_lower for w in ["chest pain", "seene mein dard", "heart pain", "breathing problem", "saans nahi"]):
            severity = "severe_acute"
        elif any(w in msg_lower for w in severe_words):
            severity = "severe_chronic"
        elif any(w in msg_lower for w in moderate_words):
            severity = "moderate"

        if severity == "severe_acute":
            return (
                "This may be urgent. I am escalating to emergency mode now.",
                1.0,
                {"level": severity, "confidence": 1.0, "reason": "acute_health_keyword"},
            )
        if severity == "severe_chronic":
            try:
                patient_id = _patient_id(profile)
                if patient_id:
                    db.create_alert(
                        patient_id,
                        "symptom_severe",
                        "medium",
                        "Severe symptom report needs caregiver review.",
                        {"raw_text": message, "intent": intent, "severity": severity},
                    )
            except Exception:
                pass
            return (
                "I have noted this. Given the severity, I recommend contacting the doctor.",
                0.82,
                {"level": severity, "confidence": 0.82, "reason": "severe_word"},
            )
        if severity == "moderate":
            return (
                "Noted. Please monitor and update if it changes.",
                0.82,
                {"level": severity, "confidence": 0.82, "reason": "moderate_word"},
            )

        return (
            "Noted. I have classified this update for the care timeline.",
            0.82,
            {"level": severity, "confidence": 0.82, "reason": "mild_default"},
        )
    except Exception:
        return (
            "Noted. I have classified this update and will use it in the care timeline later.",
            0.5,
            {"level": "moderate", "confidence": 0.5, "reason": "health_handler_error"},
        )


def handle_care_summary(profile: dict) -> str:
    try:
        patient_id = _patient_id(profile)
        if not patient_id:
            return "I don't have a linked patient record yet. Reply HELP for menu."

        today = date.today()
        active_meds = db.get_active_medications_schedule(patient_id)
        logs = db.get_medication_log_for_date(patient_id, today)
        recent_vitals = db.get_recent_vitals(patient_id)
        recent_labs = db.get_recent_labs(patient_id)
        open_alerts = db.get_open_alerts(patient_id)
        adherence = compute_adherence_snapshot(patient_id, logs, active_meds, today)
        summary = assemble_care_summary(
            patient_id,
            active_meds,
            recent_vitals,
            recent_labs,
            open_alerts,
            adherence,
        )
        return format_care_response(summary, "summary")
    except Exception:
        return "I don't have enough information for a care summary right now. Reply HELP for menu."


def handle_upload(intent: str) -> str:
    try:
        labels = {
            "photo_upload": "photo",
            "audio_note": "audio note",
            "pdf_upload": "PDF",
            "location_update": "location",
        }
        return f"Received the {labels.get(intent, 'attachment')}. Processing will be added in the next stage."
    except Exception:
        return "Received the attachment. Processing will be added in the next stage."


def handle_document_upload_confirmation(profile: dict) -> str:
    try:
        phone = profile.get("phone")
        task = db.get_recent_media_task_for_phone(phone, minutes=30) if phone else None
        if not task:
            return "I do not see a recent photo, audio, or PDF processing task yet. Please attach the file here and I will process it."

        labels = {
            "ocr_prescription": "prescription photo",
            "parse_pdf": "PDF report",
            "transcribe_audio": "audio note",
        }
        label = labels.get(task.get("task_type"), "file")
        status = str(task.get("status") or "queued")
        if status == "queued":
            return f"Received the {label}. It is queued for processing."
        if status == "in_progress":
            return f"Received the {label}. Processing is in progress."
        if status == "done":
            return f"The {label} has been processed. I will use the extracted details in future replies."
        if status == "failed":
            return f"I found the {label}, but processing failed. Please resend a clearer file."
        return f"I found the {label}. Current processing status: {status}."
    except Exception:
        return "I will check the uploaded file status shortly. If needed, please resend the attachment."


def handle_abusive_language() -> str:
    return "I am here to help, but please keep the message respectful. What do you need?"


def _audit_emergency(profile: dict, action: str, message: str, reason: str) -> None:
    try:
        db.write_audit(
            _uuid_or_none(profile.get("patient_id")),
            _uuid_or_none(profile.get("id")),
            "crisis",
            None,
            action,
            str(profile.get("role") or "unknown"),
            {"trigger": message, "reason": reason},
        )
    except Exception:
        pass


def _crisis_card_reply(profile: dict) -> str:
    try:
        return crisis_runtime.build_patient_crisis_reply(profile)
    except Exception:
        return handle_crisis(profile)


def _strip_patient_reply_phone_numbers(reply: str) -> str:
    try:
        import re

        # Patient-facing crisis acknowledgements should not echo caregiver phones.
        return re.sub(r"\+91\d{10}", "[phone hidden]", str(reply or ""))
    except Exception:
        return str(reply or "")


def _append_caregiver_notification_confirmation(
    reply: str,
    profile: dict,
    trigger_message: str = "",
) -> tuple[str, list[dict]]:
    try:
        patient_id = profile.get("patient_id")
        if not patient_id:
            return reply, []

        notified = alerting.send_crisis_alerts(
            str(patient_id),
            str(profile.get("patient_name") or "the patient"),
            force=True,
            trigger_message=trigger_message,
            triggered_by_phone=str(profile.get("phone") or ""),
        )
        names = [
            f"{caregiver.get('name')} ({caregiver.get('role')})"
            for caregiver in notified
            if caregiver.get("name") and caregiver.get("role")
        ]
        if not names:
            return reply, notified

        confirm_text = f"Caregiver alerts sent: {', '.join(names)}"
        return f"{reply}\n\n{confirm_text}", notified
    except Exception:
        return reply, []


def _llm_reply_has_emergency_signal(reply: str) -> bool:
    try:
        text = str(reply or "").lower()
        if "emergency_flag_detected" in text:
            return True
        return any(keyword in text for keyword in LLM_EMERGENCY_KEYWORDS)
    except Exception:
        return False


def _uuid_or_none(value) -> str | None:
    try:
        text = str(value or "").strip()
        return text if len(text) == 36 and text.count("-") == 4 else None
    except Exception:
        return None


def _router_classification(
    message: str,
    pending_task: dict | None,
    routed_intent: str | None,
    routed_confidence: float | None,
    routed_source: str | None,
) -> dict | None:
    if not routed_intent:
        return None
    try:
        base = intent_classifier.classify_intent_with_confidence(message, pending_task)
        return {
            "intent": str(routed_intent),
            "confidence": _bounded_confidence(routed_confidence, 0.0),
            "reason": f"router:{routed_source or 'unknown'}",
            "normalized_text": base.get("normalized_text") or intent_classifier.normalize_message(message),
            "secondary_intents": [] if routed_source == "compound_secondary" else list(base.get("secondary_intents") or []),
        }
    except Exception:
        return {
            "intent": str(routed_intent),
            "confidence": _bounded_confidence(routed_confidence, 0.0),
            "reason": "router:provided",
            "normalized_text": "",
            "secondary_intents": [],
        }


def _route_llm_with_emergency_safety(
    message: str,
    profile: dict,
    intent: str,
    classifier: dict | None = None,
) -> dict:
    is_valid, reason = intent_classifier.is_valid_emergency(message)
    if is_valid:
        _audit_emergency(profile, "emergency_detected", message, reason)
        reply, caregiver_alerts = _append_caregiver_notification_confirmation(
            _crisis_card_reply(profile),
            profile,
            message,
        )
        return _response(
            reply,
            "crisis",
            1.0,
            "deterministic_safety",
            f"safety_gate:{reason}",
            "safety_gate",
            classifier,
            {"caregiver_alerts": caregiver_alerts},
        )

    recent = db.get_recent_messages_for_phone(profile["phone"], limit=2) or []
    llm_reply = llm.generate_reply(message, profile, recent, intent)
    if _llm_reply_has_emergency_signal(llm_reply):
        _audit_emergency(profile, "llm_emergency_fallback", message, "llm_output_safety")
        reply, caregiver_alerts = _append_caregiver_notification_confirmation(
            _crisis_card_reply(profile),
            profile,
            message,
        )
        return _response(
            reply,
            "crisis",
            1.0,
            "llm_output_safety",
            "llm_output_safety:emergency_signal",
            "llm_safety_gate",
            classifier,
            {"caregiver_alerts": caregiver_alerts},
        )

    return _response(llm_reply, intent, 0.65, "llm_fallback", "llm:allowed_low_risk", "llm", classifier)


def route_message(
    message: str,
    profile: dict,
    pending_task: dict | None = None,
    routed_intent: str | None = None,
    routed_confidence: float | None = None,
    routed_source: str | None = None,
) -> dict:
    """
    Returns {"reply": str, "intent": str, "confidence": float, "source": str}.
    """
    try:
        secondary_results: list[dict] = []
        classification = _router_classification(message, pending_task, routed_intent, routed_confidence, routed_source)
        if classification is None:
            classification = intent_classifier.classify_intent_with_confidence(message, pending_task)
        intent = classification["intent"]
        classifier_conf = classification.get("confidence") if classification else None

        def use_conf(default: float) -> float:
            return _bounded_confidence(classifier_conf, default) if classifier_conf is not None else _bounded_confidence(default)

        secondary_intents = list(classification.get("secondary_intents") or [])
        response_extra: dict = {}

        if intent in ["unknown", "unclear"]:
            return _route_llm_with_emergency_safety(message, profile, intent, classification)

        if intent == "emotional_checkin":
            if config.ENABLE_WARM_GREETINGS:
                return _route_llm_with_emergency_safety(message, profile, intent, classification)
            safe = _safe_profile(profile)
            reply = (
                f"I hear you, {safe['full_name']}. "
                "I'm here to listen. If you need to talk or need help, just let me know."
            )
            confidence = use_conf(0.88)
        elif intent in ["greeting", "greeting_help"]:
            is_valid, reason = intent_classifier.is_valid_emergency(message)
            if is_valid:
                _audit_emergency(profile, "emergency_detected", message, reason)
                reply, caregiver_alerts = _append_caregiver_notification_confirmation(
                    _crisis_card_reply(profile),
                    profile,
                    message,
                )
                return _response(
                    reply,
                    "crisis",
                    1.0,
                    "deterministic_safety",
                    f"safety_gate:{reason}",
                    "safety_gate",
                    classification,
                    {"caregiver_alerts": caregiver_alerts},
                )
            if config.ENABLE_WARM_GREETINGS:
                return _route_llm_with_emergency_safety(message, profile, intent, classification)
            reply = handle_greeting(profile)
            confidence = _classifier_confidence(classification, 0.9)
        elif intent == "crisis" or str(intent).startswith("crisis_"):
            is_valid, reason = intent_classifier.is_valid_emergency(message)
            if is_valid or str(intent).startswith("crisis_"):
                _audit_emergency(profile, "emergency_detected", message, reason)
                reply = _crisis_card_reply(profile)
                reply, caregiver_alerts = _append_caregiver_notification_confirmation(reply, profile, message)
                confidence = 1.0
                return _response(
                    reply,
                    intent,
                    confidence,
                    "deterministic",
                    f"deterministic:{intent}",
                    "handler",
                    classification,
                    {"caregiver_alerts": caregiver_alerts},
                )
            else:
                reply = PAIN_NON_EMERGENCY_FALLBACK
                confidence = _classifier_confidence(classification, 0.7)
        elif intent in ["status_query", "medication_query", "medication_due_now", "medication_list", "medication_schedule"]:
            reply = handle_status_query(profile, message)
            confidence = _classifier_confidence(classification, 0.88)
        elif intent == "medication_latest":
            reply = handle_latest_medication(profile)
            confidence = _classifier_confidence(classification, 0.9)
        elif intent == "medication_side_effect":
            reply = handle_medication_side_effect(profile, message)
            confidence = use_conf(0.82)
        elif intent in [
            "medication_report",
            "medication_taken_incorrect",
            "medication_taken_confirm",
            "medication_missed_confirm",
        ]:
            reply = handle_medication_confirmation(profile, message, intent, pending_task)
            confidence = _classifier_confidence(classification, 0.9 if pending_task else 0.82)
        elif intent == "vital_report":
            reply = handle_vital_report(message)
            confidence = _classifier_confidence(classification, 0.92)
        elif intent in ["family_member_query", "caregiver_update", "health_status_query"]:
            reply = handle_care_summary(profile)
            confidence = _classifier_confidence(classification, 0.86)
        elif intent == "caregiver_observation":
            try:
                patient_id = _patient_id(profile)
                if patient_id:
                    db.write_audit(
                        patient_id,
                        profile.get("id"),
                        "caregiver_observation",
                        None,
                        "OBSERVATION_LOGGED",
                        str(profile.get("role") or "caregiver"),
                        {"raw_text": message, "normalized": classification.get("normalized_text", "")},
                    )
                reply = "Thank you for the update. I have noted this observation."
            except Exception:
                reply = "Thank you for the update."
            confidence = use_conf(0.86)
        elif intent in ["photo_upload", "audio_note", "pdf_upload", "location_update"]:
            reply = handle_upload(intent)
            confidence = _classifier_confidence(classification, 0.9)
        elif intent in HEALTH_REPORT_INTENTS:
            reply, severity_confidence, severity = handle_health_report(profile, message, intent)
            if severity.get("level") == "severe_acute":
                _audit_emergency(profile, "health_report_escalated", message, severity.get("reason", "severe_acute"))
                reply = _crisis_card_reply(profile)
                reply, caregiver_alerts = _append_caregiver_notification_confirmation(reply, profile, message)
                return _response(
                    reply,
                    "crisis",
                    1.0,
                    "deterministic_safety",
                    f"health_severity:{severity.get('reason', 'severe_acute')}",
                    "health_severity",
                    classification,
                    {"health_severity": severity, "caregiver_alerts": caregiver_alerts},
                )
            confidence = max(use_conf(0.82), severity_confidence)
            response_extra["health_severity"] = severity
        elif intent in [
            "appointment_query",
            "appointment_add",
            "appointment_confirmed",
            "appointment_declined",
            "doctor_visit_update",
            "refill_request",
            "refill_confirmed",
            "refill_declined",
        ]:
            if intent in ["appointment_query", "appointment_add", "appointment_confirmed", "appointment_declined", "doctor_visit_update"]:
                reply = handle_appointment(profile, message, intent, pending_task)
                confidence = _classifier_confidence(classification, 0.82)
            else:
                reply = "I understood the request, but this workflow is not connected yet. Reply HELP for menu."
                confidence = _classifier_confidence(classification, 0.78)
        elif intent in ["veto_command", "approve_command"]:
            if not _has_veto_power(profile):
                return _response(
                    APPROVAL_UNAUTHORIZED_FALLBACK,
                    "approval_not_authorized",
                    use_conf(0.95),
                    "deterministic",
                    "command_gate:role_not_primary_caregiver",
                    "command_gate",
                    classification,
                )

            command_match = re.search(r"\b(VETO|APPROVE|APPROVED|FINALIZE)\s+([a-f0-9]{6,64})\b", message, re.I)
            if command_match:
                import pharma_promotion

                command = command_match.group(1).lower()
                rule_code = command_match.group(2).lower()
                if command == "veto":
                    decision = pharma_promotion.veto_approval(rule_code, actor=str(profile.get("phone") or "chat"))
                else:
                    decision = pharma_promotion.finalize_approval(rule_code, actor=str(profile.get("phone") or "chat"))
                return _response(
                    decision.get("reply") or "Decision command received and processed.",
                    "pharma_approval_decision",
                    use_conf(0.95),
                    "deterministic",
                    f"pharma_approval:{decision.get('status', 'unknown')}",
                    "pharma_approval",
                    classification,
                    {"pharma_approval": decision},
                )

            if not pending_task or pending_task.get("type") not in {
                "veto_window",
                "approval_window",
                "pending_approval",
                "interaction_alert",
                "new_med",
            }:
                return _response(
                    APPROVAL_CONTEXT_FALLBACK,
                    "approval_context_missing",
                    use_conf(0.9),
                    "deterministic",
                    "command_gate:no_active_context",
                    "command_gate",
                    classification,
                )

            expires_at = pending_task.get("expires_at")
            if expires_at:
                try:
                    exp = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
                    if datetime.now(exp.tzinfo) > exp:
                        return _response(
                            APPROVAL_CONTEXT_EXPIRED_FALLBACK,
                            "approval_context_expired",
                            use_conf(0.9),
                            "deterministic",
                            "command_gate:expired",
                            "command_gate",
                            classification,
                        )
                except Exception:
                    pass

            approval_state = _approval_window_state(pending_task)
            if approval_state == "expired":
                return _response(
                    APPROVAL_CONTEXT_EXPIRED_FALLBACK,
                    "approval_context_expired",
                    use_conf(0.9),
                    "deterministic",
                    "command_gate:expired_context",
                    "command_gate",
                    classification,
                )
            if approval_state != "active":
                return _response(
                    APPROVAL_CONTEXT_FALLBACK,
                    "approval_context_missing",
                    use_conf(0.9),
                    "deterministic",
                    "command_gate:missing_context",
                    "command_gate",
                    classification,
                )
            reply = "Decision command received and processed."
            confidence = use_conf(0.95)
        elif intent == "approval_context_missing":
            reply = APPROVAL_CONTEXT_FALLBACK
            confidence = _classifier_confidence(classification, 0.9)
        elif intent == "approval_context_expired":
            reply = APPROVAL_CONTEXT_EXPIRED_FALLBACK
            confidence = _classifier_confidence(classification, 0.9)
        elif intent == "abusive_language":
            reply = handle_abusive_language()
            confidence = _classifier_confidence(classification, 0.9)
        elif intent == "lab_report":
            reply = handle_lab_query(profile, message)
            confidence = _classifier_confidence(classification, 0.9)
        elif intent == "document_upload_confirmation":
            reply = handle_document_upload_confirmation(profile)
            confidence = _classifier_confidence(classification, 0.84)
        elif intent in ["new_prescription", "discharge_summary", "caregiver_handoff"]:
            reply = "Noted. This update is classified and ready for the next workflow stage."
            confidence = _classifier_confidence(classification, 0.84)
        else:
            reply = SAFE_FALLBACK
            confidence = _classifier_confidence(classification, 0.5)

        for secondary in secondary_intents[:3]:
            secondary_intent = secondary.get("intent")
            secondary_clause = secondary.get("clause") or message
            if not secondary_intent or secondary_intent == intent:
                continue
            secondary_result = route_message(
                secondary_clause,
                profile,
                pending_task,
                routed_intent=secondary_intent,
                routed_confidence=secondary.get("confidence"),
                routed_source="compound_secondary",
            )
            secondary_results.append(
                {
                    "intent": secondary_result.get("intent", secondary_intent),
                    "confidence": secondary_result.get("confidence", secondary.get("confidence")),
                    "clause": secondary_clause,
                    "reply": secondary_result.get("reply", ""),
                }
            )

        reply = _merge_compound_replies(reply, secondary_results)
        if secondary_results:
            response_extra["secondary_results"] = secondary_results
        return _response(
            reply,
            intent,
            confidence,
            "deterministic",
            f"deterministic:{intent}",
            "handler",
            classification,
            response_extra or None,
        )
    except Exception:
        return _response(
            "I didn't quite understand. Could you rephrase that?",
            "unknown",
            0.0,
            "fallback",
            "exception:fallback",
            "error",
        )
