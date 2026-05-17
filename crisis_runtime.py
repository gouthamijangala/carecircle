"""
Fast, deterministic crisis runtime.

This layer deliberately avoids embedding and LLM calls. It uses the existing
crisis packet builder/cache, then records caregiver notifications through the
alerting layer. If anything fails, it still returns a minimal emergency reply.
"""

from __future__ import annotations

import time
from typing import Any

import alerting
import config
import crisis
import db


MINIMAL_CRISIS_REPLY = (
    "EMERGENCY MODE\n"
    "Please seek immediate medical help now.\n"
    "Call local emergency services or go to the nearest hospital.\n"
    "Government Helpline: 112"
)


def build_patient_crisis_reply(profile: dict | None) -> str:
    try:
        patient_id = (profile or {}).get("patient_id")
        if not patient_id:
            return MINIMAL_CRISIS_REPLY
        packet = crisis.get_emergency_packet(str(patient_id))
        return crisis.format_crisis_card(packet)
    except Exception:
        return MINIMAL_CRISIS_REPLY


def handle_crisis(
    profile: dict | None,
    trigger_message: str = "",
    force_alerts: bool = True,
) -> dict[str, Any]:
    started = time.perf_counter()
    safe_profile = profile or {}
    patient_id = safe_profile.get("patient_id")
    patient_name = str(safe_profile.get("patient_name") or safe_profile.get("full_name") or "Patient")

    reply = build_patient_crisis_reply(safe_profile)
    caregiver_alerts: list[dict] = []
    if patient_id:
        caregiver_alerts = alerting.send_crisis_alerts(
            patient_id=str(patient_id),
            patient_name=patient_name,
            trigger_message=trigger_message,
            triggered_by_phone=str(safe_profile.get("phone") or ""),
            force=force_alerts,
        )

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    if elapsed_ms > int(getattr(config, "CRISIS_RUNTIME_BUDGET_MS", 900)):
        try:
            db.write_audit(
                patient_id=str(patient_id) if patient_id else None,
                profile_id=safe_profile.get("id"),
                entity_type="crisis_runtime",
                entity_id=None,
                action="CRISIS_RUNTIME_SLOW_PATH",
                actor_role="system",
                new_value={"elapsed_ms": elapsed_ms},
            )
        except Exception:
            pass

    return {
        "reply": reply,
        "caregiver_alerts": caregiver_alerts,
        "elapsed_ms": elapsed_ms,
        "mode": "fast_path",
    }


def crisis_readiness(patient_id: str | None) -> dict[str, Any]:
    if not patient_id:
        return {"status": "unknown", "score": 0, "issues": ["missing_patient_id"]}
    try:
        packet = crisis.get_emergency_packet(str(patient_id))
        return packet.get("quality") or crisis.score_crisis_card_quality(packet)
    except Exception:
        return {"status": "unknown", "score": 0, "issues": ["readiness_check_failed"]}
