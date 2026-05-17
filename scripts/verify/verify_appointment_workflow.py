from __future__ import annotations

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from scripts.verify import _bootstrap  # noqa: F401

from datetime import datetime, timedelta, timezone

import appointment_manager
import db
from router import get_final_intent


IST = timezone(timedelta(hours=5, minutes=30))
TEST_PHONE = "+919876543211"


def _profile(patient_id: str) -> dict:
    profile = db.get_profile_by_phone(TEST_PHONE) or {}
    profile.setdefault("phone", TEST_PHONE)
    profile.setdefault("role", "primary_caregiver")
    profile.setdefault("patient_id", patient_id)
    profile.setdefault("patient_name", db.get_patient_name(patient_id) or "Patient")
    return profile


def main() -> None:
    print("=== APPOINTMENT WORKFLOW VERIFICATION ===")
    assert db.ensure_appointment_workflow_schema() is True
    patient_ids = db.get_active_patient_ids()
    assert patient_ids, "No active patient found for verification"
    patient_id = patient_ids[0]
    profile = _profile(patient_id)

    examples = {
        "When is Dad's next cardiology follow-up?": "appointment_query",
        "Doctor kab ka appointment hai?": "appointment_query",
        "Show me all upcoming doctor visits": "appointment_query",
        "cardiology appointment on 30th may 2026": "appointment_add",
        "create cardiology appointment on 30th may 2026": "appointment_add",
        "create cardiac appointment on 30th may 2026": "appointment_add",
        "Book appointment with Dr. Sharma on 25th March at 10 AM": "appointment_add",
        "Schedule cardiology checkup for next Monday": "appointment_add",
    }
    for message, expected in examples.items():
        intent, confidence, source = get_final_intent(message, patient_id=patient_id, profile_id=profile.get("id"))
        assert intent == expected, (message, intent, expected, confidence, source)
    print("Intent routing: OK")

    appointment_at = datetime.now(IST) + timedelta(days=5)
    appointment_id = db.create_care_appointment(
        patient_id=patient_id,
        appointment_type="doctor",
        title="VERIFICATION cardiology follow-up",
        appointment_at=appointment_at.isoformat(),
        location="Verification Clinic",
        provider_name="Dr. Verify",
        department="cardiology",
        notes="Temporary verification row",
        status="scheduled",
    )
    assert appointment_id, "Appointment insert failed"
    try:
        query_reply = appointment_manager.query_appointments(profile, "appointments in next 10 days")
        assert "VERIFICATION cardiology follow-up" in query_reply or "Dr. Verify" in query_reply, query_reply
        print("Appointment query DB fetch: OK")

        general_reply = appointment_manager.query_appointments(profile, "when is the next general checkup for uncle")
        if "General Checkup" in general_reply:
            assert "Blood Panel Test" not in general_reply, general_reply
            assert "Physiotherapy Session" not in general_reply, general_reply
        blood_reply = appointment_manager.query_appointments(profile, "when is the next blood panel test")
        if "Blood Panel Test" in blood_reply:
            assert "MRI Brain Scan" not in blood_reply, blood_reply
        print("Appointment query filtering: OK")

        pending_id = db.create_appointment_confirmation_task(
            patient_id,
            appointment_id,
            profile.get("phone"),
            expires_at=appointment_at,
        )
        assert pending_id, "Appointment confirmation task not created"
        pending = db.get_pending_task_for_phone(profile.get("phone"))
        assert pending and pending.get("type") == "appointment_confirm", pending
        yes_intent, _, _ = get_final_intent(
            "YES",
            pending_context=pending,
            patient_id=patient_id,
            profile_id=profile.get("id"),
        )
        assert yes_intent == "appointment_confirmed", yes_intent
        confirmed = appointment_manager.handle_appointment_message(profile, "Yes will go", "appointment_confirmed", pending)
        assert "confirmed" in confirmed.lower(), confirmed
        print("Appointment confirmation context: OK")

        draft_reply = appointment_manager.add_appointment_from_message(
            profile,
            "create cardiology appointment on 30th may 2026",
        )
        assert "need time" in draft_reply.lower(), draft_reply
        draft_context = db.get_pending_task_for_phone(profile.get("phone"))
        assert draft_context and draft_context.get("type") == "appointment_draft", draft_context
        completed_reply = appointment_manager.handle_appointment_message(
            profile,
            "10 AM",
            "appointment_add",
            draft_context,
        )
        assert "appointment saved" in completed_reply.lower(), completed_reply
        for item in db.get_care_appointments(
            patient_id,
            start_at="2026-05-30T00:00:00+05:30",
            end_at="2026-05-31T00:00:00+05:30",
            statuses=["scheduled", "confirmed", "tentative"],
            limit=20,
        ):
            if item.get("department") == "cardiology":
                db.delete_care_appointment(item["id"])
        print("Appointment draft completion: OK")

        cardiac = appointment_manager.parse_appointment_details("create cardiac appointment on 30th may 2026 2:00 PM")
        assert cardiac["department"] == "cardiology", cardiac
        assert cardiac["title"] == "cardiology", cardiac
        print("Cardiac specialty mapping: OK")

        reminder_id = db.create_care_appointment(
            patient_id=patient_id,
            appointment_type="doctor",
            title="VERIFICATION reminder appointment",
            appointment_at=(datetime.now(IST) + timedelta(hours=48)).isoformat(),
            location="Reminder Clinic",
            provider_name="Dr. Reminder",
            department="general",
            notes="Temporary reminder verification row",
            status="scheduled",
        )
        assert reminder_id, "Reminder appointment insert failed"
        try:
            due = db.get_appointments_due_for_reminder(hours=72, limit=100)
            assert any(item.get("id") == reminder_id for item in due), due
            print("Reminder candidate fetch: OK")
        finally:
            db.delete_care_appointment(reminder_id)
    finally:
        db.delete_care_appointment(appointment_id)

    print("APPOINTMENT_WORKFLOW_VERIFICATION_PASS")


if __name__ == "__main__":
    main()
