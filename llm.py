import re
import queue
import threading
from typing import Any

import config
import llm_policy
import personalization


FALLBACK_REPLY = "I didn't quite understand. Could you rephrase that?"
WHATSAPP_MAX_CHARS = 380
NO_THINK_SUFFIX = "/no_think"
SYSTEM_PROMPT = """You are CareCircle, a warm but factual assistant for family caregivers.
User: {full_name} ({role}). Patient: {patient_name}.
You are only allowed to handle low-risk greetings, emotional check-ins, and unclear small talk.
Rules:
- Reply in ONE short sentence under 20 words.
- Use the user's name naturally when helpful; do not over-personalize.
- NEVER give medical advice, drug names, doses, diagnoses, or treatment changes.
- NEVER claim you checked the database unless a deterministic handler provided that data.
- If the message may involve pain, emergency, self-harm, medicine changes, labs, or hospital care, ask them to use the normal CareCircle command or seek help.
- If unsure, ask one specific clarification question.
- Output plain text only, no markdown."""

BLOCKED_INTENTS = {
    "crisis",
    "veto_command",
    "medication_report",
    "status_query",
    "vital_report",
    "lab_report",
    "medication_query",
    "medication_taken_incorrect",
    "medication_taken_confirm",
    "medication_missed_confirm",
    "abusive_language",
    "approval_context_missing",
    "approval_context_expired",
}
SAFETY_REJECT_PATTERNS = [
    "chest pain",
    "heart attack",
    "emergency",
    "hospital",
    "ambulance",
    "dose",
    "dosage",
    "mg",
    "prescribe",
    "prescription",
    "diagnose",
    "diagnosis",
    "take medicine",
    "stop medicine",
    "increase",
    "decrease",
    "tablet",
    "pill",
    "seek immediate medical help",
    "immediate medical help",
]

RECENT_CONTEXT_BLOCK_PATTERNS = [
    "chest pain",
    "heart attack",
    "emergency",
    "ambulance",
    "pain",
    "hospital",
    "died",
    "suicide",
    "kill",
    "rape",
    "medicine",
    "medication",
    "tablet",
    "pill",
    "lab",
    "report",
    "creatinine",
    "hba1c",
    "latest",
]


def _safe_profile(profile: dict) -> dict:
    safe = profile or {}
    return {
        "full_name": str(safe.get("full_name") or "there")[:80],
        "role": str(safe.get("role") or "user")[:60],
        "patient_name": str(safe.get("patient_name") or "the patient")[:80],
    }


def _personalized_fallback(profile: dict, intent: str = "unknown") -> str:
    return personalization.fallback_reply(profile, intent)


def _safe_truncate(text: str, max_chars: int = WHATSAPP_MAX_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    clipped = text[: max(0, max_chars - 1)].rsplit(" ", 1)[0].strip()
    return f"{clipped}..." if clipped else text[:max_chars]


def _strip_markdown(text: str) -> str:
    cleaned = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    cleaned = re.sub(r"[*_`>#~]", "", cleaned)
    cleaned = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _ensure_punctuation(text: str) -> str:
    if not text:
        return text
    return text if text[-1] in ".!?" else f"{text}."


def _clean_reply(text: str) -> str:
    cleaned = _strip_markdown(text)
    cleaned = cleaned.replace("Assistant:", "").replace("User:", "").strip()
    cleaned = _ensure_punctuation(cleaned)
    return _safe_truncate(cleaned, WHATSAPP_MAX_CHARS)


def _shorten_message(text: str, max_chars: int = 180) -> str:
    return _safe_truncate(str(text or "").strip(), max_chars)


def _build_messages(message: str, profile: dict, recent_messages: list[str], intent: str = "unknown") -> list[dict[str, str]]:
    safe_profile = _safe_profile(profile)
    if getattr(config, "LLM_POLICY_ENABLED", True):
        system_prompt = llm_policy.system_prompt(config.LLM_MODEL, profile, intent)
    else:
        system_prompt = SYSTEM_PROMPT.format(**safe_profile)
    messages = [{"role": "system", "content": system_prompt}]
    for recent in (recent_messages or [])[-2:]:
        shortened = _shorten_message(recent)
        lowered = shortened.lower()
        if shortened and not any(pattern in lowered for pattern in RECENT_CONTEXT_BLOCK_PATTERNS):
            messages.append({"role": "user", "content": shortened})
    current = _shorten_message(message)
    if getattr(config, "LLM_POLICY_ENABLED", True):
        messages.append({"role": "user", "content": llm_policy.user_prompt(current, config.LLM_MODEL)})
    else:
        messages.append({"role": "user", "content": f"{current}\n\n{NO_THINK_SUFFIX}"})
    return messages


def _debug_log(label: str, value: Any) -> None:
    if config.LLM_DEBUG:
        print(f"[LLM:{label}] {value}")


def _allowed_to_call(intent: str) -> bool:
    if getattr(config, "LLM_POLICY_ENABLED", True):
        return llm_policy.llm_allowed(None, intent)
    if intent in BLOCKED_INTENTS:
        return False
    if intent == "unknown":
        return True
    if intent in {"greeting", "greeting_help", "emotional_checkin"} and config.ENABLE_WARM_GREETINGS:
        return True
    return False


def generate_reply(message: str, profile: dict, recent_messages: list[str], intent: str) -> str:
    """
    Master WhatsApp-safe LLM fallback entrypoint.
    Returns an LLM reply only for allowed low-risk cases, otherwise a safe fallback.
    """
    try:
        if not _allowed_to_call(intent):
            return _personalized_fallback(profile, intent)
        if not str(message or "").strip():
            return _personalized_fallback(profile, intent)

        if getattr(config, "LLM_POLICY_ENABLED", True) and not llm_policy.llm_allowed(profile, intent):
            return _personalized_fallback(profile, intent)

        messages = _build_messages(message, profile, recent_messages, intent)
        _debug_log("messages", messages)
        params = llm_policy.chat_parameters(config.LLM_MODEL, "fallback") if getattr(config, "LLM_POLICY_ENABLED", True) else {}
        reply = _call_llm(
            messages,
            max_tokens=int(params.get("max_tokens", config.LLM_MAX_TOKENS)),
            temperature=float(params.get("temperature", config.LLM_TEMPERATURE)),
            timeout=config.LLM_TIMEOUT,
        )

        if not reply or not _is_safe_reply(reply):
            return _personalized_fallback(profile, intent)

        cleaned = _clean_reply(reply)
        if not cleaned or not _is_safe_reply(cleaned):
            return _personalized_fallback(profile, intent)
        _debug_log("reply", cleaned)
        return cleaned
    except Exception:
        return _personalized_fallback(profile, intent)


def _call_lmstudio_once(messages: list[dict], max_tokens: int, temperature: float, timeout: float) -> str | None:
    """Call local LM Studio once through the OpenAI-compatible API."""
    try:
        import llm_gateway

        return llm_gateway.call_chat_completion(
            base_url=config.LLM_ENDPOINT,
            model_id=config.LLM_MODEL,
            api_key="lm-studio",
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=llm_policy.chat_parameters(config.LLM_MODEL, "fallback").get("top_p", 0.8),
            stop=["User:", "Assistant:"],
            timeout=timeout,
        )
    except Exception as error:
        _debug_log("error", error)
        return None


def _call_llm(messages: list[dict], max_tokens: int, temperature: float, timeout: float) -> str | None:
    """Call local LM Studio, returning None if it exceeds the timeout budget."""
    try:
        result_queue: queue.Queue[str | None] = queue.Queue(maxsize=1)

        def worker() -> None:
            result_queue.put(_call_lmstudio_once(messages, max_tokens, temperature, timeout))

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            _debug_log("timeout", f"LLM call exceeded {timeout}s")
            return None
        return result_queue.get_nowait()
    except Exception as error:
        _debug_log("error", error)
        return None


def _is_safe_reply(text: str) -> bool:
    """Reject replies that drift into crisis language or medical advice."""
    try:
        lowered = str(text or "").lower()
        if not lowered.strip():
            return False
        return not any(pattern in lowered for pattern in SAFETY_REJECT_PATTERNS)
    except Exception:
        return False
