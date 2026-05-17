"""
Read-only PharmaAgent operations monitor.

Checks the same signals we care about during rollout:
- rules loaded and self-learning enabled
- health endpoint status when the API server is running
- recent PharmaAgent audit activity
- unprocessed caregiver feedback waiting for rule review/escalation
- current tuning thresholds
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.error import URLError
from urllib.request import urlopen

import config
import db


DEFAULT_HEALTH_URL = "http://localhost:8000/health/pharma"


def _health_endpoint(url: str = DEFAULT_HEALTH_URL) -> dict:
    try:
        with urlopen(url, timeout=8) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, json.JSONDecodeError) as error:
        return {"status": "unavailable", "detail": str(error)}


def _recent_pharma_audit(limit: int = 10) -> list[dict]:
    connection = None
    try:
        connection = db._connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT timestamp::text, action, COALESCE(new_value, '{}'::jsonb)
                FROM audit_log
                WHERE action IN ('PHARMA_AGENT_DECISION', 'PHARMA_AGENT_ERROR')
                   OR entity_type = 'pharma_agent'
                ORDER BY timestamp DESC
                LIMIT %s;
                """,
                (int(limit or 10),),
            )
            rows = cursor.fetchall()
        return [
            {
                "timestamp": row[0],
                "action": row[1],
                "drug": (row[2] or {}).get("new_drug"),
                "severity": (row[2] or {}).get("max_severity"),
                "trigger": (row[2] or {}).get("trigger"),
            }
            for row in rows
        ]
    except Exception as error:
        return [{"error": str(error)}]
    finally:
        if connection is not None:
            connection.close()


def _status() -> dict:
    rules = db.get_all_drug_interactions()
    feedback = db.get_unprocessed_feedback()
    health = _health_endpoint()
    audit_rows = _recent_pharma_audit()
    recent_drugs = [row.get("drug") for row in audit_rows if row.get("drug")]

    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "health_endpoint": health,
        "rules_loaded": len(rules),
        "pharma_agent_enabled": bool(getattr(config, "PHARMA_AGENT_ENABLED", False)),
        "self_learning_enabled": bool(getattr(config, "PHARMA_SELF_LEARNING_ENABLED", False)),
        "unprocessed_feedback_count": len(feedback),
        "recent_pharma_audit": audit_rows,
        "recent_drugs": recent_drugs[:5],
        "thresholds": {
            "PHARMA_EXPLANATION_CONFIDENCE_THRESHOLD": getattr(
                config,
                "PHARMA_EXPLANATION_CONFIDENCE_THRESHOLD",
                None,
            ),
            "PHARMA_MIN_FEEDBACK_COUNT": getattr(config, "PHARMA_MIN_FEEDBACK_COUNT", None),
            "PHARMA_FEEDBACK_CONFIDENCE_THRESHOLD": getattr(
                config,
                "PHARMA_FEEDBACK_CONFIDENCE_THRESHOLD",
                None,
            ),
        },
        "recommendations": _recommendations(rules, feedback, health, audit_rows),
    }


def _recommendations(
    rules: list[dict],
    feedback: list[dict],
    health: dict,
    audit_rows: list[dict],
) -> list[str]:
    items = []
    if not rules:
        items.append("Seed drug_interactions before enabling PharmaAgent.")
    if not getattr(config, "PHARMA_AGENT_ENABLED", False):
        items.append("Set PHARMA_AGENT_ENABLED=true to activate checks.")
    if not getattr(config, "PHARMA_SELF_LEARNING_ENABLED", False):
        items.append("Set PHARMA_SELF_LEARNING_ENABLED=true to process caregiver feedback.")
    if health.get("pharma_agent_status") not in {"healthy", None}:
        items.append("Review /health/pharma diagnostics.")
    if len(feedback) >= int(getattr(config, "PHARMA_MIN_FEEDBACK_COUNT", 5)):
        items.append("Review unprocessed feedback; repeated vetoes may escalate rule severity.")
    if not any(row.get("drug") for row in audit_rows):
        items.append("No recent PharmaAgent decisions found; upload/add a medication to confirm triggers.")
    return items


def main() -> None:
    report = _status()
    print(json.dumps(report, indent=2, default=str))
    if not report["pharma_agent_enabled"] or report["rules_loaded"] <= 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
