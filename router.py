import random
from datetime import datetime, time, timedelta, timezone

import config
import intent as keyword_intent
import intent_embedding
from normalizer import normalize


# Decision tree:
# 1. Deterministic hard safety gate is always first.
# 2. Command gate is state-aware and only accepts active approval/veto windows.
# 3. Media/location context gates route uploads before text classification.
# 4. The deterministic classifier in intent.py handles explainable core routing.
# 5. Embedding is only a low-confidence fallback and is blocklist/threshold gated.
# 6. If all layers fail, return unknown so handlers can use LLM fallback safely.
#
# Edge cases:
# - Existing keyword names such as "greeting" and "family_member_query" are mapped
#   to cluster names like "greeting_help" and "health_status_query".
# - Short friendly variants like "hi there" are handled here because the older
#   keyword classifier intentionally only matches very narrow greetings.
# - Bare command words are classified by intent.py so missing/expired approval
#   context is preserved instead of flattened into approve/veto.


KEYWORD_EMBEDDING_FALLBACK_THRESHOLD = 0.7
IST = timezone(timedelta(hours=5, minutes=30))
MEDICATION_DUE_WINDOW_MINUTES = 30
MEDICATION_DUE_NOW_BIAS = 0.15


INTENT_CLUSTER_ALIASES = {
    "crisis": "crisis_medical",
    "greeting": "greeting_help",
    "family_member_query": "health_status_query",
    "caregiver_update": "health_status_query",
    "status_query": "medication_list",
    "medication_query": "medication_due_now",
    "medication_taken_incorrect": "medication_report",
    "medication_taken_confirm": "medication_report",
    "medication_missed_confirm": "medication_report",
    "mood_report": "emotional_checkin",
    "caregiver_schedule_update": "caregiver_handoff",
    "caregiver_observation": "caregiver_observation",
    "photo_upload": "photo_processing",
    "audio_note": "audio_processing",
    "pdf_upload": "pdf_processing",
    "location_update": "location_update",
    "refill_request": "medication_schedule",
    "refill_confirmed": "medication_schedule",
    "refill_declined": "medication_schedule",
    "clarification_response": "unclear",
    "abusive_language": "unclear",
    "approval_context_missing": "approval_context_missing",
    "approval_context_expired": "approval_context_expired",
}

GREETING_HELP_PATTERNS = {
    "hi",
    "hello",
    "hey",
    "namaste",
    "hi there",
    "hello there",
    "help",
    "what can you do",
    "kya kar sakte ho",
    "mujhe help chahiye",
    "who are you",
    "who r u",
    "who are u",
    "what are you",
    "carecircle kya hai",
}

HEALTH_STATUS_PATTERNS = [
    "how is dad",
    "how is patient",
    "health update",
    "health status",
    "dad health",
    "patient health",
    "tabiyat",
    "kaisa hai",
]

LAB_QUERY_PATTERNS = [
    "latest lab",
    "latest labs",
    "lab report",
    "lab reports",
    "latest report",
    "latest reports",
    "test results",
    "creatinine",
    "hba1c",
    "cholesterol",
    "lft",
    "kft",
    "cbc",
    "tsh",
]

LATEST_MEDICATION_PATTERNS = [
    "latest medicine",
    "latest medication",
    "latest medication taken",
    "latest medicine taken",
    "last medicine",
    "last medication",
    "last medication taken",
    "recent medicine",
    "recent medication",
    "medicine taken by patient",
    "medication taken by patient",
    "what was my latest medicine",
]

MEDICATION_CURRENT_QUERY_PATTERNS = [
    "abhi kya liya",
    "abhi kya lena",
    "abhi kya lena hai",
    "kya liya abhi",
    "kya lena abhi",
    "abhi ki medicine",
    "abhi medicine",
    "medicine now",
    "medicine due now",
    "medicines due now",
    "what medicine now",
    "what medicines now",
    "what should i take now",
    "which medicine now",
    "which tablet now",
    "current medicine",
    "present medicine",
    "this time medicine",
]
MEDICATION_CURRENT_MARKERS = {"abhi", "now", "currently", "right now", "iss waqt", "this time"}
MEDICATION_QUESTION_MARKERS = {"kya", "what", "which", "due", "should", "lena", "take"}
MEDICATION_WORD_MARKERS = {"medicine", "medication", "dawai", "dawa", "tablet", "pill", "dose", "liya", "lena"}

DOCUMENT_UPLOAD_CONFIRMATION_PATTERNS = [
    "photo bhej raha hoon",
    "photo bhej rahi hoon",
    "photo bhej di",
    "photo sent",
    "sent photo",
    "sent the photo",
    "sent prescription photo",
    "sending prescription photo",
    "sending report",
    "sending the report",
    "uploading report",
    "uploaded report",
    "report upload kar diya",
    "prescription upload kar diya",
    "this is the new prescription",
    "yeh lab report hai",
    "did you receive the photo",
]

COMMAND_PATTERNS = {
    "veto": "veto_command",
    "veto this": "veto_command",
    "reject": "veto_command",
    "deny": "veto_command",
    "cancel": "veto_command",
    "cancel this": "veto_command",
    "do not proceed": "veto_command",
    "nahi karna": "veto_command",
    "nahi chahiye": "veto_command",
    "nai chahiye": "veto_command",
    "approve": "approve_command",
    "approve this": "approve_command",
    "yes approve": "approve_command",
    "confirm": "approve_command",
    "confirm it": "approve_command",
    "proceed": "approve_command",
    "go ahead": "approve_command",
    "haan karo": "approve_command",
}

MEDIA_GATE_INTENTS = {
    "image/": "photo_processing",
    "audio/": "audio_processing",
}


def _sample_embedding() -> bool:
    try:
        if not bool(getattr(config, "EMBEDDING_ENABLED", False)):
            return False
        configured_pct = getattr(config, "EMBEDDING_SHADOW_PCT", getattr(config, "EMBEDDING_TRAFFIC_PCT", 0))
        traffic_pct = max(0.0, min(100.0, float(configured_pct)))
        return random.random() * 100 < traffic_pct
    except Exception:
        return False


def _map_keyword_intent(intent_name: str) -> str:
    return INTENT_CLUSTER_ALIASES.get(intent_name, intent_name)


def _bounded_confidence(value, default: float = 0.95) -> float:
    try:
        return round(max(0.0, min(1.0, float(value))), 4)
    except Exception:
        return default


def _context_gate(pending_context: dict | None) -> tuple[str, float] | None:
    try:
        context = pending_context or {}
        media_type = context.get("media_type")
        if isinstance(media_type, str):
            media_type = media_type.strip().lower()
            for prefix, intent_name in MEDIA_GATE_INTENTS.items():
                if media_type.startswith(prefix):
                    return intent_name, 0.95
            if media_type == "application/pdf":
                return "pdf_processing", 0.95
        if context.get("latitude") is not None:
            return "location_update", 0.95
        return None
    except Exception:
        return None


def _context_media_gate(message: str, pending_context: dict | None) -> tuple[str, float] | None:
    try:
        context_result = _context_gate(pending_context)
        if context_result:
            return context_result
        if _has_location_text(message):
            return "location_update", 0.95
        return None
    except Exception:
        return None


def _has_location_text(message: str) -> bool:
    text = str(message or "").lower()
    return "maps.google" in text or "geo:" in text or "google.com/maps" in text


def _parse_schedule_time(value) -> time | None:
    try:
        if isinstance(value, time):
            return value
        text = str(value or "").strip()
        if not text:
            return None
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                return datetime.strptime(text[:8] if fmt == "%H:%M:%S" else text[:5], fmt).time()
            except Exception:
                continue
        return None
    except Exception:
        return None


def _minutes_from_now_ist(scheduled: time, now_ist: datetime) -> float:
    scheduled_dt = now_ist.replace(hour=scheduled.hour, minute=scheduled.minute, second=0, microsecond=0)
    candidates = [scheduled_dt, scheduled_dt - timedelta(days=1), scheduled_dt + timedelta(days=1)]
    return min(abs((candidate - now_ist).total_seconds()) / 60 for candidate in candidates)


def _patient_has_due_medication_now(patient_id: str | None, now_ist: datetime | None = None) -> bool:
    try:
        if not patient_id:
            return False
        import db

        current = now_ist or datetime.now(IST)
        medications = db.get_active_medications_schedule(str(patient_id)) or []
        for medication in medications:
            for scheduled_value in medication.get("scheduled_times") or []:
                scheduled = _parse_schedule_time(scheduled_value)
                if scheduled and _minutes_from_now_ist(scheduled, current) <= MEDICATION_DUE_WINDOW_MINUTES:
                    return True
        return False
    except Exception:
        return False


def _is_ambiguous_current_medication_query(message: str) -> bool:
    try:
        normalized = normalize(message)
        if not normalized:
            return False
        if any(pattern in normalized for pattern in MEDICATION_CURRENT_QUERY_PATTERNS):
            return True
        has_current = any(marker in normalized for marker in MEDICATION_CURRENT_MARKERS)
        has_question = any(marker in normalized for marker in MEDICATION_QUESTION_MARKERS)
        has_medication_word = any(marker in normalized for marker in MEDICATION_WORD_MARKERS)
        return has_current and has_question and has_medication_word
    except Exception:
        return False


def _medication_due_now_temporal_bias(
    message: str,
    patient_id: str | None,
    current_intent: str,
    current_confidence: float,
) -> tuple[str, float, str] | None:
    try:
        if current_intent in {"crisis", "crisis_medical", "crisis_safety", "crisis_self_harm", "crisis_death"}:
            return None
        if current_intent in {"approve_command", "veto_command", "approval_context_missing", "approval_context_expired"}:
            return None
        if current_intent == "medication_latest":
            return None
        if not _is_ambiguous_current_medication_query(message):
            return None
        if not _patient_has_due_medication_now(patient_id):
            return None

        boosted = max(_bounded_confidence(current_confidence, 0.0) + MEDICATION_DUE_NOW_BIAS, 0.72)
        return "medication_due_now", _bounded_confidence(boosted), "keyword_temporal_bias"
    except Exception:
        return None


def _apply_temporal_bias(
    intent: str,
    confidence: float,
    message: str,
    pending_context: dict | None,
) -> tuple[str, float]:
    """
    If message time is near a scheduled medication dose, bias toward medication_due_now.
    If message time is far from any dose, bias toward medication_report or medication_list.
    """
    if intent not in {"medication_due_now", "medication_report", "medication_list", "medication_schedule"}:
        return intent, confidence

    patient_id = (pending_context or {}).get("patient_id")
    if not patient_id:
        return intent, confidence

    try:
        import db

        now = datetime.now()
        current_minutes = now.hour * 60 + now.minute

        meds = db.get_active_medications_schedule(patient_id)
        if not meds:
            return intent, confidence

        nearest_diff_minutes = float("inf")
        for med in meds:
            times = med.get("scheduled_times") or []
            for scheduled_time in times:
                try:
                    h, m = map(int, str(scheduled_time).split(":")[:2])
                    scheduled_minutes = h * 60 + m
                    same_day_diff = abs(current_minutes - scheduled_minutes)
                    wraparound_diff = min(same_day_diff, 1440 - same_day_diff)
                    if wraparound_diff < nearest_diff_minutes:
                        nearest_diff_minutes = wraparound_diff
                except Exception:
                    continue

        if nearest_diff_minutes <= 30:
            if intent == "medication_due_now":
                return intent, min(1.0, confidence + 0.15)
            if intent == "medication_report":
                if any(word in message.lower() for word in ["abhi", "now", "iss waqt", "right now", "current"]):
                    return "medication_due_now", min(1.0, confidence + 0.10)
        elif nearest_diff_minutes > 120:
            if intent == "medication_due_now":
                return "medication_list", confidence
    except Exception:
        pass

    return intent, confidence


def _command_gate(message: str, pending_context: dict | None = None) -> tuple[str, float] | None:
    normalized = normalize(message)
    raw = str(message or "").strip()
    upper = raw.upper()

    # Step 1: Check text shape
    intent_shape = _command_shape(message)

    if intent_shape is None:
        return None

    # Step 2: CRITICAL - Check if there's an active approval context
    context = pending_context or {}
    context_type = context.get("type")

    if context_type in {"veto_window", "approval_window", "pending_approval", "interaction_alert", "new_med"}:
        if context.get("active") is True:
            expires_at = context.get("expires_at")
            if expires_at:
                try:
                    exp = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
                    if datetime.now(exp.tzinfo) <= exp:
                        return intent_shape, 0.95
                except Exception:
                    pass

            # If no expiry or can't parse, allow if created recently (< configured approval window).
            created_at = context.get("created_at") or context.get("asked_at")
            if created_at:
                try:
                    cre = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
                    if (datetime.now(cre.tzinfo) - cre).total_seconds() < config.APPROVAL_CONTEXT_EXPIRY_SECONDS:
                        return intent_shape, 0.95
                except Exception:
                    pass

    # Step 3: Text looks like a command but no valid context - don't classify as command.
    return None


def _command_shape(message: str) -> str | None:
    normalized = normalize(message)
    raw = str(message or "").strip()
    upper = raw.upper()

    if upper == "VETO" or upper.startswith("VETO "):
        return "veto_command"
    if upper == "APPROVE" or upper.startswith("APPROVE "):
        return "approve_command"
    if normalized in COMMAND_PATTERNS:
        return COMMAND_PATTERNS[normalized]
    if normalized.startswith(("veto ", "reject ", "deny ", "cancel ")):
        return "veto_command"
    if normalized.startswith(("approve ", "yes approve ", "confirm ", "proceed ")):
        return "approve_command"
    return None


def _keyword_cluster_overrides(message: str) -> str | None:
    normalized = normalize(message)
    if not normalized:
        return None

    if any(pattern in normalized for pattern in DOCUMENT_UPLOAD_CONFIRMATION_PATTERNS):
        return "document_upload_confirmation"
    if any(pattern in normalized for pattern in LATEST_MEDICATION_PATTERNS):
        return "medication_latest"
    if any(pattern in normalized for pattern in LAB_QUERY_PATTERNS):
        return "lab_report"
    if normalized in GREETING_HELP_PATTERNS:
        return "greeting_help"
    if any(pattern in normalized for pattern in HEALTH_STATUS_PATTERNS):
        return "health_status_query"
    return None


def _can_refine_keyword_result(keyword_intent_name: str, override_intent: str) -> bool:
    """Allow only narrow deterministic refinements; never weaken safety gates."""
    if keyword_intent_name in {
        "crisis_medical",
        "crisis_safety",
        "crisis_self_harm",
        "crisis_death",
        "approve_command",
        "veto_command",
        "approval_context_missing",
        "approval_context_expired",
        "photo_processing",
        "audio_processing",
        "pdf_processing",
        "location_update",
    }:
        return False
    if keyword_intent_name == "unknown":
        return True
    if override_intent == "medication_latest":
        return keyword_intent_name in {
            "medication_report",
            "medication_list",
            "medication_due_now",
            "medication_schedule",
        }
    if override_intent == "lab_report":
        return keyword_intent_name in {"health_status_query", "symptom_report"}
    if override_intent == "document_upload_confirmation":
        return keyword_intent_name in {"unknown", "new_prescription", "lab_report", "health_status_query"}
    if override_intent in {"greeting_help", "health_status_query"}:
        return keyword_intent_name == "unknown"
    return False


def _embedding_allowed(message: str, pending_context: dict | None) -> bool:
    try:
        is_emergency, _ = keyword_intent.is_valid_emergency(message)
        if is_emergency:
            return False
    except Exception:
        pass
    if _context_media_gate(message, pending_context) is not None:
        return False
    if _command_shape(message) is not None:
        return False
    return True


def _is_embedding_blocked(intent_name: str) -> bool:
    try:
        return intent_name in getattr(config, "EMBEDDING_BLOCKLIST", set())
    except Exception:
        return False


def _embedding_threshold(intent_name: str) -> float:
    try:
        thresholds = getattr(config, "EMBEDDING_CONFIDENCE_THRESHOLDS", {})
        return float(thresholds.get(intent_name, thresholds.get("default", 0.55)))
    except Exception:
        return 0.55


def _log_embedding_decision(
    message: str,
    intent_name: str,
    confidence: float,
    source: str,
    patient_id: str | None = None,
    profile_id: str | None = None,
    pending_context: dict | None = None,
) -> None:
    try:
        import db

        if (not patient_id or not profile_id) and pending_context:
            from_phone = str((pending_context or {}).get("from_phone") or "").strip()
            if from_phone:
                try:
                    profile = db.get_profile_by_phone(from_phone)
                    patient_id = patient_id or (profile or {}).get("patient_id")
                    profile_id = profile_id or (profile or {}).get("id")
                except Exception:
                    pass

        db.log_embedding_decision(
            patient_id,
            profile_id,
            str(message or ""),
            str(intent_name or "unknown"),
            _bounded_confidence(confidence, 0.0),
            source,
            normalize(message),
        )
    except Exception:
        return


def _log_embedding_shadow(
    message: str,
    pending_context: dict | None,
    actual_intent: str,
    actual_confidence: float,
    actual_source: str,
    patient_id: str | None = None,
    profile_id: str | None = None,
) -> None:
    """Classify with embedding for validation only; never affects the reply."""
    try:
        if str(actual_intent or "").startswith("crisis"):
            return
        if not bool(getattr(config, "EMBEDDING_SHADOW_MODE", True)):
            return
        if not _sample_embedding() or not _embedding_allowed(message, pending_context):
            return

        embedding_intent, embedding_confidence = intent_embedding.classify_intent_embedding(message)
        if _is_embedding_blocked(embedding_intent):
            embedding_intent = f"blocked:{embedding_intent}"

        _log_embedding_decision(
            message,
            embedding_intent,
            embedding_confidence,
            f"embedding_shadow|actual={actual_source}:{actual_intent}:{_bounded_confidence(actual_confidence, 0.0)}",
            patient_id=patient_id,
            profile_id=profile_id,
            pending_context=pending_context,
        )
    except Exception:
        return


def _try_embedding_fallback(
    message: str,
    pending_context: dict | None,
    patient_id: str | None = None,
    profile_id: str | None = None,
) -> tuple[str, float, str] | None:
    try:
        if not _sample_embedding() or not _embedding_allowed(message, pending_context):
            return None

        embedding_intent, embedding_confidence = intent_embedding.classify_intent_embedding(message)
        embedding_confidence = _bounded_confidence(embedding_confidence, 0.0)

        if _is_embedding_blocked(embedding_intent):
            embedding_intent = "unknown"
            embedding_confidence = 0.0

        threshold = _embedding_threshold(embedding_intent)
        passed = embedding_intent != "unknown" and embedding_confidence >= threshold

        _log_embedding_decision(
            message,
            embedding_intent,
            embedding_confidence,
            "embedding_shadow" if bool(getattr(config, "EMBEDDING_SHADOW_MODE", True)) else "embedding",
            patient_id=patient_id,
            profile_id=profile_id,
            pending_context=pending_context,
        )

        if passed and not bool(getattr(config, "EMBEDDING_SHADOW_MODE", True)):
            return embedding_intent, float(embedding_confidence), "embedding"
        return None
    except Exception:
        return None


def _with_shadow_log(
    result: tuple[str, float, str],
    message: str,
    pending_context: dict | None,
    patient_id: str | None,
    profile_id: str | None,
) -> tuple[str, float, str]:
    try:
        _log_embedding_shadow(
            message,
            pending_context,
            result[0],
            result[1],
            result[2],
            patient_id=patient_id,
            profile_id=profile_id,
        )
    except Exception:
        pass
    return result


def _deterministic_crisis_intent(message: str) -> tuple[str, float, str] | None:
    try:
        is_valid, reason = keyword_intent.is_valid_emergency(message)
        if not is_valid:
            return None
        if reason == "self_harm_risk":
            return "crisis_self_harm", 1.0, "crisis"
        if reason == "death_report":
            return "crisis_death", 1.0, "crisis"
        if reason in {"violence_threat", "sexual_assault"}:
            return "crisis_safety", 1.0, "crisis"
        return "crisis_medical", 1.0, "crisis"
    except Exception:
        return None


def get_final_intent(
    message: str,
    pending_context: dict = None,
    patient_id: str | None = None,
    profile_id: str | None = None,
) -> tuple[str, float, str]:
    """
    Returns (intent, confidence, source).

    Cascade:
    1. deterministic hard safety gate
    2. state-aware command gate
    3. media/location context gate
    4. deterministic classifier
    5. embedding fallback for unknown or low-confidence deterministic results
    6. unknown for handler-level LLM fallback
    """
    try:
        def _finalize(result: tuple[str, float, str]) -> tuple[str, float, str]:
            decided_intent, decided_confidence, decided_source = result
            temporal_context = dict(pending_context or {})
            if patient_id and not temporal_context.get("patient_id"):
                temporal_context["patient_id"] = patient_id
            decided_intent, decided_confidence = _apply_temporal_bias(
                decided_intent,
                decided_confidence,
                message,
                temporal_context,
            )
            return decided_intent, _bounded_confidence(decided_confidence, 0.0), decided_source

        # 1. Hard Safety Gate - deterministic crisis check is always first.
        deterministic_crisis = _deterministic_crisis_intent(message)
        if deterministic_crisis:
            return _finalize(deterministic_crisis)

        # 2. Command Gate - only active, non-expired approval/veto contexts pass.
        command_result = _command_gate(message, pending_context)
        if command_result:
            return _finalize((command_result[0], command_result[1], "command_gate"))
        if _command_shape(message) is not None:
            return _finalize(("approval_context_missing", 0.9, "command_gate"))

        # 3. Context/Media Gate.
        context_result = _context_media_gate(message, pending_context)
        if context_result:
            return _finalize((context_result[0], context_result[1], "context_gate"))

        # 4. Deterministic Classifier.
        classification = keyword_intent.classify_intent_with_confidence(message, pending_context)
        intent_name = classification.get("intent", "unknown")
        final_intent = _map_keyword_intent(intent_name)
        keyword_confidence = _bounded_confidence(classification.get("confidence"), 0.0)

        override = _keyword_cluster_overrides(message)
        if override and _can_refine_keyword_result(final_intent, override):
            final_intent = override
            keyword_confidence = 0.95

        due_now_bias = _medication_due_now_temporal_bias(message, patient_id, final_intent, keyword_confidence)
        if due_now_bias:
            final_intent, keyword_confidence, keyword_source = due_now_bias
        else:
            keyword_source = "keyword"

        if final_intent.startswith("crisis"):
            return _finalize((final_intent, keyword_confidence, keyword_source))

        # 5. Embedding fallback only for unknown/low-confidence deterministic output.
        if final_intent == "unknown" or keyword_confidence < KEYWORD_EMBEDDING_FALLBACK_THRESHOLD:
            embedding_result = _try_embedding_fallback(
                message,
                pending_context,
                patient_id=patient_id,
                profile_id=profile_id,
            )
            if embedding_result:
                return _finalize(embedding_result)

        if final_intent != "unknown":
            return _finalize((final_intent, keyword_confidence, keyword_source))

        # 6. Unknown so handlers can perform low-risk LLM fallback.
        return _finalize(("unknown", keyword_confidence, "keyword"))
    except Exception:
        return "unknown", 0.0, "keyword"


# APPROVED ENHANCEMENTS (commented-out for future activation):
# 1. Phonetic matching for Hinglish: Use jellyfish.metaphone() to match "dard"/"dardh".
# 2. Aho-Corasick automaton for hard gate: Build once at startup for O(n) crisis scanning.
# 3. Centroid auto-refresh: Weekly job to recompute centroids from confirmed interactions.
# 4. Per-user personalisation: Store user-specific alias expansions in profiles.preferences jsonb.
# 5. Fallback to Krutrim Vyakyarth model: If MiniLM confidence < 0.5, try Vyakyarth.
# 6. Async embedding pre-computation: Pre-embed common phrases at idle time.
# 7. Audit-based seed expansion: Monthly job to add high-confidence misclassifications to seed bank.
