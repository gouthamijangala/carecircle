"""
Deterministic appointment workflow for caregiver chat.

This module keeps appointment intent handling explainable and DB-backed:
queries read care_appointments, add requests insert care_appointments, and
confirmation/reminder context is stored in pending_tasks.
"""

from __future__ import annotations

import re
import json
import threading
import time
from datetime import date, datetime, time as dt_time, timedelta, timezone
from typing import Any

import config
import db
import notification_dispatcher


IST = timezone(timedelta(hours=5, minutes=30))
_started = False

SPECIALTIES = {
    "cardiology": ["cardiology", "cardiologist", "cardiac", "cardio", "heart", "heart doctor", "heart specialist", "dil"],
    "nephrology": ["nephrology", "nephrologist", "kidney specialist", "kidney doctor"],
    "endocrinology": ["endocrinology", "endocrinologist", "diabetes doctor", "sugar doctor"],
    "neurology": ["neurology", "neurologist", "brain doctor"],
    "psychiatry": ["psychiatry", "psychiatrist"],
    "dentistry": ["dentist", "dental"],
}

MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

WEEKDAYS = {
    "monday": 0, "somwar": 0,
    "tuesday": 1, "mangalwar": 1,
    "wednesday": 2, "budhwar": 2,
    "thursday": 3, "guruvar": 3,
    "friday": 4, "shukrawar": 4,
    "saturday": 5, "shanivar": 5,
    "sunday": 6, "ravivar": 6,
}


def start_appointment_reminder_scheduler() -> bool:
    global _started
    if _started or not getattr(config, "APPOINTMENT_REMINDERS_ENABLED", True):
        return False
    _started = True
    threading.Thread(target=_appointment_reminder_loop, daemon=True).start()
    return True


def handle_appointment_message(
    profile: dict,
    message: str,
    intent: str,
    pending_context: dict | None = None,
) -> str:
    patient_id = str(profile.get("patient_id") or "")
    if not patient_id:
        return "I can manage appointments once this chat is linked to a patient record."

    if intent == "appointment_add":
        return add_appointment_from_message(profile, message, pending_context)
    if intent == "appointment_confirmed":
        return confirm_appointment_from_context(profile, message, pending_context)
    if intent == "appointment_declined":
        return decline_appointment_from_context(profile, message, pending_context)
    if intent == "doctor_visit_update":
        return log_doctor_visit_update(profile, message)
    return query_appointments(profile, message)


def query_appointments(profile: dict, message: str) -> str:
    patient_id = str(profile.get("patient_id") or "")
    patient_name = profile.get("patient_name") or "Dad"
    window = _query_window(message)
    all_appointments = db.get_care_appointments(
        patient_id,
        start_at=window["start"],
        end_at=window["end"],
        statuses=window["statuses"],
        limit=12,
        direction=window["direction"],
    )
    appointments = _filter_appointments_for_query(all_appointments, message)
    if not appointments:
        descriptor = _query_descriptor(message)
        if descriptor:
            return f"{patient_name} has no upcoming {descriptor} appointments right now."
        if window["history"]:
            return f"{patient_name} has no completed or cancelled appointments in that period."
        return f"{patient_name} has no upcoming appointments right now."
    title = "Recent appointments:" if window["history"] else "Upcoming appointments:"
    return "\n".join([title, *[_format_appointment(item, index) for index, item in enumerate(appointments, 1)]])


def add_appointment_from_message(profile: dict, message: str, pending_context: dict | None = None) -> str:
    patient_id = str(profile.get("patient_id") or "")
    source_message = _merge_draft_message(message, pending_context)
    parsed = parse_appointment_details(source_message)
    if parsed["needs_clarification"]:
        db.create_appointment_draft_task(
            patient_id,
            profile.get("phone"),
            source_message,
            _json_safe_parsed(parsed),
        )
        missing = ", ".join(parsed["missing"])
        return f"I can add this appointment, but I need {missing}. Please send the appointment {missing}."

    appointment_id = db.create_care_appointment(
        patient_id=patient_id,
        appointment_type=parsed["appointment_type"],
        title=parsed["title"],
        appointment_at=parsed["appointment_at"].isoformat(),
        location=parsed["location"],
        provider_name=parsed["provider_name"],
        department=parsed["department"],
        notes=f"Added from chat: {source_message[:300]}",
        status="scheduled",
    )
    if not appointment_id:
        return "I could not save the appointment right now. Please try again."

    db.write_audit(
        patient_id,
        profile.get("id"),
        "care_appointment",
        appointment_id,
        "APPOINTMENT_ADDED_FROM_CHAT",
        str(profile.get("role") or "caregiver"),
        {"raw_text": source_message, "parsed": _json_safe_parsed(parsed)},
    )
    if pending_context and pending_context.get("type") == "appointment_draft":
        db.update_pending_task_status(
            pending_context.get("id"),
            "done",
            {"appointment_id": appointment_id, "completed_from": message},
        )
    confirmation_id = db.create_appointment_confirmation_task(
        patient_id,
        appointment_id,
        profile.get("phone"),
        expires_at=parsed["appointment_at"],
    )
    suffix = " Reply YES to confirm or NO to cancel." if confirmation_id else ""
    return f"Appointment saved: {_format_appointment_summary(parsed)}.{suffix}"


def confirm_appointment_from_context(profile: dict, message: str, pending_context: dict | None) -> str:
    appointment_id = _pending_appointment_id(pending_context)
    if not appointment_id:
        return "I could not find the appointment confirmation context. Please ask for upcoming appointments."
    parsed = parse_appointment_details(message)
    new_time = None if parsed["needs_clarification"] else parsed.get("appointment_at")
    ok = db.update_care_appointment(
        appointment_id,
        status="confirmed",
        appointment_at=new_time.isoformat() if new_time else None,
        notes=f"Confirmed from chat: {message[:240]}",
    )
    if not ok:
        return "I could not confirm that appointment. Please ask for upcoming appointments and try again."
    db.update_pending_task_status(pending_context.get("id"), "done", {"appointment_id": appointment_id, "decision": "confirmed"})
    return "Appointment confirmed. I will include it in reminders and daily briefs."


def decline_appointment_from_context(profile: dict, message: str, pending_context: dict | None) -> str:
    appointment_id = _pending_appointment_id(pending_context)
    if not appointment_id:
        return "I could not find the appointment context. Please ask for upcoming appointments or share the appointment details."
    wants_reschedule = _contains_any(_clean(message), ["postpone", "reschedule", "next week", "baad", "later"])
    ok = db.update_care_appointment(
        appointment_id,
        status="cancelled",
        notes=f"Declined from chat: {message[:240]}",
    )
    if not ok:
        return "I could not update that appointment. Please ask for upcoming appointments and try again."
    db.update_pending_task_status(pending_context.get("id"), "done", {"appointment_id": appointment_id, "decision": "declined"})
    if wants_reschedule:
        return "Appointment marked cancelled. Please send the new date and time to reschedule it."
    return "Appointment marked cancelled. I will not treat it as upcoming."


def log_doctor_visit_update(profile: dict, message: str) -> str:
    patient_id = str(profile.get("patient_id") or "")
    parsed = parse_appointment_details(message)
    content = {
        "raw_text": message,
        "department": parsed.get("department"),
        "provider_name": parsed.get("provider_name"),
        "possible_follow_up_at": parsed.get("appointment_at").isoformat() if parsed.get("appointment_at") and not parsed["needs_clarification"] else None,
    }
    saved = db.insert_care_note(patient_id, "doctor_visit_update", json.dumps(content, ensure_ascii=True), "chat", f"visit-{abs(hash(message))}")
    if not saved:
        return "I understood this as a doctor visit update, but I could not save it right now. Please try again."
    if content["possible_follow_up_at"]:
        return "Doctor visit update saved. I noticed a possible follow-up date; send 'add appointment' with the date/time if you want me to schedule it."
    return "Doctor visit update saved in the care notes."


def scan_and_send_appointment_reminders(force: bool = False) -> dict:
    if not force and not getattr(config, "APPOINTMENT_REMINDERS_ENABLED", True):
        return {"status": "disabled", "sent": 0}
    sent = 0
    skipped = 0
    for appointment in db.get_appointments_due_for_reminder(
        hours=getattr(config, "APPOINTMENT_REMINDER_WINDOW_HOURS", 72),
        limit=100,
    ):
        bucket = _reminder_bucket(appointment.get("appointment_at"))
        if not bucket:
            skipped += 1
            continue
        if db.appointment_reminder_sent(appointment["id"], bucket):
            skipped += 1
            continue
        caregivers = db.get_caregivers(appointment["patient_id"])
        targets = [caregiver for caregiver in caregivers if caregiver.get("role") in {"primary_caregiver", "secondary_caregiver"} and caregiver.get("phone")]
        if not targets:
            skipped += 1
            continue
        message = _reminder_message(appointment, bucket)
        for caregiver in targets:
            delivery = notification_dispatcher.dispatch_user_message(
                caregiver.get("phone"),
                message,
                patient_id=appointment["patient_id"],
                priority="high" if bucket in {"24h", "same_day"} else "normal",
            )
            db.write_audit(
                appointment["patient_id"],
                None,
                "care_appointment",
                appointment["id"],
                "APPOINTMENT_REMINDER_SENT",
                "system",
                {"bucket": bucket, "caregiver": caregiver, "delivery": delivery},
            )
            sent += 1
            if bucket in {"24h", "same_day"}:
                db.create_appointment_confirmation_task(
                    appointment["patient_id"],
                    appointment["id"],
                    caregiver.get("phone"),
                    expires_at=appointment.get("appointment_at"),
                )
    return {"status": "completed", "sent": sent, "skipped": skipped}


def parse_appointment_details(message: str, now: datetime | None = None) -> dict:
    current = now or datetime.now(IST)
    text = _clean(message)
    appointment_date = _extract_date(text, current)
    appointment_time = _extract_time(text)
    department = _extract_department(text)
    provider_name = _extract_provider_name(message)
    location = _extract_location(message)
    appointment_type = "tele_consult" if _contains_any(text, ["tele consult", "tele-consult", "video consult", "phone consult"]) else "doctor"
    missing = []
    if appointment_date is None:
        missing.append("date")
    if appointment_time is None:
        missing.append("time")
    appointment_at = None
    if appointment_date is not None and appointment_time is not None:
        appointment_at = datetime.combine(appointment_date, appointment_time, tzinfo=IST)
        if appointment_at < current - timedelta(hours=2):
            appointment_at = appointment_at.replace(year=appointment_at.year + 1)
    title_parts = [part for part in (department, provider_name) if part]
    title = " / ".join(title_parts) if title_parts else "Doctor appointment"
    return {
        "appointment_type": appointment_type,
        "title": title,
        "appointment_at": appointment_at,
        "location": location,
        "provider_name": provider_name,
        "department": department,
        "missing": missing,
        "needs_clarification": bool(missing),
    }


def _appointment_reminder_loop() -> None:
    interval = max(60, int(getattr(config, "APPOINTMENT_REMINDER_POLLER_INTERVAL_SECONDS", 900)))
    while True:
        try:
            scan_and_send_appointment_reminders()
        except Exception as error:
            print(f"Appointment reminder scan failed: {error}")
        time.sleep(interval)


def _query_window(message: str) -> dict:
    now = datetime.now(IST)
    text = _clean(message)
    history = _contains_any(text, ["miss hua", "missed", "completed", "past", "previous", "last appointment", "ho gaya"])
    days_match = re.search(r"(?:next|in|agle|andar)\s+(\d{1,2})\s+days?", text)
    if days_match:
        days = int(days_match.group(1))
        return {"start": now.isoformat(), "end": (now + timedelta(days=days)).isoformat(), "statuses": ["scheduled", "confirmed", "tentative"], "direction": "asc", "history": False}
    if "today" in text or "aaj" in text:
        start = datetime.combine(now.date(), dt_time.min, tzinfo=IST)
        return {"start": start.isoformat(), "end": (start + timedelta(days=1)).isoformat(), "statuses": ["scheduled", "confirmed", "tentative"], "direction": "asc", "history": False}
    if "this week" in text or "week" in text:
        return {"start": now.isoformat(), "end": (now + timedelta(days=7)).isoformat(), "statuses": ["scheduled", "confirmed", "tentative"], "direction": "asc", "history": False}
    if "month" in text:
        return {"start": now.isoformat(), "end": (now + timedelta(days=31)).isoformat(), "statuses": ["scheduled", "confirmed", "tentative"], "direction": "asc", "history": False}
    if history:
        return {"start": (now - timedelta(days=90)).isoformat(), "end": now.isoformat(), "statuses": ["completed", "cancelled", "declined"], "direction": "desc", "history": True}
    return {"start": now.isoformat(), "end": (now + timedelta(days=14)).isoformat(), "statuses": ["scheduled", "confirmed", "tentative"], "direction": "asc", "history": False}


def _filter_appointments_for_query(appointments: list[dict], message: str) -> list[dict]:
    criteria = _appointment_query_criteria(message)
    if not criteria:
        return appointments
    filtered = []
    for item in appointments:
        haystack = _appointment_search_text(item)
        if any(_term_matches(haystack, term) for term in criteria["include"]) and not any(_term_matches(haystack, term) for term in criteria["exclude"]):
            filtered.append(item)
    return filtered


def _appointment_query_criteria(message: str) -> dict | None:
    text = _clean(message)
    criteria_map = [
        (
            ["general checkup", "general check-up", "annual physical", "physical exam", "routine checkup", "regular checkup"],
            ["general", "general medicine", "general checkup", "physical exam", "annual physical", "routine"],
            ["lab", "pathology", "blood", "cbc", "lipid", "mri", "scan", "physio", "therapy", "vaccination", "radiology"],
            "general checkup",
        ),
        (
            ["blood panel", "blood test", "lab", "cbc", "lipid", "sugar test", "diagnostic", "test appointment"],
            ["lab", "pathology", "blood", "cbc", "lipid", "diagnostic"],
            ["physio", "therapy", "vaccination", "radiology", "mri", "scan", "xray", "x-ray", "ct"],
            "lab/test",
        ),
        (
            ["mri", "scan", "xray", "x-ray", "ct", "radiology"],
            ["radiology", "mri", "scan", "xray", "x-ray", "ct"],
            ["blood", "cbc", "lipid", "pathology"],
            "scan/radiology",
        ),
        (
            ["physio", "physiotherapy", "rehab", "therapy"],
            ["physio", "physiotherapy", "rehab", "therapy"],
            ["lab", "pathology", "blood"],
            "physiotherapy",
        ),
        (
            ["cardiology", "cardiologist", "cardiac", "heart"],
            ["cardiology", "cardiologist", "cardiac", "heart"],
            ["lab", "pathology", "physio", "therapy"],
            "cardiology",
        ),
        (
            ["neurology", "neurologist", "brain"],
            ["neurology", "neurologist", "brain"],
            ["lab", "pathology", "physio", "therapy"],
            "neurology",
        ),
    ]
    for triggers, include, exclude, descriptor in criteria_map:
        if _contains_any(text, triggers):
            return {"include": include, "exclude": exclude, "descriptor": descriptor}
    department = _extract_department(text)
    if department:
        return {"include": [department], "exclude": [], "descriptor": department}
    return None


def _query_descriptor(message: str) -> str:
    criteria = _appointment_query_criteria(message)
    return str((criteria or {}).get("descriptor") or "")


def _appointment_search_text(item: dict) -> str:
    return _clean(
        " ".join(
            str(item.get(key) or "")
            for key in ("appointment_type", "title", "provider_name", "department", "location", "notes")
        )
    )


def _term_matches(haystack: str, term: str) -> bool:
    normalized = _clean(term)
    if not normalized:
        return False
    if " " in normalized or "-" in normalized:
        return normalized in haystack
    return re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", haystack) is not None


def _extract_date(text: str, now: datetime) -> date | None:
    if _contains_any(text, ["parso", "day after tomorrow"]):
        return (now + timedelta(days=2)).date()
    if _contains_any(text, ["kal", "tomorrow"]):
        return (now + timedelta(days=1)).date()
    if _contains_any(text, ["aaj", "today"]):
        return now.date()
    if match := re.search(r"in\s+two\s+weeks|do\s+hafte|2\s+weeks", text):
        return (now + timedelta(days=14)).date()
    if "next week" in text:
        return (now + timedelta(days=7)).date()
    if match := re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)\b", text):
        day = int(match.group(1))
        month = MONTHS.get(match.group(2)[:3], MONTHS.get(match.group(2)))
        if month:
            year = now.year
            candidate = date(year, month, day)
            if candidate < now.date():
                candidate = date(year + 1, month, day)
            return candidate
    if match := re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", text):
        day = int(match.group(1))
        month = int(match.group(2))
        year = int(match.group(3) or now.year)
        if year < 100:
            year += 2000
        candidate = date(year, month, day)
        if candidate < now.date() and match.group(3) is None:
            candidate = date(year + 1, month, day)
        return candidate
    for word, weekday in WEEKDAYS.items():
        if word in text:
            delta = (weekday - now.weekday()) % 7
            if delta == 0 or f"next {word}" in text:
                delta = 7
            return (now + timedelta(days=delta)).date()
    return None


def _extract_time(text: str) -> dt_time | None:
    if match := re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text):
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        meridian = match.group(3)
        if meridian == "pm" and hour != 12:
            hour += 12
        if meridian == "am" and hour == 12:
            hour = 0
        return dt_time(hour % 24, minute)
    if match := re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*baje\b", text):
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        if _contains_any(text, ["shaam", "evening", "raat"]) and hour < 12:
            hour += 12
        return dt_time(hour % 24, minute)
    if _contains_any(text, ["subah", "morning"]):
        return dt_time(9, 0)
    if _contains_any(text, ["afternoon", "dopahar"]):
        return dt_time(14, 0)
    if _contains_any(text, ["evening", "shaam"]):
        return dt_time(18, 0)
    return None


def _extract_department(text: str) -> str | None:
    for canonical, aliases in SPECIALTIES.items():
        if _contains_any(text, aliases):
            return canonical
    return None


def _extract_provider_name(message: str) -> str | None:
    if match := re.search(r"\bDr\.?\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3})", message):
        return f"Dr. {match.group(1).strip()}"
    return None


def _extract_location(message: str) -> str | None:
    if match := re.search(r"\b(?:at|in|mein|me)\s+([A-Z][A-Za-z0-9&.\-\s]{2,40})", message):
        value = match.group(1).strip(" .,-")
        value = re.split(r"\s+(?:on|at|mein|me|ko|par)\b", value, maxsplit=1)[0].strip()
        if value and not value.lower().startswith(("dr ", "dr.")):
            return value
    return None


def _format_appointment(item: dict, index: int) -> str:
    when = _format_ist(item.get("appointment_at"))
    doctor = item.get("provider_name") or item.get("department") or item.get("title") or "doctor"
    location = item.get("location") or "location not recorded"
    notes = item.get("notes") or "no preparation notes"
    return f"{index}. {when} - {doctor} at {location}. Notes: {notes}"


def _format_appointment_summary(parsed: dict) -> str:
    when = _format_ist(parsed.get("appointment_at"))
    doctor = parsed.get("provider_name") or parsed.get("department") or "doctor"
    location = parsed.get("location") or "location not recorded"
    return f"{when} with {doctor} at {location}"


def _format_ist(value: Any) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        return "time not recorded"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(IST).strftime("%d %b %Y, %I:%M %p IST").lstrip("0")


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def _reminder_bucket(value: Any) -> str | None:
    appointment_at = _parse_datetime(value)
    if appointment_at is None:
        return None
    if appointment_at.tzinfo is None:
        appointment_at = appointment_at.replace(tzinfo=timezone.utc)
    hours = (appointment_at.astimezone(IST) - datetime.now(IST)).total_seconds() / 3600
    if hours <= 0:
        return None
    if hours <= 3:
        return "same_day"
    if hours <= 24:
        return "24h"
    if hours <= 72:
        return "72h"
    return None


def _reminder_message(appointment: dict, bucket: str) -> str:
    when = _format_ist(appointment.get("appointment_at"))
    doctor = appointment.get("provider_name") or appointment.get("department") or appointment.get("title") or "doctor"
    location = appointment.get("location") or "location not recorded"
    prefix = {
        "72h": "Appointment reminder: within 72 hours.",
        "24h": "Appointment tomorrow/within 24 hours.",
        "same_day": "Appointment today soon.",
    }.get(bucket, "Appointment reminder.")
    return f"{prefix} {when} with {doctor} at {location}. Reply YES if going, NO if cancelling."


def _pending_appointment_id(context: dict | None) -> str | None:
    payload = (context or {}).get("payload") if isinstance((context or {}).get("payload"), dict) else {}
    return (context or {}).get("target_id") or payload.get("appointment_id") or payload.get("entity_id")


def _merge_draft_message(message: str, context: dict | None) -> str:
    if not context or context.get("type") != "appointment_draft":
        return message
    payload = context.get("payload") if isinstance(context.get("payload"), dict) else {}
    previous = str(payload.get("raw_text") or "").strip()
    current = str(message or "").strip()
    return " ".join(part for part in (previous, current) if part)


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").lower()).strip()


def _contains_any(text: str, terms: list[str]) -> bool:
    return any(term in text for term in terms)


def _json_safe_parsed(parsed: dict) -> dict:
    safe = dict(parsed)
    if isinstance(safe.get("appointment_at"), datetime):
        safe["appointment_at"] = safe["appointment_at"].isoformat()
    return safe
