print("=== LAYERS 4-6 VERIFICATION ===")

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from scripts.verify import _bootstrap  # noqa: F401

import db
import config
import daily_summary
import pharma_research


TEST_PATIENT_ID = "d0000001-0002-0001-0001-000000000001"


assert config.DAILY_DAY_BRIEF_HOUR_LOCAL == 10, "10AM day brief hour not configured"
assert config.DAILY_NIGHT_SUMMARY_HOUR_LOCAL == 22, "10PM night summary hour not configured"
print("Scheduler config: OK")

assert db.ensure_notification_outbox() is True, "notification_outbox not ready"
assert db.ensure_care_coordination_tables() is True, "care coordination tables not ready"
print("DB table setup: OK")

context = daily_summary.build_brief_context(TEST_PATIENT_ID)
required_keys = {
    "active_meds",
    "adherence_yesterday",
    "adherence_today",
    "doctor_appointments",
    "test_appointments",
    "caregiver_visits",
    "pending_approvals",
}
assert required_keys.issubset(context), context.keys()
print("DB input fetch: OK")

day_msg = daily_summary.format_day_brief(context)
night_msg = daily_summary.format_night_summary(context)
assert "10AM CareCircle day brief" in day_msg
assert "Yesterday meds:" in day_msg
assert "Doctor:" in day_msg
assert "Tests:" in day_msg
assert "Pending approvals:" in day_msg
assert "Secondary caregiver visits:" in day_msg
assert "10PM quick summary" in night_msg
assert "Today meds:" in night_msg
print("Brief templates: OK")

outbox_id = db.enqueue_notification_outbox(
    TEST_PATIENT_ID,
    "+910000000000",
    "VERIFY layers 4-6 daily brief outbox",
    priority="normal",
    payload={"verification": "layers_4_6"},
)
assert outbox_id, "outbox insert failed"
assert db.update_notification_outbox_status(outbox_id, "sent") is True
print("DB output roundtrip: OK")

tool_results = {
    "tool_attempts": [
        {"source": "local_drug_interactions", "status": "ok"},
        {"source": "rxnav", "status": "no_data"},
        {"source": "openfda", "status": "no_data"},
    ],
    "patient_context_flags": [],
}
synthesis = {
    "severity": "medium",
    "confidence": 0.8,
    "evidence": [{"source": "local_drug_interactions", "text": "Known rule"}],
    "risk": "Risk",
    "why_it_matters": "Why",
    "what_to_do_now": "Action",
}
critic = {"passed": True}
gates = pharma_research._run_safety_gates(synthesis, critic, tool_results, {"conditions": []})
assert gates["tool_ledger"] is True, gates
assert gates["three_part_summary"] is True, gates
print("Research gates: OK")

print("LAYERS_4_6_VERIFICATION_PASS")
