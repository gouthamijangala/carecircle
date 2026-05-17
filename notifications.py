from datetime import datetime, timedelta, timezone

import crisis
import config
import db
import notification_dispatcher


NOTIFICATION_COOLDOWN_SECONDS = 300  # 5 minutes
MAX_CAREGIVER_MESSAGE_CHARS = 1200


def _format_time_short(value: str) -> str:
    try:
        parsed = datetime.strptime(str(value).strip(), "%H:%M")
        if parsed.minute == 0:
            return parsed.strftime("%I%p").lstrip("0")
        return parsed.strftime("%I:%M%p").lstrip("0")
    except Exception:
        return str(value or "").strip()


def _format_times_short(times) -> str:
    try:
        formatted = [_format_time_short(time_value) for time_value in list(times or []) if str(time_value).strip()]
        return ", ".join(formatted)
    except Exception:
        return ""


def _short_frequency(frequency: str) -> str:
    try:
        value = str(frequency or "").strip().lower()
        if value in {"bid", "bd", "twice daily", "twice a day", "2 times daily"}:
            return "BID"
        if value in {"tds", "tid", "three times daily", "three times a day", "3 times daily"}:
            return "TDS"
        if value in {"od", "qd", "once daily", "once a day", "daily"}:
            return "QD"
        if value in {"hs", "bedtime", "night", "at night", "raat"}:
            return "HS"
        return str(frequency or "").strip().upper()
    except Exception:
        return ""


def _format_medication_short(medication: dict) -> str:
    try:
        drug = medication.get("drug_name") or "Unknown"
        dose = medication.get("dose_amount") or ""
        unit = medication.get("dose_unit") or ""
        frequency = _short_frequency(medication.get("frequency") or "")
        times = _format_times_short(medication.get("scheduled_times"))
        base = f"{drug} {dose}{unit} {frequency}".strip()
        return f"{base} ({times})" if times else base
    except Exception:
        return "Unknown medication"


def _format_lab(lab: dict) -> str:
    try:
        name = lab.get("test_name") or "Lab"
        value = lab.get("value")
        unit = lab.get("unit") or ""
        low = lab.get("reference_range_low")
        high = lab.get("reference_range_high")
        value_text = f"{value:g}" if isinstance(value, float) else str(value)
        ref_text = ""
        if low is not None and high is not None:
            low_text = f"{low:g}" if isinstance(low, float) else str(low)
            high_text = f"{high:g}" if isinstance(high, float) else str(high)
            ref_text = f" (ref {low_text}-{high_text})"
        return f"{name}: {value_text} {unit}{ref_text}".strip()
    except Exception:
        return "Lab result available"


def _truncate_word(text: str, max_chars: int) -> str:
    try:
        if len(text) <= max_chars:
            return text
        shortened = text[: max_chars - 1].rstrip()
        if " " in shortened:
            shortened = shortened.rsplit(" ", 1)[0]
        return shortened.rstrip(" ,.;:") + "..."
    except Exception:
        return text[:max_chars]


def _assemble_message(header: str, crisis_text: str, med_lines: list[str], lab_lines: list[str]) -> str:
    sections = [
        header,
        crisis_text,
        "Active Medications:",
        *(f"   - {line}" for line in med_lines),
    ]
    if lab_lines:
        sections.extend(["", "Recent Labs:", *(f"   - {line}" for line in lab_lines)])
    return "\n".join(sections)


def build_caregiver_crisis_message(patient_name: str, patient_id: str) -> str:
    """
    Build a compact crisis notification message for caregivers.
    """
    try:
        packet = crisis.get_emergency_packet(patient_id)
        crisis_text = crisis.format_crisis_card(packet)
        active_meds = db.get_active_medications_schedule(patient_id)
        recent_labs = db.get_recent_labs(patient_id, limit=2)

        header = f"EMERGENCY ALERT - {patient_name}"
        med_lines = [_format_medication_short(medication) for medication in active_meds]
        if not med_lines:
            med_lines = ["No active medications recorded"]
        lab_lines = [_format_lab(lab) for lab in recent_labs]

        message = _assemble_message(header, crisis_text, med_lines, lab_lines)
        while len(message) > MAX_CAREGIVER_MESSAGE_CHARS and lab_lines:
            lab_lines.pop()
            message = _assemble_message(header, crisis_text, med_lines, lab_lines)

        while len(message) > MAX_CAREGIVER_MESSAGE_CHARS and len(med_lines) > 1:
            med_lines.pop()
            message = _assemble_message(header, crisis_text, med_lines, lab_lines)

        return _truncate_word(message, MAX_CAREGIVER_MESSAGE_CHARS)
    except Exception:
        return _truncate_word(
            f"EMERGENCY ALERT - {patient_name}\nPlease contact the caregiver team immediately.",
            MAX_CAREGIVER_MESSAGE_CHARS,
        )


def _recent_notification_logged(patient_id: str) -> bool:
    """
    Use audit_log for notification cooldown so crisis-card cache refreshes do not
    suppress the first actual caregiver notification.
    """
    connection = None
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=NOTIFICATION_COOLDOWN_SECONDS)
        connection = db._connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM audit_log
                WHERE patient_id = %s
                  AND entity_type = 'crisis_notification'
                  AND action = 'caregiver_notified'
                  AND timestamp > %s
                LIMIT 1;
                """,
                (patient_id, cutoff),
            )
            return cursor.fetchone() is not None
    except Exception:
        return False
    finally:
        if connection is not None:
            connection.close()


def send_caregiver_notifications(
    patient_id: str,
    patient_name: str,
    force: bool = False,
    trigger_message: str | None = None,
    triggered_by_phone: str | None = None,
) -> list[dict]:
    """
    Log caregiver crisis notifications and return notification status rows.
    In Phase 1 local mode, "send" means a durable audit_log alert record that
    the future WhatsApp/Twilio sender can consume.
    """
    try:
        caregivers = db.get_caregivers(patient_id)
        message = build_caregiver_crisis_message(patient_name, patient_id)
        notifications = []

        if not force and _recent_notification_logged(patient_id):
            return [
                {
                    "name": caregiver.get("name"),
                    "role": caregiver.get("role"),
                    "phone": caregiver.get("phone"),
                    "notification_status": "already_logged_recently",
                    "alert_text": message,
                }
                for caregiver in caregivers
            ]

        for caregiver in caregivers:
            try:
                outbox_id = None
                if getattr(config, "NOTIFICATION_OUTBOX_ENABLED", True):
                    outbox_id = db.enqueue_notification_outbox(
                        patient_id=patient_id,
                        to_phone=caregiver.get("phone"),
                        message=message,
                        channel="web",
                        priority="critical" if force else "high",
                        payload={
                            "caregiver_name": caregiver.get("name"),
                            "caregiver_role": caregiver.get("role"),
                            "trigger_message": trigger_message,
                            "triggered_by_phone": triggered_by_phone,
                        },
                    )
                if force or str(caregiver.get("role") or "").lower().replace(" ", "_") == "primary_caregiver":
                    notification_dispatcher.dispatch_user_message(
                        caregiver.get("phone"),
                        message,
                        patient_id=patient_id,
                        priority="critical" if force else "high",
                    )
                db.write_audit(
                    patient_id=patient_id,
                    profile_id=None,
                    entity_type="crisis_notification",
                    entity_id=None,
                    action="caregiver_notified",
                    actor_role="system",
                    new_value={
                        "caregiver_name": caregiver.get("name"),
                        "caregiver_role": caregiver.get("role"),
                        "caregiver_phone": caregiver.get("phone"),
                        "message_length": len(message),
                        "message": message,
                        "trigger_message": trigger_message,
                        "triggered_by_phone": triggered_by_phone,
                        "delivery_channel": "phase1_audit_log",
                        "outbox_id": outbox_id,
                    },
                )
            except Exception as error:
                print(f"Audit log failed for {caregiver.get('name')}: {error}")
                continue

            notifications.append(
                {
                    "name": caregiver.get("name"),
                    "role": caregiver.get("role"),
                    "phone": caregiver.get("phone"),
                    "notification_status": "logged",
                    "alert_text": message,
                    "outbox_id": outbox_id,
                }
            )

        return notifications
    except Exception as error:
        print(error)
        return []
