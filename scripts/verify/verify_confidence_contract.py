try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from scripts.verify import _bootstrap  # noqa: F401

from fastapi.testclient import TestClient

from handlers import route_message
from main import app


PATIENT_ID = "d0000001-0002-0001-0001-000000000001"
REQUIRED_KEYS = {
    "reply",
    "intent",
    "confidence",
    "confidence_score",
    "confidence_label",
    "confidence_reason",
    "classifier_confidence",
    "classifier_reason",
    "normalized_text",
    "source",
    "layer",
}


def assert_confidence_envelope(response: dict) -> None:
    missing = REQUIRED_KEYS - set(response)
    assert not missing, f"Missing confidence keys: {missing} in {response}"
    assert isinstance(response["reply"], str) and response["reply"], response
    assert 0.0 <= float(response["confidence"]) <= 1.0, response
    assert 0.0 <= float(response["confidence_score"]) <= 1.0, response
    assert 0.0 <= float(response["classifier_confidence"]) <= 1.0, response
    assert response["confidence_label"] in {"high", "medium", "low", "none"}, response
    assert response["confidence_reason"], response
    assert response["classifier_reason"], response


def main() -> None:
    profile = {
        "id": "d0000001-0001-0001-0002-000000000002",
        "phone": "+919876543211",
        "full_name": "Meera Sharma",
        "role": "primary_caregiver",
        "patient_id": PATIENT_ID,
        "patient_name": "Rajesh Sharma",
    }

    for message in [
        "hi",
        "approve",
        "heart attack",
        "no medications all today",
        "what should I take now",
        "appointment",
        "random unclear message",
    ]:
        assert_confidence_envelope(route_message(message, profile))

    client = TestClient(app)
    api_response = client.post(
        "/api/send",
        json={"phone": "+919876543211", "message": "heart attack"},
    ).json()
    for key in REQUIRED_KEYS - {"layer"}:
        assert key in api_response, f"API missing {key}: {api_response}"
    assert 0.0 <= float(api_response["confidence"]) <= 1.0, api_response
    assert 0.0 <= float(api_response["classifier_confidence"]) <= 1.0, api_response
    print("CONFIDENCE_CONTRACT_PASS")


if __name__ == "__main__":
    main()
