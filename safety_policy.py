"""
Central safety and scope policy for CareCircle.

This module is intentionally deterministic and dependency-light. It does not
classify messages; it describes which already-classified actions are allowed to
proceed, which intents must stay deterministic, and which intents are never
safe for LLM generation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


DETERMINISTIC_LOCKED_INTENTS = {
    "crisis",
    "crisis_medical",
    "crisis_safety",
    "crisis_self_harm",
    "crisis_death",
    "approve_command",
    "veto_command",
    "approval_context_missing",
    "approval_context_expired",
    "medication_report",
    "medication_taken_confirm",
    "medication_missed_confirm",
    "medication_taken_incorrect",
    "medication_due_now",
    "medication_list",
    "medication_schedule",
    "lab_report",
    "vital_report",
}

LLM_BLOCKED_INTENTS = DETERMINISTIC_LOCKED_INTENTS | {
    "new_prescription",
    "medication_side_effect",
    "refill_request",
    "refill_confirmed",
    "refill_declined",
    "abusive_language",
}

HIGH_RISK_INTENTS = {
    intent for intent in DETERMINISTIC_LOCKED_INTENTS if intent.startswith("crisis")
} | {"approve_command", "veto_command", "new_prescription"}

ROLE_ALLOWED_ACTIONS = {
    "patient": {
        "send_message",
        "upload_media",
        "view_own_status",
        "report_symptom",
        "receive_crisis_card",
    },
    "primary_caregiver": {
        "send_message",
        "upload_media",
        "view_patient_status",
        "approve_veto",
        "receive_alerts",
        "resolve_alert",
    },
    "secondary_caregiver": {
        "send_message",
        "upload_media",
        "view_patient_status",
        "receive_alerts",
    },
    "doctor": {
        "send_message",
        "view_patient_status",
        "receive_alerts",
        "clinical_review",
    },
    "unknown": {"send_message"},
}


@dataclass(frozen=True)
class ScopeDecision:
    allowed: bool
    reason: str
    risk_level: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "risk_level": self.risk_level,
        }


def normalize_role(role: str | None) -> str:
    value = str(role or "unknown").strip().lower()
    return value if value in ROLE_ALLOWED_ACTIONS else "unknown"


def is_crisis_intent(intent: str | None) -> bool:
    return str(intent or "").startswith("crisis") or str(intent or "") == "crisis"


def must_remain_deterministic(intent: str | None) -> bool:
    return str(intent or "") in DETERMINISTIC_LOCKED_INTENTS or is_crisis_intent(intent)


def llm_allowed_for_intent(intent: str | None) -> bool:
    value = str(intent or "unknown")
    if value in LLM_BLOCKED_INTENTS or is_crisis_intent(value):
        return False
    return value in {"unknown", "unclear", "greeting", "greeting_help", "emotional_checkin"}


def assess_action(role: str | None, action: str, intent: str | None = None) -> ScopeDecision:
    normalized_role = normalize_role(role)
    normalized_action = str(action or "").strip().lower()
    normalized_intent = str(intent or "unknown")

    if is_crisis_intent(normalized_intent):
        return ScopeDecision(True, "crisis_fast_path_always_allowed", "critical")

    if normalized_intent in HIGH_RISK_INTENTS and normalized_action == "llm_generate":
        return ScopeDecision(False, "high_risk_intent_blocks_llm", "high")

    allowed = normalized_action in ROLE_ALLOWED_ACTIONS.get(normalized_role, set())
    if not allowed:
        return ScopeDecision(False, f"role_{normalized_role}_cannot_{normalized_action}", "medium")

    risk = "high" if normalized_intent in HIGH_RISK_INTENTS else "low"
    return ScopeDecision(True, "role_action_allowed", risk)


def redacted_scope(profile: dict | None, intent: str | None) -> dict[str, Any]:
    safe_profile = profile or {}
    role = normalize_role(safe_profile.get("role"))
    return {
        "role": role,
        "intent": str(intent or "unknown"),
        "deterministic_lock_required": must_remain_deterministic(intent),
        "llm_allowed": llm_allowed_for_intent(intent),
    }
