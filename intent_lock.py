"""
Intent locking metadata for CareCircle routing.

The router and handler still make the existing business decision. This module
adds a small immutable record explaining whether the decision is locked away
from embedding/LLM overrides.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import config
import safety_policy


@dataclass(frozen=True)
class IntentLock:
    intent: str
    confidence: float
    source: str
    locked: bool
    lock_reason: str
    llm_allowed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "confidence": self.confidence,
            "source": self.source,
            "locked": self.locked,
            "lock_reason": self.lock_reason,
            "llm_allowed": self.llm_allowed,
        }


def _bounded_confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return 0.0


def build_intent_lock(intent: str, confidence: float, source: str, pending_context: dict | None = None) -> IntentLock:
    intent_value = str(intent or "unknown")
    source_value = str(source or "unknown")
    context = pending_context or {}

    if not getattr(config, "INTENT_LOCKING_ENABLED", True):
        return IntentLock(intent_value, _bounded_confidence(confidence), source_value, False, "disabled", True)

    if safety_policy.is_crisis_intent(intent_value):
        reason = "crisis_hard_gate"
        locked = True
    elif intent_value in {"approve_command", "veto_command", "approval_context_missing", "approval_context_expired"}:
        reason = "stateful_approval_command"
        locked = True
    elif context.get("type") in {"veto_window", "approval_window", "pending_approval", "interaction_alert", "new_med"}:
        reason = "active_pending_context"
        locked = True
    elif safety_policy.must_remain_deterministic(intent_value):
        reason = "deterministic_domain_intent"
        locked = True
    else:
        reason = "open_low_risk_intent"
        locked = False

    return IntentLock(
        intent=intent_value,
        confidence=_bounded_confidence(confidence),
        source=source_value,
        locked=locked,
        lock_reason=reason,
        llm_allowed=safety_policy.llm_allowed_for_intent(intent_value),
    )


def response_metadata(intent: str, confidence: float, source: str, pending_context: dict | None = None) -> dict[str, Any]:
    return build_intent_lock(intent, confidence, source, pending_context).as_dict()
