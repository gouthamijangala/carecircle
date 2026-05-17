"""
Read-only database observability helpers.

No migrations are executed here. The module reports missing recommended indexes
and table availability so operators can apply safe SQL manually when ready.
"""

from __future__ import annotations

from typing import Any

import db


RECOMMENDED_INDEXES = {
    "idx_audit_log_patient_action_ts": "audit_log",
    "idx_alerts_patient_status_severity_created": "alerts",
    "idx_pending_tasks_status_created": "pending_tasks",
    "idx_pending_tasks_phone_status_created": "pending_tasks",
    "idx_media_uploads_patient_created": "media_uploads",
    "idx_medications_patient_status_drug": "medications",
    "idx_lab_reports_patient_test_report_date": "lab_reports",
    "idx_drug_interactions_pair_lower": "drug_interactions",
    "idx_agent_approvals_patient_expr": "agent_approvals",
}

RECOMMENDED_INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_audit_log_patient_action_ts ON audit_log(patient_id, action, timestamp DESC);",
    "CREATE INDEX IF NOT EXISTS idx_alerts_patient_status_severity_created ON alerts(patient_id, status, severity, created_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_pending_tasks_status_created ON pending_tasks(status, created_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_pending_tasks_phone_status_created ON pending_tasks(from_phone, status, created_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_media_uploads_patient_created ON media_uploads(patient_id, created_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_medications_patient_status_drug ON medications(patient_id, status, drug_name);",
    "CREATE INDEX IF NOT EXISTS idx_lab_reports_patient_test_report_date ON lab_reports(patient_id, test_name, report_date DESC);",
    "CREATE INDEX IF NOT EXISTS idx_drug_interactions_pair_lower ON drug_interactions(LOWER(drug_a), LOWER(drug_b));",
    "CREATE INDEX IF NOT EXISTS idx_agent_approvals_patient_expr ON agent_approvals((gates_result ->> 'patient_id'), created_at DESC);",
]


def database_health() -> dict[str, Any]:
    connection = None
    try:
        connection = db._connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public';
                """
            )
            tables = {row[0] for row in cursor.fetchall()}

            cursor.execute(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = 'public';
                """
            )
            indexes = {row[0] for row in cursor.fetchall()}

        missing_tables = sorted({table for table in RECOMMENDED_INDEXES.values()} - tables)
        missing_indexes = [
            {"index": name, "table": table}
            for name, table in RECOMMENDED_INDEXES.items()
            if name not in indexes and table in tables
        ]
        return {
            "status": "ok" if not missing_tables else "degraded",
            "tables_present": len(tables),
            "missing_tables": missing_tables,
            "missing_recommended_indexes": missing_indexes,
            "recommended_index_sql": RECOMMENDED_INDEX_SQL,
        }
    except Exception as error:
        return {
            "status": "error",
            "error": str(error),
            "missing_tables": [],
            "missing_recommended_indexes": [],
            "recommended_index_sql": RECOMMENDED_INDEX_SQL,
        }
    finally:
        if connection is not None:
            connection.close()
