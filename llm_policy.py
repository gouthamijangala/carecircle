"""
Model-specific prompt and decoding policy.

This module keeps fallback LLM behavior conservative across model families.
It is pure policy: no network calls and no OpenAI imports.
"""

from __future__ import annotations

from typing import Any

import config
import personalization
import safety_policy


BASE_LOW_RISK_SYSTEM = """You are CareCircle, a warm but factual assistant for family caregivers.
You may only answer low-risk greetings, emotional check-ins, or clarification questions.
Never diagnose, prescribe, change medicines, provide dosage, or claim database facts unless deterministic context was provided.
If a request mentions emergency, pain, hospital, medicine changes, labs, or self-harm, ask for a safer CareCircle-specific question or urgent help.
Keep the reply under 22 words. Plain text only."""


def model_family(model_id: str | None, base_url: str | None = None) -> str:
    text = f"{model_id or ''} {base_url or ''}".lower()
    if "qwen" in text:
        return "qwen"
    if "gemini" in text:
        return "gemini"
    if "nemotron" in text or "nvidia" in text:
        return "nvidia"
    if "openrouter" in text:
        return "openrouter"
    return "generic"


def chat_parameters(model_id: str | None, task_type: str = "fallback") -> dict[str, Any]:
    family = model_family(model_id)
    strict_temperature = float(getattr(config, "LLM_STRICT_TEMPERATURE", 0.0))

    if task_type == "extraction":
        return {
            "temperature": strict_temperature,
            "top_p": min(float(getattr(config, "LLM_EXTRACTION_TOP_P", 0.75)), 0.8),
            "max_tokens": 1000 if family in {"gemini", "nvidia", "openrouter"} else 800,
        }

    if family == "qwen":
        return {"temperature": 0.0, "top_p": 0.75, "max_tokens": int(getattr(config, "LLM_FALLBACK_MAX_TOKENS", 70))}
    if family in {"gemini", "openrouter"}:
        return {"temperature": 0.0, "top_p": 0.8, "max_tokens": int(getattr(config, "LLM_FALLBACK_MAX_TOKENS", 70))}
    if family == "nvidia":
        return {"temperature": 0.0, "top_p": 0.75, "max_tokens": int(getattr(config, "LLM_FALLBACK_MAX_TOKENS", 70))}
    return {"temperature": 0.0, "top_p": float(getattr(config, "LLM_FALLBACK_TOP_P", 0.8)), "max_tokens": 60}


def system_prompt(model_id: str | None, profile: dict | None, intent: str = "unknown") -> str:
    context = personalization.build_personalization_context(profile, {"intent": intent})
    scope = safety_policy.redacted_scope(profile, intent)
    family = model_family(model_id)
    suffix = ""
    if family == "qwen":
        suffix = "\nFor Qwen: do not reveal thinking. Follow /no_think."
    elif family in {"gemini", "openrouter"}:
        suffix = "\nFor Gemini/OpenRouter: be literal, concise, and do not infer missing facts."
    elif family == "nvidia":
        suffix = "\nFor NVIDIA/Nemotron: avoid broad medical language and answer only the allowed scope."

    return (
        f"{BASE_LOW_RISK_SYSTEM}\n"
        f"User: {context['user_name']} ({context['role']}). Patient: {context['patient_name']}.\n"
        f"Scope: {scope}.\n"
        f"If blocked by scope, ask one short clarification question.{suffix}"
    )


def user_prompt(message: str, model_id: str | None) -> str:
    text = str(message or "").strip()
    if model_family(model_id) == "qwen":
        return f"{text}\n\n/no_think"
    return text


def llm_allowed(profile: dict | None, intent: str | None) -> bool:
    role = (profile or {}).get("role")
    decision = safety_policy.assess_action(role, "send_message", intent)
    return decision.allowed and safety_policy.llm_allowed_for_intent(intent)
