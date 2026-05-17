"""
Non-mutating PharmaAgent live-tool verification.

This script reads DB rules and calls external sources directly without writing
interaction_cache, alerts, approvals, or pending tasks.
"""

from __future__ import annotations

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from scripts.verify import _bootstrap  # noqa: F401

import json
import time

import db
import pharma_research
from pharma_tools import OpenFDAClient, RxNavClient


TEST_PAIRS = [
    ("Warfarin", "Aspirin"),
    ("Amlodipine", "Simvastatin"),
    ("Metformin", "Furosemide"),
    ("Telmisartan", "Spironolactone"),
]


def _status_score(result: dict | None) -> float:
    if not isinstance(result, dict):
        return 0.0
    status = str(result.get("status") or "ok").lower()
    if status == "ok" and result.get("description"):
        return 1.0
    if status == "ok" and result.get("evidence"):
        return 0.8
    if status == "no_data":
        return 0.45
    if status == "error":
        return 0.15
    return 0.25


def _local_lookup(drug_a: str, drug_b: str) -> dict:
    result = pharma_research._local_rule_lookup(drug_a, drug_b)
    result["confidence_score"] = _status_score(result)
    return result


def _call_tool(name: str, callback) -> dict:
    start = time.time()
    try:
        result = callback()
        latency = int((time.time() - start) * 1000)
        if not isinstance(result, dict):
            result = {"source": name, "status": "no_data", "severity": "none", "description": "", "error": None}
        result = dict(result)
        result.setdefault("source", name)
        result.setdefault("status", "ok")
        result.setdefault("severity", "none")
        result.setdefault("description", "")
        result.setdefault("error", None)
        result["latency_ms"] = latency
        result["confidence_score"] = _status_score(result)
        return result
    except Exception as error:
        return {
            "source": name,
            "status": "error",
            "severity": "none",
            "description": "",
            "error": str(error),
            "latency_ms": int((time.time() - start) * 1000),
            "confidence_score": 0.0,
        }


def _pubmed(drug_a: str, drug_b: str) -> dict:
    result = pharma_research._pubmed_search_with_status(drug_a, drug_b)
    result["confidence_score"] = _status_score(result)
    return result


def _combined_confidence(results: list[dict]) -> float:
    best = max((_status_score(item) for item in results), default=0.0)
    evidence_sources = sum(1 for item in results if item.get("status") == "ok" and (item.get("description") or item.get("evidence")))
    return round(min(1.0, best + max(0, evidence_sources - 1) * 0.08), 2)


def main() -> None:
    print("=== PHARMA LIVE TOOL VERIFICATION ===")
    db_rules = db.get_all_drug_interactions()
    print(json.dumps({"db_active_rules": len(db_rules), "db_confidence_score": 1.0 if db_rules else 0.0}))

    rxnav = RxNavClient()
    openfda = OpenFDAClient()
    reports = []
    for drug_a, drug_b in TEST_PAIRS:
        local = _local_lookup(drug_a, drug_b)
        rx = _call_tool("rxnav", lambda a=drug_a, b=drug_b: rxnav.get_interaction(a, b))
        fda = _call_tool("openfda", lambda a=drug_a, b=drug_b: openfda.find_interaction(a, b))
        pubmed = _pubmed(drug_a, drug_b)
        results = [local, rx, fda, pubmed]
        reports.append(
            {
                "pair": f"{drug_a}+{drug_b}",
                "combined_confidence_score": _combined_confidence(results),
                "tools": [
                    {
                        "source": item.get("source"),
                        "status": item.get("status"),
                        "severity": item.get("severity"),
                        "confidence_score": item.get("confidence_score"),
                        "error": item.get("error"),
                        "latency_ms": item.get("latency_ms"),
                        "evidence_count": pharma_research._evidence_count(item.get("evidence")),
                    }
                    for item in results
                ],
            }
        )

    print(json.dumps(reports, indent=2, default=str))
    failing = [
        {"pair": report["pair"], "tool": tool}
        for report in reports
        for tool in report["tools"]
        if tool["status"] == "error"
    ]
    print(json.dumps({"failing_tools": failing, "overall_confidence_score": round(sum(r["combined_confidence_score"] for r in reports) / len(reports), 2)}))


if __name__ == "__main__":
    main()
