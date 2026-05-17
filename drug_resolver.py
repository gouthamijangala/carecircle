"""
Shared drug-name resolution for ingestion and PharmaAgent.

The resolver is intentionally conservative: local formulary and known side-effect
aliases are preferred, RxNav is optional, and failures fall back to the raw name
with low confidence instead of blocking the care workflow.
"""

from __future__ import annotations

import re

import requests

import config
import db
from config import SIDE_EFFECT_HINTS

LOCAL_DRUG_ALIASES = {
    "telma": "telmisartan",
    "telma-am": "telmisartan amlodipine",
    "telma am": "telmisartan amlodipine",
    "ecosprin": "aspirin",
    "ecosprin av": "aspirin atorvastatin",
    "ecosprin atatac": "aspirin atorvastatin",
    "clopivas": "clopidogrel",
    "metoprodol": "metoprolol",
    "metoprodol succinate": "metoprolol succinate",
    "pantoprazde": "pantoprazole",
    "pantoprazde 40": "pantoprazole",
}

_INTERACTION_RULE_NAMES_CACHE: set[str] | None = None


def _clean_drug_text(value: str | None) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    text = re.sub(r"^(?:tab|cap|capsule|syrup|inj|susp)\.?\s+", "", text, flags=re.IGNORECASE)
    return text.strip(" .:-")


def resolve_drug_name(raw_name: str | None) -> tuple[str | None, float]:
    """
    Resolve a raw medication string to a canonical display name.
    Returns (canonical_name, confidence); never raises.
    """
    raw = _clean_drug_text(raw_name)
    if not raw:
        return None, 0.0
    alias = _local_alias(raw)
    if alias:
        return alias, 0.92

    rule_name = _interaction_rule_name(raw)
    if rule_name:
        return rule_name, 0.88

    cached = _cached_drug_resolution(raw)
    if cached:
        return cached, 0.95

    raw_lower = raw.lower()
    for drug, _symptom in SIDE_EFFECT_HINTS:
        drug_lower = str(drug or "").lower()
        if drug_lower and (drug_lower in raw_lower or raw_lower in drug_lower):
            return drug_lower, 0.80

    if getattr(config, "DRUG_RESOLVER_EXTERNAL_ENABLED", False):
        try:
            response = requests.get(
                f"{config.RXNAV_API_BASE}/approximateTerm.json",
                params={"term": raw, "maxEntries": 1},
                timeout=float(getattr(config, "DRUG_RESOLVER_EXTERNAL_TIMEOUT", 3)),
            )
            if response.status_code == 200:
                candidate = _extract_rxnav_candidate_name(response.json())
                if candidate:
                    db.upsert_drug_formulary(candidate, raw, resolved_from="rxnorm")
                    return candidate, 0.90
        except Exception:
            pass

    return raw, 0.50


def _local_alias(raw_name: str) -> str | None:
    cleaned = _clean_drug_text(raw_name).lower()
    cleaned = re.sub(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml|iu|units?)\b", "", cleaned).strip(" .:-")
    if cleaned in LOCAL_DRUG_ALIASES:
        return LOCAL_DRUG_ALIASES[cleaned]
    for alias, canonical in LOCAL_DRUG_ALIASES.items():
        if cleaned.startswith(alias + " ") or alias in cleaned:
            return canonical
    return None


def _interaction_rule_name(raw_name: str) -> str | None:
    """
    Treat exact active interaction-rule drug names as verified local vocabulary.
    This keeps common drugs such as warfarin/aspirin from falling to low
    confidence when the external resolver is disabled.
    """
    cleaned = _clean_drug_text(raw_name).lower()
    if not cleaned:
        return None
    global _INTERACTION_RULE_NAMES_CACHE
    try:
        if _INTERACTION_RULE_NAMES_CACHE is None:
            names = set()
            for row in db.get_all_drug_interactions():
                for key in ("drug_a", "drug_b"):
                    value = str(row.get(key) or "").strip().lower()
                    if value:
                        names.add(value)
            _INTERACTION_RULE_NAMES_CACHE = names
        if cleaned in _INTERACTION_RULE_NAMES_CACHE:
            return cleaned
    except Exception:
        return None
    return None


def _cached_drug_resolution(raw_name: str) -> str | None:
    """
    Check optional helper hooks, then the drug_formulary table directly.
    """
    raw = _clean_drug_text(raw_name)
    if not raw:
        return None

    for helper_name in ("get_from_formulary",):
        try:
            helper = getattr(db, helper_name, None)
            if callable(helper):
                cached = helper(raw)
                if isinstance(cached, dict) and cached.get("canonical_name"):
                    return str(cached["canonical_name"])
                if isinstance(cached, str):
                    return cached
        except Exception:
            pass

    connection = None
    try:
        connection = db._connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT canonical_name
                FROM drug_formulary
                WHERE LOWER(canonical_name) = LOWER(%s)
                   OR LOWER(%s) = ANY(SELECT LOWER(alias) FROM unnest(common_aliases) AS alias)
                ORDER BY use_count DESC, last_used DESC
                LIMIT 1;
                """,
                (raw, raw),
            )
            row = cursor.fetchone()
        return str(row[0]) if row else None
    except Exception:
        return None
    finally:
        if connection is not None:
            connection.close()


def _extract_rxnav_candidate_name(data: dict) -> str | None:
    try:
        group = data.get("approximateGroup") or {}
        candidates = group.get("candidate") or []
        for candidate in candidates:
            name = candidate.get("name")
            if name:
                return str(name)

        concept_groups = group.get("conceptGroup") or []
        for concept_group in concept_groups:
            properties = concept_group.get("conceptProperties") or []
            if properties and properties[0].get("name"):
                return str(properties[0]["name"])
    except Exception:
        return None
    return None
