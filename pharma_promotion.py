"""
Layer 3 PharmaAgent rule promotion and veto orchestration.

Research reports remain evidence-first. This module is the only place that
turns a passed research report into a staged approval, caregiver notice, veto,
or production drug_interactions rule.
"""

from __future__ import annotations

import secrets

import config
import db
import notification_dispatcher


SEVERITY_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def stage_research_report_for_approval(research_key: str) -> dict:
    """
    Stage a passed pharma_research_report in agent_approvals.
    Returns a safe envelope and never raises.
    """
    try:
        if not getattr(config, "PHARMA_RULE_PROMOTION_ENABLED", True):
            return {"status": "disabled", "research_key": research_key}

        report = db.get_pharma_research_report_by_key(research_key)
        if not report:
            return {"status": "not_found", "research_key": research_key}

        gates = report.get("gates_result") or {}
        synthesis = report.get("synthesis") or {}
        severity = _normalize_severity(report.get("severity") or synthesis.get("severity"))
        confidence = _safe_float(report.get("confidence") or synthesis.get("confidence"))
        min_confidence = float(getattr(config, "PHARMA_RESEARCH_MIN_CONFIDENCE", 0.85))

        if not gates.get("all_passed"):
            db.update_pharma_research_report_status(research_key, "shadow_failed", "Safety gates did not pass")
            return {"status": "not_promoted", "reason": "gates_failed", "research_key": research_key}
        if confidence < min_confidence:
            db.update_pharma_research_report_status(research_key, "shadow_failed", "Confidence below promotion threshold")
            return {"status": "not_promoted", "reason": "low_confidence", "research_key": research_key}
        if severity == "none":
            db.update_pharma_research_report_status(research_key, "no_rule_needed", None)
            return {"status": "not_promoted", "reason": "no_interaction", "research_key": research_key}

        existing = db.get_agent_approval_by_rule_hash_or_prefix(research_key)
        if existing and existing.get("status") in {"vetoed", "finalized", "human_approved", "auto_approved"}:
            return {"status": "already_decided", "approval": existing, "research_key": research_key}

        human_required = severity in getattr(config, "PHARMA_RULE_HUMAN_REQUIRED_SEVERITIES", {"critical"})
        auto_eligible = (
            not human_required
            and bool(getattr(config, "PHARMA_RULE_AUTO_APPROVAL_ENABLED", True))
            and severity in getattr(config, "PHARMA_RULE_AUTO_APPROVAL_SEVERITIES", {"high"})
        )
        if not human_required and not auto_eligible:
            db.update_pharma_research_report_status(research_key, "shadow_passed", "Severity is not configured for approval promotion")
            return {"status": "not_promoted", "reason": "severity_not_promotable", "research_key": research_key}

        approval_id = db.create_agent_approval_from_research_report(report, auto_approve_eligible=auto_eligible)
        if not approval_id:
            db.update_pharma_research_report_status(research_key, "promotion_failed", "Could not create approval row")
            return {"status": "error", "error": "approval_create_failed", "research_key": research_key}

        next_status = "pending_veto" if auto_eligible else "pending_human_approval"
        db.update_pharma_research_report_status(research_key, next_status, None)
        challenge_code = _create_approval_challenge(research_key)
        notice = _notify_primary_caregiver(report, auto_eligible=auto_eligible, challenge_code=challenge_code)
        db.write_audit(
            patient_id=str(report.get("patient_id") or ""),
            profile_id=None,
            entity_type="agent_approvals",
            entity_id=approval_id,
            action="PHARMA_RULE_APPROVAL_STAGED",
            actor_role="system",
            new_value={
                "research_key": research_key,
                "rule_hash": research_key,
                "drug_a": report.get("drug_a"),
                "drug_b": report.get("drug_b"),
                "severity": severity,
                "confidence": confidence,
                "auto_approve_eligible": auto_eligible,
                "notice": notice,
            },
        )
        return {
            "status": "staged",
            "approval_id": approval_id,
            "rule_hash": research_key,
            "short_code": research_key[:8],
            "auto_approve_eligible": auto_eligible,
            "next_status": next_status,
            "notice": notice,
        }
    except Exception as error:
        try:
            db.update_pharma_research_report_status(research_key, "promotion_error", str(error))
        except Exception:
            pass
        return {"status": "error", "error": str(error), "research_key": research_key}


def process_due_auto_approvals(limit: int = 25) -> dict:
    """
    Finalize pending auto-eligible rules whose veto window has expired.
    """
    finalized = []
    errors = []
    try:
        for approval in db.get_due_auto_approvals(limit=limit):
            rule_hash = approval.get("rule_hash")
            if not rule_hash:
                continue
            if db.activate_drug_interaction_rule_from_approval(rule_hash):
                db.update_pharma_research_report_status(rule_hash, "active", None)
                db.write_audit(
                    patient_id=str((approval.get("gates_result") or {}).get("patient_id") or ""),
                    profile_id=None,
                    entity_type="agent_approvals",
                    entity_id=None,
                    action="PHARMA_RULE_AUTO_FINALIZED",
                    actor_role="system",
                    new_value={
                        "rule_hash": rule_hash,
                        "drug_a": approval.get("drug_a"),
                        "drug_b": approval.get("drug_b"),
                        "severity": approval.get("severity"),
                    },
                )
                finalized.append(rule_hash)
            else:
                errors.append({"rule_hash": rule_hash, "error": "activation_failed"})
        return {"status": "ok", "finalized": finalized, "errors": errors}
    except Exception as error:
        return {"status": "error", "error": str(error), "finalized": finalized, "errors": errors}


def finalize_approval(rule_hash_or_prefix: str, actor: str = "system") -> dict:
    """
    Human-approve/finalize a staged rule and activate it in drug_interactions.
    """
    try:
        approval = db.get_agent_approval_by_code_or_prefix(rule_hash_or_prefix)
        if not approval:
            return {"status": "not_found", "reply": "Approval row not found."}
        if approval.get("token_match") == "approval_code" and approval.get("code_status") != "valid":
            return {"status": "blocked", "reply": f"This approval code is {approval.get('code_status')}."}
        if approval.get("status") == "vetoed":
            return {"status": "blocked", "reply": "This rule was vetoed and cannot be finalized."}
        rule_hash = approval["rule_hash"]
        if not db.update_agent_approval_status(rule_hash, "human_approved"):
            return {"status": "error", "reply": "Could not mark rule approved."}
        if not db.activate_drug_interaction_rule_from_approval(rule_hash):
            return {"status": "error", "reply": "Could not activate interaction rule."}
        if approval.get("token_match") == "approval_code":
            db.mark_agent_approval_code_used(rule_hash)
        db.update_pharma_research_report_status(rule_hash, "active", None)
        _audit_decision(approval, "PHARMA_RULE_HUMAN_FINALIZED", actor)
        return {
            "status": "finalized",
            "rule_hash": rule_hash,
            "reply": f"Approved and activated rule {rule_hash[:8]}.",
        }
    except Exception as error:
        return {"status": "error", "reply": str(error)}


def veto_approval(rule_hash_or_prefix: str, actor: str = "system", reason: str | None = None) -> dict:
    """
    Veto a staged rule. Accepts full hash or short prefix.
    """
    try:
        approval = db.get_agent_approval_by_code_or_prefix(rule_hash_or_prefix)
        if not approval:
            return {"status": "not_found", "reply": "Approval row not found."}
        if approval.get("token_match") == "approval_code" and approval.get("code_status") != "valid":
            return {"status": "blocked", "reply": f"This veto code is {approval.get('code_status')}."}
        if approval.get("status") == "finalized":
            return {"status": "blocked", "reply": "This rule is already finalized."}
        rule_hash = approval["rule_hash"]
        if not db.update_agent_approval_status(rule_hash, "vetoed"):
            return {"status": "error", "reply": "Could not veto rule."}
        if approval.get("token_match") == "approval_code":
            db.mark_agent_approval_code_used(rule_hash)
        db.update_pharma_research_report_status(rule_hash, "vetoed", reason)
        _audit_decision(approval, "PHARMA_RULE_VETOED", actor, {"reason": reason})
        return {
            "status": "vetoed",
            "rule_hash": rule_hash,
            "reply": f"Veto recorded for PharmaAgent rule {rule_hash[:8]}.",
        }
    except Exception as error:
        return {"status": "error", "reply": str(error)}


def _create_approval_challenge(rule_hash: str) -> str | None:
    try:
        code = f"{secrets.randbelow(900000) + 100000}"
        hours = int(getattr(config, "PHARMAGENT_VETO_EXPIRY_HOURS", 48))
        if db.set_agent_approval_challenge(rule_hash, code, expiry_hours=hours):
            return code
    except Exception:
        pass
    return None


def _notify_primary_caregiver(report: dict, auto_eligible: bool, challenge_code: str | None = None) -> dict:
    patient_id = str(report.get("patient_id") or "")
    caregivers = db.get_caregivers(patient_id)
    primary = next((row for row in caregivers if row.get("role") == "primary_caregiver"), None)
    target = primary or (caregivers[0] if caregivers else {})
    phone = target.get("phone")
    message = _build_veto_notice(report, auto_eligible=auto_eligible, challenge_code=challenge_code)
    delivery = notification_dispatcher.dispatch_user_message(phone, message)
    delivered = delivery.get("status") in {"logged", "sent"}
    db.mark_agent_approval_veto_notified(str(report.get("research_key") or ""), delivered)
    return {"target_phone": phone, "delivery": delivery, "message": message, "challenge_code_issued": bool(challenge_code)}


def _build_veto_notice(report: dict, auto_eligible: bool, challenge_code: str | None = None) -> str:
    rule_hash = str(report.get("research_key") or "")
    synthesis = report.get("synthesis") or {}
    severity = _normalize_severity(report.get("severity") or synthesis.get("severity"))
    confidence = _safe_float(report.get("confidence") or synthesis.get("confidence"))
    hours = int(getattr(config, "PHARMAGENT_VETO_EXPIRY_HOURS", 48))
    summary = str(synthesis.get("summary") or "").strip()
    code = challenge_code or rule_hash[:8]
    if auto_eligible:
        action = f"Reply VETO {code} within {hours} hours to block auto-activation."
    else:
        action = f"This rule needs primary caregiver approval. Reply APPROVE {code} or VETO {code}."
    return (
        f"PharmaAgent review: {report.get('drug_a')} + {report.get('drug_b')} "
        f"looks {severity} risk (confidence {confidence:.2f}). "
        f"{summary[:180]} {action}"
    ).strip()


def _audit_decision(approval: dict, action: str, actor: str, extra: dict | None = None) -> None:
    payload = {
        "rule_hash": approval.get("rule_hash"),
        "drug_a": approval.get("drug_a"),
        "drug_b": approval.get("drug_b"),
        "severity": approval.get("severity"),
        "actor": actor,
    }
    if extra:
        payload.update(extra)
    db.write_audit(
        patient_id=str((approval.get("gates_result") or {}).get("patient_id") or ""),
        profile_id=None,
        entity_type="agent_approvals",
        entity_id=None,
        action=action,
        actor_role="primary_caregiver" if actor != "system" else "system",
        new_value=payload,
    )


def _normalize_severity(value: str | None) -> str:
    text = str(value or "none").strip().lower()
    return text if text in SEVERITY_RANK else "medium"


def _safe_float(value) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0
