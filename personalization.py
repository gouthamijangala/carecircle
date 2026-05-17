"""
Safe personalization helpers for CareCircle replies.

The goal is warmth and relevance without inventing facts. These helpers only
use profile fields and caller-supplied context; they do not fetch hidden data or
make medical claims.
"""

from __future__ import annotations

from typing import Any

import config


def _clean(value: Any, fallback: str, max_chars: int = 80) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    return text[:max_chars]


def build_personalization_context(profile: dict | None, extra: dict | None = None) -> dict[str, Any]:
    safe_profile = profile or {}
    context = {
        "enabled": bool(getattr(config, "PERSONALIZATION_ENABLED", True)),
        "user_name": _clean(safe_profile.get("full_name"), "there"),
        "role": _clean(safe_profile.get("role"), "user", 40),
        "patient_name": _clean(safe_profile.get("patient_name"), "the patient"),
        "patient_id_present": bool(safe_profile.get("patient_id")),
    }
    if isinstance(extra, dict):
        for key in ("intent", "source", "confidence_label", "recent_safe_summary"):
            if key in extra:
                context[key] = extra[key]
    return context


def fallback_reply(profile: dict | None, intent: str = "unknown") -> str:
    context = build_personalization_context(profile, {"intent": intent})
    name = context["user_name"] if context["user_name"].lower() not in {"unknown", "there"} else "there"
    patient = context["patient_name"]
    intent_value = str(intent or "unknown")

    if intent_value in {"greeting", "greeting_help"}:
        return f"Hello {name}. You can ask about {patient}'s medicines, labs, symptoms, or care summary."
    if intent_value == "emotional_checkin":
        return f"I hear you, {name}. Tell me what changed today, and I will keep it in the care context."
    if intent_value == "lab_report":
        return f"I can help with {patient}'s lab records. Please ask for a specific test like creatinine or HbA1c."
    if intent_value.startswith("medication"):
        return f"I can help with {patient}'s medicines. Please ask what is due, latest taken, or list all medicines."
    return f"I want to help, {name}. Please ask as a medicine, lab, symptom, upload, or care-summary question."


def personalize_low_risk_reply(reply: str, profile: dict | None, intent: str = "unknown") -> str:
    if not getattr(config, "PERSONALIZATION_ENABLED", True):
        return str(reply or "")
    text = str(reply or "").strip()
    if not text:
        return fallback_reply(profile, intent)
    return text
