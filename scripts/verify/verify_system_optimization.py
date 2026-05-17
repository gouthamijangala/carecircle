"""
Verification for the robustness/guardrail optimization layer.

This script avoids destructive writes. It checks imports, deterministic policy
decisions, crisis fallback formatting, model policy tuning, and DB health shape.
"""

from __future__ import annotations

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from scripts.verify import _bootstrap  # noqa: F401

import alerting
import crisis_runtime
import db_observability
import intent_lock
import llm_policy
import personalization
import safety_policy


def main() -> None:
    crisis_lock = intent_lock.response_metadata("crisis_medical", 1.0, "crisis", None)
    assert crisis_lock["locked"] is True
    assert crisis_lock["llm_allowed"] is False

    greeting_lock = intent_lock.response_metadata("greeting_help", 0.9, "keyword", None)
    assert greeting_lock["locked"] is False

    assert safety_policy.llm_allowed_for_intent("unknown") is True
    assert safety_policy.llm_allowed_for_intent("medication_report") is False
    assert safety_policy.assess_action("primary_caregiver", "approve_veto", "approve_command").allowed is True
    assert safety_policy.assess_action("patient", "approve_veto", "approve_command").allowed is False

    qwen_params = llm_policy.chat_parameters("qwen/qwen3-4b", "fallback")
    assert qwen_params["temperature"] == 0.0
    assert qwen_params["top_p"] <= 0.8
    assert "/no_think" in llm_policy.user_prompt("hello", "qwen/qwen3-4b")

    reply = personalization.fallback_reply(
        {"full_name": "Meera", "role": "primary_caregiver", "patient_name": "Dad"},
        "unknown",
    )
    assert "Meera" in reply

    minimal = crisis_runtime.build_patient_crisis_reply({"patient_id": None})
    assert "EMERGENCY" in minimal
    assert "112" in minimal

    summary = alerting.summarize_notifications(
        [{"notification_status": "logged"}, {"notification_status": "already_logged_recently"}]
    )
    assert summary["count"] == 2
    assert summary["sent_or_logged"] == 1

    db_health = db_observability.database_health()
    assert "status" in db_health
    assert "recommended_index_sql" in db_health

    print("SYSTEM_OPTIMIZATION_VERIFICATION_PASS")


if __name__ == "__main__":
    main()
