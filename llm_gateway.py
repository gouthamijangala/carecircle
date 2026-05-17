"""
Unified LLM gateway for CareCircle extraction workflows.

This module is the only media-ingestion entrypoint that talks to extraction
LLMs. It owns model health, model selection, chunking, strict prompts, retry,
JSON parsing, schema normalization, and chunk-result merging.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import config
import llm_policy


MEDICATION_SCHEMA_TEMPLATE = """
You are a medical data extractor. You MUST output ONLY a JSON object.
Do not add markdown, explanations, or commentary.

Required JSON structure (use null if a field is not in the text):
{
  "type": "prescription",
  "medications": [
    {
      "drug_name_raw": "exact name as written",
      "drug_name_canonical": null,
      "dose_amount": null,
      "dose_unit": null,
      "frequency": null,
      "instructions": null,
      "advice": null,
      "prescribed_by": null,
      "start_date": null,
      "date": null
    }
  ],
  "doctor_name": null,
  "date": null,
  "start_date": null
}

RULES:
- dose_amount must be a number (int or float), not a string.
- frequency must be one of: OD, BD, TDS, QID, SOS, HS, once daily, twice daily.
- Extract tablets, capsules, injections, syrups, suspensions, drops, creams, ointments, sprays, inhalers, nebulization solutions, sachets, vials, ampoules, and any other medication form.
- Put medication-specific doctor advice in advice. Keep route/timing instructions in instructions.
- The prescription date is the medication start_date. Copy it to top-level start_date and each medication start_date when visible.
- prescribed_by should be the doctor's specialty or department if visible, not the doctor's personal name.
- If a medication is not found, return {"medications": []}.
- NEVER invent a drug name. If unclear, set drug_name_raw to null.
"""


LAB_SCHEMA_TEMPLATE = """
You are a lab report extractor. You MUST output ONLY a JSON object.

Required JSON structure:
{
  "type": "lab_report",
  "lab_values": [
    {
      "test_name": "exact test name",
      "test_value": null,
      "unit": null,
      "reference_range_low": null,
      "reference_range_high": null
    }
  ],
  "lab_name": null,
  "report_date": null
}

RULES:
- test_value must be a number, never a string.
- test_name must be normalized: lowercase, spaces replaced with underscores.
  Examples: "HbA1c" -> "hba1c", "Blood Sugar Fasting" -> "glucose",
  "Creatinine Serum" -> "creatinine", "Blood Pressure" -> "blood_pressure_systolic".
- If a value has two numbers (e.g., "140/90"), extract systolic as test_value
  and set unit to "/90 mmHg" with a second entry for diastolic.
- NEVER invent a value. If missing, use null.
"""


NOTE_SCHEMA_TEMPLATE = """
You are a caregiver note extractor. Output ONLY JSON.

Required structure:
{
  "type": "general_note",
  "events": [
    {
      "event_type": "symptom|diet|sleep|activity|mood|other",
      "description": null,
      "time_of_day": null
    }
  ]
}
"""


EXTRACTION_SYSTEM_PROMPT = """You are a deterministic medical data extractor.
Your ONLY job is to convert unstructured text into a strict JSON object.
You are NOT a conversational assistant.
Do NOT explain. Do NOT add markdown. Do NOT apologize.
Do NOT add fields not in the schema. Do NOT omit fields in the schema.
--
OUTPUT RULES
--
1. Output MUST be valid JSON only.
No markdown fences. No ```json. No trailing commas. No comments.

2. Every key in the schema MUST appear in output.
Use null if value not found. Never omit a schema key.

3. NEVER invent data.
If a field is not present in the text, set it to null.
Do not infer, assume, or extrapolate any value.

4. For medications: extract EVERY drug mentioned.
Do not skip any. Include drugs in instructions, footnotes, or margins.

5. For lab reports: extract EVERY test result mentioned.
Do not skip any. Include results in footers or handwritten annotations.

6. drug_name_raw must be EXACTLY as written in the source text.
Preserve original spelling, capitalisation, and script errors.

7. dose_amount must be a NUMBER (int or float). Never a string.
If ambiguous OCR (e.g., "l.5" that could be 1.5 or 15), set to null
and set ocr_flag: true on that entry.

8. test_value must be a NUMBER. Never a string.
If value contains a symbol like ">" or "<", extract the number only
and store the operator in a separate test_operator field.

9. If a value has a range (e.g., BP "140/90"), extract as two entries:
systolic and diastolic separately, or as defined in the schema.

10. If text is in Hinglish or regional Indian languages (Tamil, Telugu,
Kannada, Malayalam, Marathi, Bengali), extract drug names and
instructions as-is in drug_name_raw and instructions_raw.
Do NOT transliterate or translate unless a translated field exists
in the schema.

11. Handle Indian prescription formats including:
- Handwritten OCR noise (smudges, overwriting, strike-throughs)
- Brand names (e.g., Glycomet, Ecosprin, Amlong, Telma-AM)
- Doctor shorthand (e.g., "w/f" = with food, "sos" = as needed)

12. For combination drugs (e.g., Telma-AM, Gluconorm-G2):
- Set drug_name_raw exactly as written.
- Set drug_name_canonical to the primary ingredient only if unambiguous.
- Do NOT decompose into separate entries unless the prescription
explicitly lists components as separate line items.
- Include suspected ingredients in instructions_raw only if visible
in the source text.

13. Mark any unclear medication, dose, unit, or frequency as null.
Never guess. When OCR noise is suspected on a field, set
ocr_flag: true on that specific entry only.

14. Preserve source numbers exactly.
Do not invent lab values, renal markers, or interaction claims.
Copy numeric values character-for-character from source.

15. For frequency and route, normalise to standard abbreviations:
BD or BID → "BD", TDS or TID → "TDS", OD or QD → "OD",
QID → "QID", SOS or PRN → "SOS", HS → "HS".
If frequency is written in a regional language, preserve it in
instructions_raw and set frequency to null.

16. If text contains crossed-out, struck-through, or corrected values:
Extract ONLY the final uncrossed value.
Set ocr_flag: true and add a correction_note field with the
crossed-out value preserved as a string.

17. If a medication or lab entry is illegible beyond recovery:
Include it in the array with all fields set to null
and set illegible: true. Do NOT skip it entirely.

18. Preserve array order exactly as items appear top-to-bottom,
left-to-right in the source document.
Do NOT sort, reorder, or deduplicate entries.
If the same drug appears twice (e.g., morning and evening dose),
emit two separate entries.

19. If the input text is empty, whitespace only, or clearly not a
medical document, return this exact JSON and nothing else:
{"error": "non_medical_input", "medications": [], "lab_results": []}

20. If the total number of extracted items exceeds 30 medications or
30 lab results, emit all of them. Never truncate silently.
Add "truncation_warning": false at the root level.
If you genuinely cannot fit all within your output limit,
set "truncation_warning": true and include as many as possible
starting from the first item in document order."""


def _openrouter_key() -> str | None:
    try:
        return (
            getattr(config, "LLM_EXTRACTION_API_KEY", None)
            or getattr(config, "_ENV", {}).get("OPENROUTER_API_KEY")
        )
    except Exception:
        return None


def _openai_base_url(endpoint: str) -> str:
    endpoint = str(endpoint or "").rstrip("/")
    suffix = "/chat/completions"
    if endpoint.endswith(suffix):
        return endpoint[: -len(suffix)]
    return endpoint


MODEL_REGISTRY = [
    {
        "name": "local_qwen3",
        "endpoint": config.LLM_EXTRACTION_PRIMARY,
        "model_id": config.LLM_EXTRACTION_PRIMARY_MODEL,
        "api_key": "lm-studio",
	"prompt_suffix": "/think",
        "timeout": 120,
        "max_tokens": 800,
        "context_limit": 32000,
        "is_free": True,
        "priority": 1,
    },
    {
        "name": "openrouter_free",
        "endpoint": config.LLM_EXTRACTION_FALLBACK,
        # Verified via OpenRouter model registry on 2026-05-10. The older
        # config value google/gemini-flash-1.5 currently returns 404.
        "model_id": "openrouter/free",
        "api_key": _openrouter_key(),
        "timeout": 20,
        "max_tokens": 1000,
        "context_limit": 128000,
        "is_free": True,
        "priority": 2,
    },
    {
        "name": "openrouter_nvidia",
        "endpoint": "https://openrouter.ai/api/v1",
        # Verified free NVIDIA fallback; replaces invalid llama-3.1-nemotron-70b id.
        "model_id": "nvidia/nemotron-3-nano-30b-a3b:free",
        "api_key": _openrouter_key(),
        "timeout": 20,
        "max_tokens": 1000,
        "context_limit": 128000,
        "is_free": True,
        "priority": 3,
    },
]


_model_health: dict[str, dict[str, Any]] = {}
_health_checked_at: dict[str, datetime] = {}
_HEALTH_TTL_SECONDS = 300


def test_model_health(model_config: dict) -> dict:
    """
    Send a tiny ping prompt to test if model is responsive.
    Returns {"alive": bool, "latency_ms": int, "error": str|None}.
    """
    try:
        if not model_config.get("endpoint") or not model_config.get("model_id"):
            return {"alive": False, "latency_ms": 99999, "error": "missing endpoint/model"}
        if "openrouter" in str(model_config.get("name")) and not model_config.get("api_key"):
            return {"alive": False, "latency_ms": 99999, "error": "missing OpenRouter API key"}

        start = time.time()
        from openai import OpenAI

        client = OpenAI(
            base_url=_openai_base_url(model_config["endpoint"]),
            api_key=model_config["api_key"],
            timeout=model_config["timeout"],
        )
        resp = client.chat.completions.create(
            model=model_config["model_id"],
            messages=[{"role": "user", "content": "Reply ONLY: pong"}],
            max_tokens=5,
            temperature=0,
        )
        latency = int((time.time() - start) * 1000)
        text = str(resp.choices[0].message.content or "").strip().lower()
        return {"alive": "pong" in text, "latency_ms": latency, "error": None}
    except Exception as error:
        return {"alive": False, "latency_ms": 99999, "error": str(error)}


def refresh_all_model_health() -> list[dict]:
    """
    Test every model in registry. Return sorted list by priority and health.
    """
    global _model_health
    results = []
    now = datetime.now(timezone.utc)
    for model in MODEL_REGISTRY:
        health = test_model_health(model)
        _model_health[model["name"]] = health
        _health_checked_at[model["name"]] = now
        public_model = {key: value for key, value in model.items() if key != "api_key"}
        results.append({**public_model, **health})
    return sorted(results, key=lambda item: (not item["alive"], item["priority"]))


def _ensure_health_fresh() -> None:
    now = datetime.now(timezone.utc)
    if not _model_health:
        refresh_all_model_health()
        return
    oldest = min(_health_checked_at.values(), default=now - timedelta(seconds=_HEALTH_TTL_SECONDS + 1))
    if (now - oldest).total_seconds() >= _HEALTH_TTL_SECONDS:
        refresh_all_model_health()


def get_best_available_model(min_context: int = 0) -> dict | None:
    """
    Return the highest-priority alive model with enough context.
    """
    _ensure_health_fresh()
    for model in sorted(MODEL_REGISTRY, key=lambda item: item["priority"]):
        health = _model_health.get(model["name"]) or {}
        if health.get("alive") and int(model.get("context_limit") or 0) >= int(min_context or 0):
            return model
    return None


def get_cached_model_health() -> list[dict]:
    """
    Return the last known model health without making network calls.
    Useful for HTTP health endpoints that must stay fast and predictable.
    """
    results = []
    for model in sorted(MODEL_REGISTRY, key=lambda item: item["priority"]):
        health = _model_health.get(model["name"]) or {
            "alive": False,
            "latency_ms": None,
            "error": "not_checked",
        }
        public_model = {key: value for key, value in model.items() if key != "api_key"}
        checked_at = _health_checked_at.get(model["name"])
        results.append(
            {
                **public_model,
                **health,
                "last_check": checked_at.isoformat() if checked_at else None,
            }
        )
    return results


def chunk_text_for_llm(text: str, max_chunk_chars: int = 8000, overlap_chars: int = 200) -> list[str]:
    """
    Split long text into overlapping chunks that fit LLM context.
    Prefer newline/table boundaries over hard cuts.
    """
    text = str(text or "")
    if len(text) <= max_chunk_chars:
        return [text]

    chunks = []
    start = 0
    overlap_chars = max(0, min(overlap_chars, max_chunk_chars // 4))
    while start < len(text):
        end = start + max_chunk_chars
        if end >= len(text):
            chunks.append(text[start:])
            break

        safe_end = text.rfind("\n", start, end)
        if safe_end == -1 or safe_end - start < max_chunk_chars * 0.7:
            safe_end = text.rfind(". ", start, end)
        if safe_end == -1 or safe_end - start < max_chunk_chars * 0.7:
            safe_end = end

        chunks.append(text[start:safe_end])
        next_start = max(safe_end - overlap_chars, start + 1)
        start = next_start

    return [chunk for chunk in chunks if chunk]


def merge_chunked_extractions(chunk_results: list[dict], schema_type: str) -> dict:
    """
    Merge JSON extractions from multiple chunks with dedupe.
    """
    merged = {"type": schema_type}
    if schema_type == "prescription":
        all_meds = []
        seen_drugs = set()
        for result in chunk_results:
            for med in result.get("medications", []) or []:
                name = str(med.get("drug_name_raw") or "").lower().strip()
                dose = str(med.get("dose_amount") or "").strip()
                key = (name, dose)
                if name and key not in seen_drugs:
                    seen_drugs.add(key)
                    all_meds.append(med)
        merged["medications"] = all_meds
        merged["doctor_name"] = next((r.get("doctor_name") for r in chunk_results if r.get("doctor_name")), None)
        merged["date"] = next((r.get("date") for r in chunk_results if r.get("date")), None)
    elif schema_type == "lab_report":
        all_labs = []
        seen_tests = set()
        for result in chunk_results:
            for lab in result.get("lab_values", []) or []:
                name = str(lab.get("test_name") or "").lower().strip()
                value = str(lab.get("test_value") or "").strip()
                key = (name, value)
                if name and key not in seen_tests:
                    seen_tests.add(key)
                    all_labs.append(lab)
        merged["lab_values"] = all_labs
        merged["lab_name"] = next((r.get("lab_name") for r in chunk_results if r.get("lab_name")), None)
        merged["report_date"] = next((r.get("report_date") for r in chunk_results if r.get("report_date")), None)
    else:
        events = []
        for result in chunk_results:
            events.extend(result.get("events", []) or [])
        merged["events"] = events
    return merged


def build_extraction_prompt(schema_template: str, raw_text: str) -> str:
    """
    Build a strict prompt that forces JSON output.
    """
    return f"""{EXTRACTION_SYSTEM_PROMPT}

SCHEMA (you MUST follow this exact structure):
{schema_template}

TEXT TO EXTRACT FROM:
---
{raw_text}
---

REMEMBER:
- Output ONLY JSON. No other text.
- Include ALL items. Do not stop at first.
- Use null for missing fields, never omit keys.
"""


def extract_structured_data(raw_text: str, schema_template: str, schema_type: str) -> dict:
    """
    The only media-ingestion function that calls LLMs for extraction.
    Handles model selection, chunking, retries, validation, and merging.
    """
    raw_text = str(raw_text or "")
    min_context = max(0, len(raw_text) * 2)
    model = get_best_available_model(min_context)
    if not model:
        return _empty_result(schema_type, "No LLM available")

    chunk_size = max(2000, min(8000, int(model["context_limit"]) // 4))
    chunks = chunk_text_for_llm(raw_text, max_chunk_chars=chunk_size)
    chunk_results = []
    for chunk in chunks:
        prompt = build_extraction_prompt(schema_template, chunk)
        result = _call_single_model(model, prompt, schema_type)
        if result:
            chunk_results.append(_normalize_schema_result(result, schema_type))

    if len(chunk_results) == 1:
        return chunk_results[0]
    if len(chunk_results) > 1:
        return _normalize_schema_result(merge_chunked_extractions(chunk_results, schema_type), schema_type)
    return _empty_result(schema_type, "All LLM extractions failed")


def _call_single_model(model: dict, prompt: str, schema_type: str, max_retries: int = 2) -> dict | None:
    """
    Call one model with retry, JSON parsing, and minimal schema validation.
    """
    try:
        from openai import OpenAI
    except Exception:
        return None

    for attempt in range(max_retries):
        try:
            client = OpenAI(base_url=_openai_base_url(model["endpoint"]), api_key=model["api_key"], timeout=model["timeout"])
            policy = llm_policy.chat_parameters(model.get("model_id"), "extraction")
            kwargs = {
                "model": model["model_id"],
                "messages": [
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": min(int(model["max_tokens"]), int(policy.get("max_tokens", model["max_tokens"]))),
                "temperature": float(policy.get("temperature", 0.0)),
                "top_p": float(policy.get("top_p", 0.75)),
            }
            try:
                resp = client.chat.completions.create(**kwargs, response_format={"type": "json_object"})
            except Exception:
                resp = client.chat.completions.create(**kwargs)

            data = _parse_json_response(resp.choices[0].message.content)
            if _has_expected_keys(data, schema_type):
                return data
        except Exception:
            if attempt >= max_retries - 1:
                return None
    return None


def call_chat_completion(
    base_url: str,
    model_id: str,
    api_key: str,
    messages: list[dict],
    max_tokens: int,
    temperature: float,
    timeout: float,
    top_p: float | None = None,
    stop: list[str] | None = None,
    response_format: dict | None = None,
) -> str | None:
    """
    Generic OpenAI-compatible chat call for non-extraction LLM users.
    Keeps OpenAI client usage centralized in this gateway.
    """
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=_openai_base_url(base_url), timeout=timeout)
        policy = llm_policy.chat_parameters(model_id, "fallback")
        safe_temperature = max(0.0, min(float(temperature if temperature is not None else policy["temperature"]), 0.2))
        kwargs = {
            "model": model_id,
            "messages": messages,
            "max_tokens": min(int(max_tokens or policy["max_tokens"]), int(policy["max_tokens"])),
            "temperature": safe_temperature,
            "timeout": timeout,
        }
        if top_p is not None:
            kwargs["top_p"] = max(0.1, min(float(top_p), 0.9))
        else:
            kwargs["top_p"] = max(0.1, min(float(policy.get("top_p", 0.8)), 0.9))
        if stop is not None:
            kwargs["stop"] = stop
        if response_format is not None:
            try:
                resp = client.chat.completions.create(**kwargs, response_format=response_format)
            except Exception:
                resp = client.chat.completions.create(**kwargs)
        else:
            resp = client.chat.completions.create(**kwargs)
        return strip_reasoning_artifacts(resp.choices[0].message.content)
    except Exception:
        return None


class ModelRouter:
    """Route PharmaAgent LLM calls to the right configured model."""

    def __init__(self):
        self.primary_config = config.PHARMA_PRIMARY_MODEL
        self.reasoning_config = config.PHARMA_REASONING_MODEL
        self.safety_config = config.PHARMA_SAFETY_MODEL

    def call_primary(self, prompt: str, schema: dict | None = None) -> dict | None:
        """
        Call primary model (Qwen3-4B) with thinking enabled.
        Returns a parsed JSON dict, or None when the model response is missing
        or cannot be parsed as a JSON object.
        """
        try:
            full_prompt = self._with_suffix(prompt, self.primary_config.get("prompt_suffix"))
            if schema is not None:
                full_prompt = (
                    f"{full_prompt}\n\nReturn ONLY valid JSON matching this schema:\n"
                    f"{json.dumps(schema, ensure_ascii=False, default=str)}"
                )

            raw = call_chat_completion(
                base_url=self.primary_config["base_url"],
                model_id=self.primary_config["model_id"],
                api_key=self._api_key_for(self.primary_config),
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are CareCircle's structured medical safety assistant. "
                            "Return only compact valid JSON. Do not invent patient facts. "
                            "Support English, Hindi, and Hinglish. For medication safety output, "
                            "preserve severity and use risk, why_it_matters, and what_to_do_now."
                        ),
                    },
                    {"role": "user", "content": full_prompt},
                ],
                max_tokens=int(self.primary_config.get("max_tokens") or 600),
                temperature=float(self.primary_config.get("temperature") or 0),
                timeout=float(self.primary_config.get("timeout") or 60),
                top_p=0.75,
                response_format=self._response_format(self.primary_config, schema),
            )
            parsed = _parse_json_response(raw)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None

    def call_reasoning(self, prompt: str, max_context: int = 2000) -> str | None:
        """
        Call reasoning model (Nemotron) for complex synthesis.
        Returns raw text, or None when the model is unavailable.
        """
        try:
            trimmed_prompt = str(prompt or "")[: max(1, int(max_context or 2000))]
            return call_chat_completion(
                base_url=self.reasoning_config["base_url"],
                model_id=self.reasoning_config["model_id"],
                api_key=self._api_key_for(self.reasoning_config),
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are CareCircle's medication safety reasoning model. "
                            "Be concise, cite uncertainty, support English/Hinglish caregiver wording, "
                            "and do not prescribe or change doses. Use a clear risk, why it matters, "
                            "and what to do now structure when explaining safety findings."
                        ),
                    },
                    {"role": "user", "content": trimmed_prompt},
                ],
                max_tokens=int(self.reasoning_config.get("max_tokens") or 2000),
                temperature=float(self.reasoning_config.get("temperature") or 0.1),
                timeout=float(self.reasoning_config.get("timeout") or 120),
                top_p=0.75,
            )
        except Exception:
            return None

    def call_safety_check(self, content: str) -> dict:
        """
        Call safety model with short timeout.
        Returns a normalized moderation envelope. If the model cannot be reached,
        fail closed so callers can decide whether to retry or skip synthesis.
        """
        try:
            prompt = (
                "Classify this content for caregiver-facing medication safety output.\n"
                "Return ONLY JSON with keys: allowed, risk, reason.\n\n"
                f"Content:\n{str(content or '')[:4000]}"
            )
            raw = call_chat_completion(
                base_url=self.safety_config["base_url"],
                model_id=self.safety_config["model_id"],
                api_key=self._api_key_for(self.safety_config),
                messages=[
                    {
                        "role": "system",
                        "content": "You are a strict medical safety moderation classifier. Output only JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
                max_tokens=int(self.safety_config.get("max_tokens") or 100),
                temperature=float(self.safety_config.get("temperature") or 0),
                timeout=float(self.safety_config.get("timeout") or 10),
                top_p=0.5,
                response_format={"type": "json_object"},
            )
            parsed = _parse_json_response(raw)
            if not isinstance(parsed, dict):
                return self._safety_error("safety model returned non-json")
            return {
                "allowed": bool(parsed.get("allowed")),
                "risk": str(parsed.get("risk") or "unknown"),
                "reason": str(parsed.get("reason") or ""),
                "source": self.safety_config.get("model_id"),
            }
        except Exception as error:
            return self._safety_error(str(error))

    def _with_suffix(self, prompt: str, suffix: str | None) -> str:
        text = str(prompt or "").strip()
        suffix_text = str(suffix or "").strip()
        if "/no_think" in text or "/think" in text:
            return text
        if suffix_text and suffix_text not in text:
            return f"{text}\n{suffix_text}".strip()
        return text

    def _api_key_for(self, model_config: dict) -> str:
        endpoint = str(model_config.get("base_url") or "")
        if "openrouter.ai" in endpoint:
            return _openrouter_key() or str(model_config.get("api_key") or "")
        if "integrate.api.nvidia.com" in endpoint:
            return (
                getattr(config, "_ENV", {}).get("NVIDIA_API_KEY")
                or getattr(config, "_ENV", {}).get("NVIDIA_API_KEYS")
                or str(model_config.get("api_key") or "")
            )
        return str(model_config.get("api_key") or "lm-studio")

    def _response_format(self, model_config: dict, schema: dict | None) -> dict | None:
        if schema is None:
            return None
        if model_config.get("response_format") == "json_object":
            return {"type": "json_object"}
        response_format = model_config.get("response_format")
        return response_format if isinstance(response_format, dict) else None

    def _safety_error(self, reason: str) -> dict:
        return {
            "allowed": False,
            "risk": "unknown",
            "reason": reason or "safety model unavailable",
            "source": "safety_model_error",
        }


def _parse_json_response(raw: str | None) -> dict | None:
    try:
        cleaned = re.sub(r"^```json\s*|\s*```$", "", strip_reasoning_artifacts(raw), flags=re.MULTILINE)
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end < start:
            return None
        return json.loads(cleaned[start : end + 1])
    except Exception:
        return None


def strip_reasoning_artifacts(raw: str | None) -> str:
    """
    Remove reasoning/tool-call markup that thinking models may leak.
    Inspired by Hermes Agent's display sanitizer; kept compact here so model
    JSON parsers and caregiver-facing replies receive only final content.
    """
    cleaned = str(raw or "").strip()
    for tag in ("REASONING_SCRATCHPAD", "think", "thinking", "reasoning", "thought"):
        cleaned = re.sub(
            rf"<{tag}>.*?</{tag}\s*>",
            "",
            cleaned,
            flags=re.DOTALL | re.IGNORECASE,
        )
        cleaned = re.sub(
            rf"<{tag}>.*$",
            "",
            cleaned,
            flags=re.DOTALL | re.IGNORECASE,
        )
        cleaned = re.sub(rf"</{tag}\s*>", "", cleaned, flags=re.IGNORECASE)

    for tag in ("tool_call", "tool_calls", "tool_result", "function_call", "function_calls"):
        cleaned = re.sub(
            rf"<{tag}\b[^>]*>.*?</{tag}\s*>",
            "",
            cleaned,
            flags=re.DOTALL | re.IGNORECASE,
        )
        cleaned = re.sub(rf"</{tag}\s*>", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _has_expected_keys(data: dict | None, schema_type: str) -> bool:
    if not isinstance(data, dict):
        return False
    if schema_type == "prescription":
        return "medications" in data
    if schema_type == "lab_report":
        return "lab_values" in data
    return "events" in data


def _empty_result(schema_type: str, error: str) -> dict:
    result = {"error": error, "type": schema_type}
    if schema_type == "prescription":
        result["medications"] = []
    elif schema_type == "lab_report":
        result["lab_values"] = []
    else:
        result["events"] = []
    return result


def _normalize_schema_result(data: dict, schema_type: str) -> dict:
    data = dict(data or {})
    data["type"] = schema_type
    if schema_type == "prescription":
        meds = data.get("medications")
        data["medications"] = meds if isinstance(meds, list) else ([] if meds is None else [meds])
        data.setdefault("doctor_name", None)
        data.setdefault("date", None)
    elif schema_type == "lab_report":
        labs = data.get("lab_values")
        data["lab_values"] = labs if isinstance(labs, list) else ([] if labs is None else [labs])
        data.setdefault("lab_name", None)
        data.setdefault("report_date", None)
    else:
        events = data.get("events")
        data["events"] = events if isinstance(events, list) else ([] if events is None else [events])
    return data
