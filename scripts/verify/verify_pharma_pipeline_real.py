"""
Real PharmaAgent pipeline verification.

This script verifies live DB wiring, deterministic safety, live tool status,
registry verification writes, and caregiver approval helpers without creating
patient medications or alerts.
"""

from __future__ import annotations

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from scripts.verify import _bootstrap  # noqa: F401

import json

import config
import db
import pharma_research
from pharma_agent import PharmaSafetyEngine
from pharma_tools import DrugInteractionTool


def _query_one(sql: str, params=()):
    conn = db._connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()
    finally:
        conn.close()


def _query_all(sql: str, params=()):
    conn = db._connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def main() -> None:
    print("=== PHARMA PIPELINE REAL VERIFICATION ===")

    schema_ready = db.ensure_pharma_rule_registry_schema()
    research_ready = db.ensure_pharma_research_tables()
    trigger_row = _query_one(
        """
        SELECT tgname
        FROM pg_trigger
        WHERE tgname = 'trg_enqueue_pharma_medication_check'
          AND NOT tgisinternal;
        """
    )
    active_rule_count = len(db.get_all_drug_interactions())
    registry_summary = db.get_drug_interaction_registry_summary()
    med_count = int((_query_one("SELECT COUNT(*) FROM medications;") or [0])[0] or 0)
    active_med_count = int((_query_one("SELECT COUNT(*) FROM medications WHERE status = 'active';") or [0])[0] or 0)

    engine = PharmaSafetyEngine()
    deterministic = engine.evaluate(
        "d0000001-0002-0001-0001-000000000001",
        "Warfarin",
        {
            "active_meds": [{"drug_name": "Aspirin"}],
            "conditions": [],
            "renal_markers": None,
            "skip_external": True,
        },
    )
    assert deterministic["max_severity"] == "critical", deterministic

    live_tools = DrugInteractionTool().check_all_interactions("Warfarin", "Aspirin")
    tool_statuses = {
        "rxnav": (live_tools.get("rxnav") or {}).get("status"),
        "openfda": (live_tools.get("openfda") or {}).get("status"),
        "merged": (live_tools.get("merged") or {}).get("status") if live_tools.get("merged") else None,
    }

    registry_verify = pharma_research.verify_drug_interaction_registry(limit=1, force=False)
    approval_columns = _query_all(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'agent_approvals'
          AND column_name IN (
              'approval_code_hash',
              'approval_code_expires_at',
              'approval_code_used_at',
              'approval_code_attempts'
          )
        ORDER BY column_name;
        """
    )

    report = {
        "config": {
            "PHARMA_AGENT_ENABLED": config.PHARMA_AGENT_ENABLED,
            "PHARMA_RESEARCH_ENABLED": config.PHARMA_RESEARCH_ENABLED,
            "PHARMA_RULE_PROMOTION_ENABLED": config.PHARMA_RULE_PROMOTION_ENABLED,
        },
        "db": {
            "schema_ready": schema_ready,
            "research_tables_ready": research_ready,
            "trigger_present": bool(trigger_row),
            "active_rule_count": active_rule_count,
            "registry_summary": registry_summary,
            "medications_count": med_count,
            "active_medications_count": active_med_count,
            "approval_challenge_columns": [row[0] for row in approval_columns],
        },
        "deterministic_engine": {
            "warfarin_plus_aspirin_max_severity": deterministic["max_severity"],
            "interactions_count": len(deterministic.get("interactions", [])),
        },
        "live_tools": tool_statuses,
        "registry_verification": registry_verify,
    }

    print(json.dumps(report, indent=2, default=str))
    assert schema_ready and research_ready
    assert trigger_row
    assert active_rule_count > 0
    assert len(approval_columns) == 4
    print("PHARMA_PIPELINE_REAL_VERIFICATION_PASS")


if __name__ == "__main__":
    main()
