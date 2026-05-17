"""
Small external lookup clients for Pharma Agent interaction checks.

The public tool first checks the local interaction_cache table, then tries
RxNav, then OpenFDA. All network and database failures are intentionally
non-fatal because the offline drug_interactions table remains the primary
clinical safety path.
"""

from __future__ import annotations

import re
import time

import requests

import config
import db
import llm_gateway


def _clean_drug_name(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _pair_key(drug_a: str, drug_b: str) -> tuple[str, str]:
    a = _clean_drug_name(drug_a).lower()
    b = _clean_drug_name(drug_b).lower()
    return tuple(sorted((a, b)))


def _severity_from_text(text: str) -> str:
    lowered = str(text or "").lower()
    high_terms = [
        "contraindicated",
        "avoid",
        "severe",
        "serious",
        "life-threatening",
        "fatal",
        "major",
    ]
    return "high" if any(term in lowered for term in high_terms) else "medium"


def _normalize_source_label(source: str | None) -> str:
    text = str(source or "").strip()
    if text.startswith("multi_source:"):
        text = text.split(":", 1)[1]
    parts = []
    for item in re.split(r"[, ]+", text):
        label = item.strip()
        if label and label not in parts:
            parts.append(label)
    return ",".join(parts) if parts else "unknown"


def _empty_tool_result(source: str, status: str = "no_data", error: str | None = None, latency_ms: int = 0) -> dict:
    return {
        "source": source,
        "status": status,
        "severity": "none",
        "description": "",
        "evidence": None,
        "error": error,
        "latency_ms": latency_ms,
    }


def _timed_tool_call(source: str, callback) -> dict:
    start = time.time()
    try:
        result = callback()
        latency = int((time.time() - start) * 1000)
        if not isinstance(result, dict):
            return _empty_tool_result(source, latency_ms=latency)
        enriched = dict(result)
        enriched.setdefault("source", source)
        enriched.setdefault("status", "ok")
        enriched.setdefault("severity", "medium")
        enriched.setdefault("description", "")
        enriched.setdefault("evidence", None)
        enriched.setdefault("error", None)
        enriched["latency_ms"] = latency
        return enriched
    except Exception as error:
        latency = int((time.time() - start) * 1000)
        return _empty_tool_result(source, status="error", error=str(error), latency_ms=latency)


class RxNavClient:
    def __init__(self):
        self.base = "https://rxnav.nlm.nih.gov/REST"

    def _resolve_rxcui(self, drug: str) -> str | None:
        try:
            name = _clean_drug_name(drug)
            if not name:
                return None
            response = requests.get(
                f"{self.base}/rxcui.json",
                params={"name": name},
                timeout=10,
            )
            if response.status_code != 200:
                return None
            data = response.json()
            rxcuis = (data.get("idGroup") or {}).get("rxnormId") or []
            return str(rxcuis[0]) if rxcuis else None
        except Exception:
            return None

    def get_interaction(self, drug_a: str, drug_b: str) -> dict | None:
        """Call RxNav interaction API. Return parsed dict or None."""
        try:
            rxcui_a = self._resolve_rxcui(drug_a)
            rxcui_b = self._resolve_rxcui(drug_b)
            if not rxcui_a or not rxcui_b:
                return None
            if not getattr(config, "RXNAV_INTERACTION_API_ENABLED", False):
                return {
                    "status": "skipped",
                    "severity": "none",
                    "description": "",
                    "source": "rxnav",
                    "evidence": {"rxcui_a": rxcui_a, "rxcui_b": rxcui_b},
                    "error": "rxnav_interaction_api_disabled_or_discontinued",
                }

            response = requests.get(
                f"{self.base}/interaction/list.json",
                params={"rxcuis": f"{rxcui_a}+{rxcui_b}"},
                timeout=10,
            )
            if response.status_code != 200:
                return {
                    "status": "error",
                    "severity": "none",
                    "description": "",
                    "source": "rxnav",
                    "evidence": {"rxcui_a": rxcui_a, "rxcui_b": rxcui_b},
                    "error": f"http_{response.status_code}: {response.text[:120]}",
                }

            data = response.json()
            groups = data.get("fullInteractionTypeGroup") or []
            descriptions = []
            severities = []
            for group in groups:
                for interaction_type in group.get("fullInteractionType", []) or []:
                    for pair in interaction_type.get("interactionPair", []) or []:
                        description = pair.get("description")
                        severity = pair.get("severity")
                        if description:
                            descriptions.append(str(description))
                        if severity:
                            severities.append(str(severity).lower())

            if not descriptions:
                return None

            text = " ".join(descriptions)
            severity = "high" if any(item in {"high", "major", "serious"} for item in severities) else _severity_from_text(text)
            return {
                "severity": severity,
                "description": text,
                "source": "rxnav",
                "evidence": {
                    "rxcui_a": rxcui_a,
                    "rxcui_b": rxcui_b,
                    "descriptions": descriptions[:5],
                    "raw_severities": severities[:5],
                },
            }
        except Exception:
            return None


class OpenFDAClient:
    def __init__(self):
        self.base = "https://api.fda.gov/drug/label.json"

    def find_interaction(self, drug_a: str, drug_b: str) -> dict | None:
        """Call OpenFDA drug label API. Return parsed dict or None."""
        try:
            a = _clean_drug_name(drug_a)
            b = _clean_drug_name(drug_b)
            if not a or not b:
                return None

            search_attempts = [
                f'openfda.generic_name:"{a}" AND drug_interactions:"{b}"',
                f'openfda.generic_name:"{b}" AND drug_interactions:"{a}"',
                f'drug_interactions:"{a}" AND drug_interactions:"{b}"',
            ]
            last_error = None
            for search in search_attempts:
                response = requests.get(
                    self.base,
                    params={"search": search, "limit": 3},
                    timeout=10,
                )
                if response.status_code == 404:
                    continue
                if response.status_code != 200:
                    last_error = f"http_{response.status_code}: {response.text[:160]}"
                    continue

                data = response.json()
                results = data.get("results") or []
                if not results:
                    continue

                interaction_blocks = []
                for row in results:
                    value = row.get("drug_interactions") or []
                    if isinstance(value, str):
                        value = [value]
                    interaction_blocks.extend(str(item) for item in value if item)
                description = " ".join(interaction_blocks)
                if not description:
                    continue

                return {
                    "severity": _severity_from_text(description),
                    "description": description,
                    "source": "openfda",
                    "evidence": {
                        "search": search,
                        "result_count": len(results),
                        "drug_interactions": interaction_blocks[:3],
                    },
                }

            if last_error:
                return {"status": "error", "severity": "none", "description": "", "source": "openfda", "evidence": None, "error": last_error}
            return None
        except Exception:
            return None


class DrugInteractionTool:
    def __init__(self):
        self.rxnav = RxNavClient()
        self.openfda = OpenFDAClient()

    def check_interaction(self, drug_a: str, drug_b: str) -> dict | None:
        """Return the strongest result across available interaction sources."""
        results = self.check_all_interactions(drug_a, drug_b)
        merged = results.get("merged") if isinstance(results, dict) else None
        return merged if isinstance(merged, dict) else None

    def check_all_interactions(self, drug_a: str, drug_b: str) -> dict:
        """Call RxNav and OpenFDA independently, then merge by highest severity."""
        a, b = _pair_key(drug_a, drug_b)
        if not a or not b:
            empty = {
                "cached": _empty_tool_result("interaction_cache", status="skipped", error="missing_drug_name"),
                "rxnav": _empty_tool_result("rxnav", status="skipped", error="missing_drug_name"),
                "openfda": _empty_tool_result("openfda", status="skipped", error="missing_drug_name"),
                "merged": None,
            }
            empty["tool_attempts"] = [empty["cached"], empty["rxnav"], empty["openfda"]]
            return empty

        cached = self._get_cached_interaction(a, b)
        results = {
            "cached": cached or _empty_tool_result("interaction_cache"),
            "rxnav": _empty_tool_result("rxnav"),
            "openfda": _empty_tool_result("openfda"),
            "merged": cached if cached and cached.get("status") == "ok" else None,
        }

        try:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=2) as executor:
                future_rxnav = executor.submit(_timed_tool_call, "rxnav", lambda: self.rxnav.get_interaction(drug_a, drug_b))
                future_openfda = executor.submit(_timed_tool_call, "openfda", lambda: self.openfda.find_interaction(drug_a, drug_b))
                try:
                    results["rxnav"] = future_rxnav.result(timeout=12)
                except Exception:
                    results["rxnav"] = _empty_tool_result("rxnav", status="error", error="timeout")
                try:
                    results["openfda"] = future_openfda.result(timeout=12)
                except Exception:
                    results["openfda"] = _empty_tool_result("openfda", status="error", error="timeout")
        except Exception:
            results["rxnav"] = _timed_tool_call("rxnav", lambda: self.rxnav.get_interaction(drug_a, drug_b))
            results["openfda"] = _timed_tool_call("openfda", lambda: self.openfda.find_interaction(drug_a, drug_b))

        merged = self._merge_source_results(results)
        if merged and not bool(merged.get("cached")):
            self._cache_interaction(a, b, merged, merged.get("source") or "multi_source")
            results["merged"] = merged
        results["tool_attempts"] = [
            results.get("cached") or _empty_tool_result("interaction_cache"),
            results.get("rxnav") or _empty_tool_result("rxnav"),
            results.get("openfda") or _empty_tool_result("openfda"),
        ]
        return results

    def _merge_source_results(self, results: dict) -> dict | None:
        candidates = []
        for source in ("rxnav", "openfda", "cached"):
            item = results.get(source)
            if isinstance(item, dict) and item.get("status") == "ok":
                candidate = dict(item)
                candidate["source"] = item.get("source") or source
                candidates.append(candidate)
        if not candidates:
            return None
        rank = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
        best = max(candidates, key=lambda item: rank.get(str(item.get("severity") or "medium").lower(), 2))
        sources = []
        for item in candidates:
            for source in _normalize_source_label(item.get("source")).split(","):
                if source and source != "unknown" and source not in sources:
                    sources.append(source)
        best["sources_checked"] = sources
        if not bool(best.get("cached")):
            best["source"] = "multi_source:" + ",".join(sources)
        return best

    def _get_cached_interaction(self, drug_a: str, drug_b: str) -> dict | None:
        connection = None
        try:
            connection = db._connect()
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT severity, description, source
                    FROM interaction_cache
                    WHERE (LOWER(drug_a) = %s AND LOWER(drug_b) = %s)
                       OR (LOWER(drug_a) = %s AND LOWER(drug_b) = %s)
                    ORDER BY cached_at DESC
                    LIMIT 1;
                    """,
                    (drug_a, drug_b, drug_b, drug_a),
                )
                row = cursor.fetchone()
            if not row:
                return None
            return {
                "severity": row[0],
                "description": row[1],
                "source": row[2],
                "cached": True,
                "status": "ok",
                "evidence": {"cache_hit": True},
                "error": None,
                "latency_ms": 0,
            }
        except Exception:
            return None
        finally:
            if connection is not None:
                connection.close()

    def _cache_interaction(self, drug_a: str, drug_b: str, result: dict, source: str) -> None:
        connection = None
        try:
            connection = db._connect()
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO interaction_cache (drug_a, drug_b, severity, description, source, cached_at)
                    VALUES (%s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (drug_a, drug_b) DO UPDATE SET
                        severity = EXCLUDED.severity,
                        description = EXCLUDED.description,
                        source = EXCLUDED.source,
                        cached_at = NOW();
                    """,
                    (
                        drug_a,
                        drug_b,
                        result.get("severity"),
                        result.get("description"),
                        "multi_source:" + _normalize_source_label(source),
                    ),
                )
            connection.commit()
        except Exception:
            if connection is not None:
                connection.rollback()
        finally:
            if connection is not None:
                connection.close()


class ModelRouter:
    """Route LLM calls to appropriate Pharma Agent models."""

    def __init__(self):
        self.primary_config = config.PHARMA_PRIMARY_MODEL
        self.reasoning_config = config.PHARMA_REASONING_MODEL
        self.safety_config = config.PHARMA_SAFETY_MODEL
        self._gateway_router = llm_gateway.ModelRouter()

    def call_primary(self, prompt: str, schema: dict | None = None) -> dict | None:
        """Call primary model (Qwen3-4B) for structured extraction."""
        return self._gateway_router.call_primary(prompt, schema)

    def call_reasoning(self, prompt: str, max_context: int = 2000) -> str | None:
        """Call reasoning model (Nemotron Super) for complex synthesis."""
        return self._gateway_router.call_reasoning(prompt, max_context)

    def call_safety_check(self, content: str) -> dict:
        """Call safety model (Llama Guard) for content moderation."""
        result = self._gateway_router.call_safety_check(content)
        if "safe" in result and "hazard_categories" in result:
            return result

        safe = bool(result.get("allowed"))
        risk = str(result.get("risk") or "unknown")
        hazards = [] if safe else [risk]
        return {
            "safe": safe,
            "hazard_categories": hazards,
            "reason": str(result.get("reason") or ""),
            "source": result.get("source") or self.safety_config.get("model_id"),
        }

    def _cache_interaction(self, drug_a: str, drug_b: str, result: dict, source: str) -> None:
        connection = None
        try:
            connection = db._connect()
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO interaction_cache (drug_a, drug_b, severity, description, source, cached_at)
                    VALUES (%s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (drug_a, drug_b) DO UPDATE SET
                        severity = EXCLUDED.severity,
                        description = EXCLUDED.description,
                        source = EXCLUDED.source,
                        cached_at = NOW();
                    """,
                    (
                        drug_a,
                        drug_b,
                        result.get("severity"),
                        result.get("description"),
                        source,
                    ),
                )
            connection.commit()
        except Exception:
            if connection is not None:
                connection.rollback()
        finally:
            if connection is not None:
                connection.close()
