"""
Notification dispatcher facade.

Phase 1 delivery remains audit-log backed, matching the current system. The
facade gives later WhatsApp/SMS/email senders one clean integration point.
"""

from __future__ import annotations

from typing import Any

import config
import db


def _dispatch_with_apprise(message: str, priority: str | None = None) -> dict[str, Any]:
    if not getattr(config, "APPRISE_ENABLED", False):
        return {"status": "disabled", "channel": "apprise", "reason": "APPRISE_ENABLED is false"}
    urls = list(getattr(config, "APPRISE_URLS", []) or [])
    if not urls:
        return {"status": "disabled", "channel": "apprise", "reason": "APPRISE_URLS is empty"}
    if getattr(config, "APPRISE_CRITICAL_ONLY", True) and str(priority or "").lower() != "critical":
        return {"status": "skipped", "channel": "apprise", "reason": "non_critical"}
    try:
        import apprise

        client = apprise.Apprise()
        for url in urls:
            client.add(url)
        ok = client.notify(
            title="CareCircle medication safety alert",
            body=message[:1000],
        )
        return {"status": "sent" if ok else "failed", "channel": "apprise"}
    except Exception as error:
        return {"status": "failed", "channel": "apprise", "error": str(error)}


def dispatch_user_message(
    to_phone: str | None,
    message: str,
    profile_id: str | None = None,
    patient_id: str | None = None,
    priority: str | None = None,
) -> dict[str, Any]:
    try:
        if not getattr(config, "NOTIFICATION_DISPATCH_ENABLED", True):
            return {"status": "disabled", "channel": "none"}
        outbox_id = None
        if getattr(config, "NOTIFICATION_OUTBOX_ENABLED", True):
            outbox_id = db.enqueue_notification_outbox(
                patient_id=patient_id,
                to_phone=to_phone,
                message=message,
                channel="web",
                priority=priority or ("high" if "critical" in str(message).lower() else "normal"),
                payload={"profile_id": profile_id, "audit_only": getattr(config, "NOTIFICATION_AUDIT_ONLY", True)},
                profile_id=profile_id,
            )
        apprise_result = _dispatch_with_apprise(message, priority)
        if outbox_id and apprise_result.get("status") in {"sent", "failed"}:
            db.update_notification_outbox_status(
                outbox_id,
                apprise_result["status"],
                apprise_result.get("error") or apprise_result.get("reason"),
            )
        elif outbox_id and getattr(config, "NOTIFICATION_AUDIT_ONLY", True):
            db.update_notification_outbox_status(
                outbox_id,
                "logged",
                apprise_result.get("reason"),
            )
        db.log_incoming_message(
            from_phone="system",
            body=f"[to {to_phone or 'unknown'}] {message}",
            num_media=0,
            media_url=None,
            media_type=None,
            profile_id=profile_id,
        )
        status = "sent" if apprise_result.get("status") == "sent" else "logged"
        return {
            "status": status,
            "channel": "audit_log",
            "outbox_id": outbox_id,
            "provider": apprise_result,
        }
    except Exception as error:
        return {"status": "failed", "error": str(error), "channel": "audit_log"}


def dispatch_crisis(patient_id: str, patient_name: str, trigger_message: str = "", from_phone: str | None = None) -> dict:
    import alerting

    rows = alerting.send_crisis_alerts(
        patient_id=patient_id,
        patient_name=patient_name,
        trigger_message=trigger_message,
        triggered_by_phone=from_phone,
        force=True,
    )
    return {
        "status": "logged",
        "summary": alerting.summarize_notifications(rows),
        "notifications": rows,
    }
