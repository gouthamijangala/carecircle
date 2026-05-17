try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from scripts.verify import _bootstrap  # noqa: F401

from datetime import datetime, timedelta

from crisis import build_crisis_card, format_crisis_card
from handlers import route_message
from intent import classify_intent, is_valid_emergency, normalize_message


PATIENT_ID = "d0000001-0002-0001-0001-000000000001"


def assert_intent(message: str, expected: str) -> None:
    actual = classify_intent(message)
    assert actual == expected, f"{message!r}: expected {expected}, got {actual}"


def assert_emergency(message: str, expected_reason: str | None = None) -> None:
    is_valid, reason = is_valid_emergency(message)
    assert is_valid, f"{message!r}: expected emergency, got {reason}"
    if expected_reason is not None:
        assert reason == expected_reason, f"{message!r}: expected {expected_reason}, got {reason}"
    assert classify_intent(message) == "crisis", f"{message!r}: classifier did not return crisis"


def main() -> None:
    profile = {
        "id": "d0000001-0001-0001-0002-000000000002",
        "phone": "+919876543211",
        "full_name": "Meera Sharma",
        "role": "primary_caregiver",
        "patient_id": PATIENT_ID,
        "patient_name": "Rajesh Sharma",
    }
    patient_profile = {
        "id": "d0000001-0001-0001-0001-000000000001",
        "phone": "+919876543210",
        "full_name": "Rajesh Sharma",
        "role": "patient",
        "patient_id": PATIENT_ID,
        "patient_name": "Rajesh Sharma",
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

    for message in ["approve", "veto", "reject"]:
        assert_intent(message, "approval_context_missing")
        routed = route_message(message, profile)
        assert routed["intent"] == "approval_context_missing", routed
        assert "active 3-minute" in routed["reply"], routed

    assert classify_intent("approve", active_approval) == "approve_command"
    assert route_message("approve", profile, active_approval)["intent"] == "approve_command"
    assert route_message("approve", patient_profile, active_approval)["intent"] == "approval_not_authorized"
    assert classify_intent("approve", expired_approval) == "approval_context_expired"
    assert "expired" in route_message("approve", profile, expired_approval)["reply"].lower()

    assert_intent("hi", "greeting")
    for message in [
        "heart attack",
        "hert attak",
        "chest pain",
        "emergency",
        "ambulanse bulao",
        "ambulance",
        "liver failure",
        "died",
    ]:
        assert_emergency(message)

    assert_emergency("someone came to kill me", "violence_threat")
    assert_emergency("some one raped my rani", "sexual_assault")
    assert_emergency("mai build pe sey kudh liya", "self_harm_risk")
    assert "bridge par se kood" in normalize_message("mai build pe sey kudh liya")

    for message in ["no medications all today", "nhi li", "skipped"]:
        assert_intent(message, "medication_report")

    for message in ["what is my present tablets to be taken", "what should I take now"]:
        assert_intent(message, "medication_query")

    for message in ["appointment", "follow-up", "appoinment"]:
        assert_intent(message, "appointment_query")

    crisis_reply = route_message("heart attack", profile)
    assert crisis_reply["intent"] == "crisis", crisis_reply
    assert crisis_reply["confidence"] == 1.0, crisis_reply
    assert "EMERGENCY CARD" in crisis_reply["reply"], crisis_reply

    card = build_crisis_card(PATIENT_ID, "Rajesh Sharma")
    caregiver_roles = {caregiver.get("role") for caregiver in card.get("caregivers", [])}
    assert "doctor" not in caregiver_roles, card
    assert (card.get("doctor") or {}).get("name") != "N/A", card
    assert (card.get("hospital") or {}).get("maps_link"), card
    assert (
        "openstreetmap.org" in card["hospital"]["maps_link"]
        or "maps/search" in card["hospital"]["maps_link"]
    ), card
    formatted = format_crisis_card(card)
    assert len(formatted) <= 800, len(formatted)

    print("HYBRID_ROUTING_VERIFICATION_PASS")


if __name__ == "__main__":
    main()
