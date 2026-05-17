"""
Daily caregiver brief scheduler.

The 10AM day brief and 10PM night summary are built only from live DB reads.
Missing optional tables produce clear fallback text instead of hardcoded claims.
"""

from __future__ import annotations

import threading
import time
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

import config
import db
import notification_dispatcher
from nlp_deterministic import assemble_care_summary, compute_adherence_snapshot


_started = False
_scheduler = None
_IST = timezone(timedelta(hours=5, minutes=30))


def start_daily_summary_scheduler() -> bool:
    global _started, _scheduler
    if _started or not getattr(config, "DAILY_SUMMARY_ENABLED", True):
        return False
    _started = True
    try:
        from apscheduler.schedulers.background import BackgroundScheduler

        scheduler = BackgroundScheduler(timezone="Asia/Kolkata")
        scheduler.add_job(
            send_all_day_briefs,
            "cron",
            hour=_brief_hour("day"),
            minute=0,
            id="carecircle_day_brief_10am",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            send_all_night_summaries,
            "cron",
            hour=_brief_hour("night"),
            minute=0,
            id="carecircle_night_summary_10pm",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.start()
        _scheduler = scheduler
        return True
    except Exception as error:
        print(f"APScheduler unavailable; using thread scheduler fallback: {error}")
        threading.Thread(target=_daily_brief_loop, daemon=True).start()
        return True


def send_all_day_briefs() -> dict:
    return _send_all("day_brief")


def send_all_night_summaries() -> dict:
    return _send_all("night_summary")


def send_daily_summary(patient_id: str, force: bool = False, brief_type: str = "day_brief") -> dict:
    """
    Backward-compatible entrypoint. Defaults to the 10AM day brief.
    """
    normalized_type = _normalize_brief_type(brief_type)
    try:
        if not force and db.daily_summary_sent_today(patient_id, normalized_type):
            return {"status": "skipped", "reason": "already_sent_today", "patient_id": patient_id, "brief_type": normalized_type}

        context = build_brief_context(patient_id)
        target = _primary_caregiver(context["caregivers"])
        if not target.get("phone"):
            return {"status": "skipped", "reason": "no_caregiver_phone", "patient_id": patient_id, "brief_type": normalized_type}

        message = (
            format_day_brief(context)
            if normalized_type == "day_brief"
            else format_night_summary(context)
        )
        delivery = notification_dispatcher.dispatch_user_message(
            target.get("phone"),
            message,
            patient_id=patient_id,
            priority="normal",
        )
        audit_payload = {
            "brief_type": normalized_type,
            "phone": target.get("phone"),
            "message": message,
            "context": _brief_audit_context(context),
            "delivery": delivery,
        }
        db.write_audit(
            patient_id=patient_id,
            profile_id=None,
            entity_type="daily_summary",
            entity_id=None,
            action="DAILY_CARE_SUMMARY_SENT",
            actor_role="system",
            new_value=audit_payload,
        )
        return {
            "status": "sent",
            "patient_id": patient_id,
            "brief_type": normalized_type,
            "delivery": delivery,
            "message": message,
        }
    except Exception as error:
        return {"status": "error", "patient_id": patient_id, "brief_type": normalized_type, "error": str(error)}


def build_brief_context(patient_id: str) -> dict:
    today = date.today()
    yesterday = today - timedelta(days=1)
    active_meds = db.get_active_medications_schedule(patient_id)
    yesterday_log = db.get_medication_log_for_date(patient_id, yesterday)
    today_log = db.get_medication_log_for_date(patient_id, today)
    recent_vitals = db.get_recent_vitals(patient_id)
    recent_labs = db.get_recent_labs(patient_id, limit=10)
    open_alerts = db.get_open_alerts(patient_id)
    pending_approvals = [
        item for item in db.get_agent_approvals_for_patient(patient_id, limit=10)
        if str(item.get("status") or "").lower() == "pending"
    ]
    pending_approvals_total = db.get_pending_agent_approval_count(patient_id)
    meds_awaiting_review = db.get_medications_awaiting_review(patient_id, limit=8)
    meds_awaiting_review_total = db.get_medications_awaiting_review_count(patient_id)
    doctor_appointments = [
        item for item in db.get_upcoming_appointments(patient_id, days=14, limit=8)
        if not _is_test_appointment(item)
    ]
    test_appointments = db.get_upcoming_test_appointments(patient_id, days=14, limit=5)
    caregiver_visits = db.get_upcoming_caregiver_visits(patient_id, days=7, limit=5)
    adherence_yesterday = compute_adherence_snapshot(patient_id, yesterday_log, active_meds, yesterday)
    adherence_today = compute_adherence_snapshot(patient_id, today_log, active_meds, today)
    care_summary = assemble_care_summary(patient_id, active_meds, recent_vitals, recent_labs, open_alerts, adherence_today)
    return {
        "patient_id": patient_id,
        "patient_name": db.get_patient_name(patient_id) or "Patient",
        "today": today.isoformat(),
        "yesterday": yesterday.isoformat(),
        "active_meds": active_meds,
        "yesterday_log": yesterday_log,
        "today_log": today_log,
        "adherence_yesterday": adherence_yesterday,
        "adherence_today": adherence_today,
        "recent_vitals": recent_vitals,
        "recent_labs": recent_labs,
        "open_alerts": open_alerts,
        "pending_approvals": pending_approvals,
        "pending_approvals_total": pending_approvals_total,
        "medications_awaiting_review": meds_awaiting_review,
        "medications_awaiting_review_total": meds_awaiting_review_total,
        "doctor_appointments": doctor_appointments,
        "test_appointments": test_appointments,
        "caregiver_visits": caregiver_visits,
        "care_summary": care_summary,
        "caregivers": db.get_caregivers(patient_id),
    }


def format_day_brief(context: dict) -> str:
    patient = context.get("patient_name") or "Patient"
    adherence = _adherence_line(context.get("adherence_yesterday") or {}, label="Yesterday meds")
    doctor = _appointment_line(context.get("doctor_appointments") or [], "No doctor appointments found in DB for the next 14 days.")
    tests = _appointment_line(context.get("test_appointments") or [], "No lab/test appointments found in DB for the next 14 days.")
    approvals = _approvals_line(
        context.get("pending_approvals") or [],
        total_count=context.get("pending_approvals_total"),
    )
    review_meds = _review_meds_line(
        context.get("medications_awaiting_review") or [],
        total_count=context.get("medications_awaiting_review_total"),
    )
    visits = _visits_line(context.get("caregiver_visits") or [])
    alerts = _alerts_line(context.get("open_alerts") or [], compact=False)
    return _truncate_message(
        "\n".join(
            [
                f"10AM CareCircle day brief for {patient}",
                adherence,
                f"Doctor: {doctor}",
                f"Tests: {tests}",
                f"Pending approvals: {approvals}",
                f"Medication review: {review_meds}",
                f"Secondary caregiver visits: {visits}",
                f"Alerts: {alerts}",
                "Reply HELP for menu.",
            ]
        ),
        1100,
    )


def format_night_summary(context: dict) -> str:
    patient = context.get("patient_name") or "Patient"
    today = _adherence_line(context.get("adherence_today") or {}, label="Today meds")
    approvals = _approvals_line(
        context.get("pending_approvals") or [],
        compact=True,
        total_count=context.get("pending_approvals_total"),
    )
    review_meds = _review_meds_line(
        context.get("medications_awaiting_review") or [],
        compact=True,
        total_count=context.get("medications_awaiting_review_total"),
    )
    alerts = _alerts_line(context.get("open_alerts") or [], compact=True)
    next_item = _next_schedule_line(context)
    return _truncate_message(
        "\n".join(
            [
                f"10PM quick summary for {patient}",
                today,
                f"Open alerts: {alerts}",
                f"Pending approvals: {approvals}",
                f"Medication review: {review_meds}",
                f"Next: {next_item}",
                "Good night. Reply HELP for menu.",
            ]
        ),
        700,
    )


def _send_all(brief_type: str) -> dict:
    results = []
    for patient_id in db.get_active_patient_ids():
        results.append(send_daily_summary(patient_id, force=False, brief_type=brief_type))
    return {"status": "completed", "brief_type": brief_type, "patients": len(results), "results": results}


def _daily_brief_loop() -> None:
    interval = max(60, int(getattr(config, "DAILY_SUMMARY_POLLER_INTERVAL_SECONDS", 300)))
    sent_marker: set[tuple[str, str]] = set()
    while True:
        try:
            now = datetime.now()
            today_key = now.date().isoformat()
            if now.hour >= _brief_hour("day") and (today_key, "day_brief") not in sent_marker:
                send_all_day_briefs()
                sent_marker.add((today_key, "day_brief"))
            if now.hour >= _brief_hour("night") and (today_key, "night_summary") not in sent_marker:
                send_all_night_summaries()
                sent_marker.add((today_key, "night_summary"))
            if len(sent_marker) > 8:
                sent_marker = {item for item in sent_marker if item[0] == today_key}
        except Exception:
            pass
        time.sleep(interval)


def _brief_hour(kind: str) -> int:
    key = "DAILY_DAY_BRIEF_HOUR_LOCAL" if kind == "day" else "DAILY_NIGHT_SUMMARY_HOUR_LOCAL"
    fallback = 10 if kind == "day" else 22
    return max(0, min(23, int(getattr(config, key, fallback))))


def _normalize_brief_type(value: str) -> str:
    text = str(value or "").strip().lower()
    if text in {"night", "night_summary", "10pm", "summary"}:
        return "night_summary"
    return "day_brief"


def _primary_caregiver(caregivers: list[dict]) -> dict:
    primary = next(
        (
            caregiver for caregiver in caregivers or []
            if str(caregiver.get("role") or "").lower().replace(" ", "_") == "primary_caregiver"
        ),
        None,
    )
    return primary or ((caregivers or [{}])[0])


def _adherence_line(adherence: dict, label: str) -> str:
    total = int(adherence.get("total_scheduled", 0) or 0)
    taken = int(adherence.get("taken", 0) or 0)
    missed = int(adherence.get("missed", 0) or 0)
    pending = int(adherence.get("pending", 0) or 0)
    if total <= 0:
        return f"{label}: no scheduled medication data found in DB."
    all_taken = taken >= total and missed == 0 and pending == 0
    answer = "YES" if all_taken else "NO"
    pending_label = "not confirmed" if str(label or "").lower().startswith("yesterday") else "pending"
    return f"{label}: {answer} ({taken} taken, {missed} missed, {pending} {pending_label} of {total})."


def _appointment_line(items: list[dict], fallback: str) -> str:
    if not items:
        return fallback
    item = items[0]
    parts = [
        item.get("title") or item.get("appointment_type") or "Appointment",
        _format_ist(item.get("appointment_at")) or "",
        item.get("location") or "location not recorded",
        item.get("provider_name") or item.get("department") or "provider not recorded",
    ]
    return " | ".join(str(part) for part in parts if part)


def _is_test_appointment(item: dict) -> bool:
    text = " ".join(str(item.get(key) or "").lower() for key in ("appointment_type", "title", "department", "notes"))
    return any(term in text for term in ("lab", "test", "scan", "xray", "x-ray", "mri", "ct", "blood", "diagnostic"))


def _approvals_line(items: list[dict], compact: bool = False, total_count: int | None = None) -> str:
    total = int(total_count if total_count is not None else len(items))
    if total <= 0:
        return "none found in DB."
    if compact:
        return f"{total} pending."
    if not items:
        return f"{total} pending; details not loaded."
    first = items[0]
    pair = f"{first.get('drug_a')} + {first.get('drug_b')}".strip(" +")
    expiry = _format_ist(first.get("veto_expiry")) or "expiry not recorded"
    showing = f"; showing first of {total}" if total > len(items) else ""
    return f"{total} pending{showing}; first: {pair} ({first.get('severity', 'unknown')}) until {expiry}."


def _review_meds_line(items: list[dict], compact: bool = False, total_count: int | None = None) -> str:
    total = int(total_count if total_count is not None else len(items))
    if total <= 0:
        return "none awaiting review."
    if compact:
        return f"{total} awaiting review."
    if not items:
        return f"{total} awaiting review; details not loaded."
    first = items[0]
    dose = " ".join(
        str(part).strip()
        for part in (first.get("dose_amount"), first.get("dose_unit"), first.get("frequency"))
        if str(part or "").strip()
    )
    detail = f"{first.get('drug_name') or 'Unknown medication'}"
    if dose:
        detail = f"{detail} {dose}"
    status = str(first.get("status") or "review").replace("_", " ")
    showing = f"; showing latest of {total}" if total > len(items) else ""
    return f"{total} awaiting review{showing}; latest: {detail} ({status})."


def _visits_line(items: list[dict]) -> str:
    if not items:
        return "no secondary caregiver visits found in DB for the next 7 days."
    first = items[0]
    brief = first.get("brief") or first.get("purpose") or "brief not recorded"
    when = _format_ist(first.get("visit_at")) or "time not recorded"
    return f"{first.get('caregiver_name')} at {when} - {brief}"


def _alerts_line(items: list[dict], compact: bool) -> str:
    if not items:
        return "none open."
    severity_counts = _alert_severity_counts(items)
    urgent = [
        item for item in items
        if str(item.get("severity") or "").lower() in {"critical", "high"}
    ]
    if compact:
        return f"{len(items)} open; {len(urgent)} urgent ({_severity_count_text(severity_counts)})."
    fresh_urgent = [item for item in urgent if _is_recent(item.get("created_at"), hours=48)]
    non_crisis = [
        item for item in fresh_urgent
        if "crisis" not in str(item.get("type") or "").lower()
    ]
    first = (non_crisis or fresh_urgent or [None])[0]
    base = f"{len(items)} open ({_severity_count_text(severity_counts)})"
    if first is None:
        return f"{base}; no fresh urgent alert in the last 48 hours."
    when = _format_ist(first.get("created_at"), include_date=False)
    message = _shorten(str(first.get("message") or "review alert").strip(), 120)
    return f"{base}; latest urgent: {first.get('severity', 'medium')} - {message} ({when})."


def _next_schedule_line(context: dict) -> str:
    doctor = context.get("doctor_appointments") or []
    tests = context.get("test_appointments") or []
    visits = context.get("caregiver_visits") or []
    for collection, fallback in (
        (doctor, "doctor appointment"),
        (tests, "test appointment"),
        (visits, "caregiver visit"),
    ):
        if collection:
            item = collection[0]
            when = item.get("appointment_at") or item.get("visit_at") or "time not recorded"
            title = item.get("title") or item.get("caregiver_name") or fallback
            return f"{title} at {_format_ist(when) or when}."
    return "No upcoming appointments or caregiver visits found in DB."


def _brief_audit_context(context: dict) -> dict:
    return {
        "active_meds_count": len(context.get("active_meds") or []),
        "yesterday_log_count": len(context.get("yesterday_log") or []),
        "today_log_count": len(context.get("today_log") or []),
        "open_alerts_count": len(context.get("open_alerts") or []),
        "pending_approvals_count": len(context.get("pending_approvals") or []),
        "pending_approvals_total": context.get("pending_approvals_total", 0),
        "medications_awaiting_review_count": len(context.get("medications_awaiting_review") or []),
        "medications_awaiting_review_total": context.get("medications_awaiting_review_total", 0),
        "doctor_appointments_count": len(context.get("doctor_appointments") or []),
        "test_appointments_count": len(context.get("test_appointments") or []),
        "caregiver_visits_count": len(context.get("caregiver_visits") or []),
    }


def _truncate_message(text: str, limit: int) -> str:
    value = "\n".join(line.strip() for line in str(text or "").splitlines() if line.strip())
    return value if len(value) <= limit else value[: max(0, limit - 3)].rstrip() + "..."


def _shorten(text: str, limit: int) -> str:
    clean = " ".join(str(text or "").split())
    return clean if len(clean) <= limit else clean[: max(0, limit - 3)].rstrip() + "..."


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    if re_match := re.match(r"^(.*[+-]\d{2})(\d{2})$", normalized):
        normalized = f"{re_match.group(1)}:{re_match.group(2)}"
    try:
        return datetime.fromisoformat(normalized)
    except Exception:
        for pattern in ("%Y-%m-%d %H:%M:%S.%f%z", "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(text, pattern)
                return parsed
            except Exception:
                continue
    return None


def _format_ist(value: Any, include_date: bool = True) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    local = parsed.astimezone(_IST)
    return local.strftime("%d %b %Y, %I:%M %p IST" if include_date else "%I:%M %p IST").lstrip("0")


def _is_recent(value: Any, hours: int) -> bool:
    parsed = _parse_datetime(value)
    if parsed is None:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return now - parsed.astimezone(timezone.utc) <= timedelta(hours=hours)


def _alert_severity_counts(items: list[dict]) -> dict[str, int]:
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for item in items:
        severity = str(item.get("severity") or "medium").lower()
        if severity in {"red"}:
            severity = "critical"
        if severity in {"advisory", "yellow"}:
            severity = "medium"
        counts[severity if severity in counts else "medium"] += 1
    return counts


def _severity_count_text(counts: dict[str, int]) -> str:
    parts = [
        f"{counts.get('critical', 0)} critical",
        f"{counts.get('high', 0)} high",
        f"{counts.get('medium', 0)} medium",
    ]
    low = counts.get("low", 0)
    if low:
        parts.append(f"{low} low")
    return ", ".join(parts)
