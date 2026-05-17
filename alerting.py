"""
Unified alert creation and notification helpers.

All functions are safe wrappers: failures are logged to stdout/audit where
possible and never interrupt the patient-facing message path.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import config
import db
import notifications


def _recent_audit_exists(patient_id: str, entity_type: str, action: str, seconds: int) -> bool:
    connection = None
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=max(1, int(seconds)))
        connection = db._connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM audit_log
                WHERE patient_id = %s
                  AND entity_type = %s
                  AND action = %s
                  AND timestamp > %s
                LIMIT 1;
                """,
                (patient_id, entity_type, action, cutoff),
            )
            return cursor.fetchone() is not None
    except Exception:
        return False
    finally:
        if connection is not None:
            connection.close()


def create_alert_safe(
    patient_id: str | None,
    alert_type: str,
    severity: str,
    message: str,
    data_payload: dict | None = None,
    dedupe_seconds: int | None = None,
) -> str | None:
    if not patient_id:
        return None
    action = f"ALERT_CREATED:{alert_type}:{severity}"
    if dedupe_seconds and _recent_audit_exists(str(patient_id), "alerting", action, dedupe_seconds):
        return None

    alert_id = db.create_alert(patient_id, alert_type, severity, message, data_payload or {})
    try:
        db.write_audit(
            patient_id=patient_id,
            profile_id=None,
            entity_type="alerting",
            entity_id=alert_id,
            action=action,
            actor_role="system",
            new_value={
                "alert_type": alert_type,
                "severity": severity,
                "message": message,
                "data_payload": data_payload or {},
            },
        )
    except Exception:
        pass
    return alert_id


def send_crisis_alerts(
    patient_id: str,
    patient_name: str,
    trigger_message: str | None = None,
    triggered_by_phone: str | None = None,
    force: bool = True,
) -> list[dict[str, Any]]:
    if not getattr(config, "NOTIFICATION_DISPATCH_ENABLED", True):
        return []

    create_alert_safe(
        patient_id=patient_id,
        alert_type="crisis_emergency",
        severity="critical",
        message="Emergency/crisis message received. Caregiver review required immediately.",
        data_payload={
            "trigger_message": trigger_message,
            "triggered_by_phone": triggered_by_phone,
            "dispatch_mode": "audit_log" if getattr(config, "NOTIFICATION_AUDIT_ONLY", True) else "external",
        },
        dedupe_seconds=int(getattr(config, "CRISIS_ALERT_DEDUPE_SECONDS", 300)),
    )

    return notifications.send_caregiver_notifications(
        patient_id,
        patient_name,
        force=force,
        trigger_message=trigger_message,
        triggered_by_phone=triggered_by_phone,
    )


def summarize_notifications(rows: list[dict] | None) -> dict[str, Any]:
    items = list(rows or [])
    return {
        "count": len(items),
        "sent_or_logged": sum(1 for item in items if item.get("notification_status") in {"logged", "sent"}),
        "recently_suppressed": sum(1 for item in items if item.get("notification_status") == "already_logged_recently"),
    }
