try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from scripts.verify import _bootstrap  # noqa: F401

import hashlib
import time

import config
import db
import pharma_agent
from ingestion import process_side_effect_lookup
from pharma_agent import PharmaSafetyEngine, _agent_approval_exists, process_new_medication


TEST_ID = "d0000001-0002-0001-0001-000000000001"
TEST_PHONE = "+919876543211"


def _assert_config_enabled() -> None:
    assert getattr(config, "PHARMA_AGENT_ENABLED", False) is True, "PHARMA_AGENT_ENABLED must be True"


def _assert_db_rules_seeded() -> None:
    rules = db.get_all_drug_interactions()
    assert rules, "Expected active rows in drug_interactions"
    has_warfarin_aspirin = any(
        {str(rule.get("drug_a") or "").lower(), str(rule.get("drug_b") or "").lower()}
        == {"warfarin", "aspirin"}
        for rule in rules
    )
    assert has_warfarin_aspirin, "Expected Warfarin + Aspirin interaction rule"


def _assert_engine_loads() -> PharmaSafetyEngine:
    engine = PharmaSafetyEngine()
    assert len(engine.critical_pairs) > 0, "Rule engine loaded no interaction pairs"
    return engine


def _assert_process_new_medication_critical_pair() -> dict:
    original_get_active = db.get_active_medications_schedule
    original_recent = db.is_pharma_decision_recent
    original_create_alert = db.create_alert
    original_recent_pairs = pharma_agent._recent_interaction_alert_pairs
    original_exists = _agent_approval_exists
    alert_calls = []
    try:
        db.get_active_medications_schedule = lambda patient_id: [{"drug_name": "Aspirin"}]
        db.is_pharma_decision_recent = lambda *args, **kwargs: False
        db.create_alert = lambda *args, **kwargs: alert_calls.append((args, kwargs)) or "verify-alert-id"
        pharma_agent._recent_interaction_alert_pairs = lambda patient_id: set()
        globals()["_agent_approval_exists"] = lambda rule_hash: False
        result = process_new_medication(
            TEST_ID,
            "Warfarin",
            5.0,
            "Verification Doctor",
            TEST_PHONE,
            "verify_pharma_agent_critical",
        )
    finally:
        db.get_active_medications_schedule = original_get_active
        db.is_pharma_decision_recent = original_recent
        db.create_alert = original_create_alert
        pharma_agent._recent_interaction_alert_pairs = original_recent_pairs
        globals()["_agent_approval_exists"] = original_exists

    assert result.get("status") == "completed", f"Expected completed, got {result}"
    assert result.get("max_severity") == "critical", f"Expected critical, got {result}"
    approvals = result.get("approvals_created") or []
    alerts = result.get("alerts_created") or []
    assert approvals or alerts or alert_calls, f"Expected approval or alert side effect, got {result}"
    if approvals:
        assert _agent_approval_exists(approvals[0]), f"Expected approval row for {approvals[0]}"
    return result


def _assert_side_effect_hint() -> None:
    result = process_side_effect_lookup(
        TEST_ID,
        "Amlodipine",
        "Dizziness",
        "Dad is feeling dizzy after taking Amlodipine",
    )
    assert result.get("source") == "known_hint", f"Expected known_hint, got {result}"
    assert result.get("reply"), "Expected side-effect reply"


def _assert_idempotency() -> None:
    unique_drug = f"IdempotencyDrug{int(time.time())}"
    result1 = process_new_medication(
        TEST_ID,
        unique_drug,
        1.0,
        "Verification Doctor",
        TEST_PHONE,
        "verify_pharma_agent_idempotency",
    )
    assert result1.get("status") == "completed", f"Expected completed first call, got {result1}"

    decision_hash = (result1.get("evaluation") or {}).get("idempotency_key")
    assert decision_hash, f"Expected decision hash in result, got {result1}"

    db.write_audit(
        patient_id=TEST_ID,
        profile_id=None,
        entity_type="pharma_agent",
        entity_id=None,
        action="PHARMA_AGENT_DECISION",
        actor_role="system",
        new_value={
            "new_drug": unique_drug.lower(),
            "decision_hash": decision_hash,
            "trigger": "verify_pharma_agent_seed",
        },
    )

    result2 = process_new_medication(
        TEST_ID,
        unique_drug,
        1.0,
        "Verification Doctor",
        TEST_PHONE,
        "verify_pharma_agent_idempotency",
    )
    assert result2.get("status") == "skipped", f"Expected skipped second call, got {result2}"


def _assert_agent_approval_idempotency() -> None:
    rule_hash = hashlib.sha256(f"verify:{TEST_ID}:{time.time()}".encode()).hexdigest()
    assert not _agent_approval_exists(rule_hash), "Generated approval hash already exists"


def main() -> None:
    _assert_config_enabled()
    _assert_db_rules_seeded()
    _assert_engine_loads()
    _assert_process_new_medication_critical_pair()
    _assert_side_effect_hint()
    _assert_idempotency()
    _assert_agent_approval_idempotency()
    print("PHARMA_AGENT_VERIFICATION_PASS")


if __name__ == "__main__":
    main()
