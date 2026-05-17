try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from scripts.verify import _bootstrap  # noqa: F401

from datetime import datetime, timedelta

from db import get_profile_by_phone
from handlers import route_message
from intent import classify_intent


def assert_intent(message: str, expected: str, context: dict | None = None) -> None:
    actual = classify_intent(message, context)
    assert actual == expected, f"{message!r}: expected {expected}, got {actual}"


def main() -> None:
    profile = get_profile_by_phone("+919876543210") or {
        "id": "test-profile",
        "phone": "+919876543210",
        "full_name": "Test User",
        "role": "patient",
        "patient_id": "test-patient",
        "patient_name": "Test Patient",
    }
    primary_caregiver = {
        "id": "test-caregiver",
        "phone": "+919876543211",
        "full_name": "Primary Caregiver",
        "role": "primary_caregiver",
        "patient_id": "test-patient",
        "patient_name": "Test Patient",
    }

    active_approval = {
        "type": "veto_window",
        "created_at": datetime.now().isoformat(),
        "active": True,
    }
    expired_approval = {
        "type": "veto_window",
        "created_at": (datetime.now() - timedelta(seconds=181)).isoformat(),
        "active": True,
    }
    med_context = {
        "type": "medication_confirmation",
        "target_id": "test-medication",
        "created_at": datetime.now().isoformat(),
        "active": True,
    }

    for message in ["hi", "hello", "namaste", "help", "what can you do", "who r u", "who are you"]:
        assert_intent(message, "greeting")
        routed = route_message(message, profile)
        assert routed["source"] == "deterministic"
        assert routed["intent"] in {"greeting", "greeting_help"}

    for message in ["nhi li", "nahi li", "skipped", "missed", "not taken", "taken", "took it"]:
        assert_intent(message, "medication_report")
        routed = route_message(message, profile)
        assert "could not identify which scheduled medicine" in routed["reply"]

    assert_intent("nhi li", "medication_missed_confirm", med_context)
    assert_intent("taken", "medication_taken_confirm", med_context)
    assert_intent("approve", "approval_context_missing")
    assert_intent("deny", "approval_context_missing")
    assert_intent("reject", "approval_context_missing")
    assert_intent("veto", "approval_context_missing")
    assert_intent("approve", "approve_command", active_approval)
    assert_intent("veto this", "veto_command", active_approval)
    assert_intent("approve", "approval_context_expired", expired_approval)
    assert route_message("approve", profile, active_approval)["intent"] == "approval_not_authorized"
    assert route_message("approve", primary_caregiver, active_approval)["intent"] == "approve_command"
    assert route_message("approve", primary_caregiver, active_approval)["reply"] == "Decision command received and processed."
    assert "expired" in route_message("approve", primary_caregiver, expired_approval)["reply"].lower()

    for message in [
        "what should I take now",
        "what medicine now",
        "current tablet",
        "present tablet",
        "medicines due now",
        "what medicine at this time",
        "what is my present tablets to be taken",
    ]:
        assert_intent(message, "medication_query")

    for message in [
        "what meds are active",
        "what medicines am I on",
        "what medicines am I taking",
        "list my tablets",
        "current medication list",
        "show current tablets",
    ]:
        assert_intent(message, "status_query")

    for message in ["appointment", "next appointment", "follow-up", "upcoming checkup", "when is my appointment"]:
        assert_intent(message, "appointment_query")
        routed = route_message(message, profile)
        assert (
            "appointment" in routed["reply"].lower()
            or "upcoming" in routed["reply"].lower()
            or "no " in routed["reply"].lower()
        ), routed

    for message in ["chest pain", "emergency", "ambulance", "severe breathing problem", "unconscious", "collapse"]:
        assert_intent(message, "crisis")

    assert route_message("approve", profile)["reply"].startswith("I could not find an active 3-minute")
    assert route_message("nhi li", profile)["reply"].startswith("I understood this as a medicine update")
    print("INTENT_ROUTING_VERIFICATION_PASS")


if __name__ == "__main__":
    main()
