"""
Shared helpers for CareCircle media ingestion.

This module intentionally keeps OCR, audio transcription, PDF extraction, drug
resolution, and LLM extraction behind small helper functions so the future
worker can compose them without coupling parser-specific failures to the main
chat endpoint.

Fallback strategy:
- Download errors raise to the caller because task-level code should decide
  whether to retry or mark the pending task failed.
- OCR/transcription/PDF/parser helpers return empty safe values on failure.
- LLM extraction tries local LM Studio first, then OpenRouter if configured.
- Drug resolution prefers the local formulary, then RxNav, then the raw text.

Future improvements:
- Add parser-specific confidence calibration from human corrections.
- Add local file size/type validation before downloading or parsing.
- Add deterministic extraction prompt templates per media type.
- Add async worker wrappers around these synchronous helpers.
"""

import db
import hashlib
from datetime import date, datetime
import io
import json
import logging
import os
import re
import requests
import tempfile
import warnings
from collections.abc import Mapping

import config
import drug_resolver
import llm_gateway
from async_pipeline import AsyncDocumentPipeline
from config import SIDE_EFFECT_HINTS
from pharma_tools import ModelRouter

try:
    from paddleocr import PaddleOCR
except Exception:  # Optional dependency; helpers must remain import-safe.
    PaddleOCR = None

try:
    from PIL import Image
except Exception:  # Optional dependency; OCR fallback handles this.
    Image = None


# Module-level variables for lazy loading (DO NOT REMOVE)
_paddleocr = None
_whisper_model = None


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
      "prescribed_by": null,
      "date": null
    }
  ],
  "doctor_name": null,
  "date": null
}

RULES:
- dose_amount must be a number (int or float), not a string.
- frequency must be one of: OD, BD, TDS, QID, SOS, HS, once daily, twice daily.
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


def _download_media(media_url: str) -> bytes:
    """
    Download media from URL using requests.get directly.
    Return file bytes or raise exception (caller handles).
    """
    response = requests.get(media_url, timeout=30)
    response.raise_for_status()
    return response.content


def _run_paddle_ocr(image_data: bytes) -> tuple[str, float]:
    """
    Run PaddleOCR on image bytes. Initialize model once at module level.
    Return (concatenated OCR text, confidence). On error, return ("", 0.0).
    """
    global _paddleocr
    try:
        if PaddleOCR is None or Image is None:
            return "", 0.0

        image = Image.open(io.BytesIO(image_data))
        image.verify()
        image = Image.open(io.BytesIO(image_data))

        if _paddleocr is None:
            try:
                # PaddleOCR 3.x removed use_gpu; device is accepted by newer builds.
                _paddleocr = PaddleOCR(
                    lang=config.PADDLEOCR_LANG,
                    device="gpu" if config.PADDLEOCR_USE_GPU else "cpu",
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                )
            except Exception:
                # PaddleOCR 2.x compatibility.
                _paddleocr = PaddleOCR(
                    lang=config.PADDLEOCR_LANG,
                    use_gpu=config.PADDLEOCR_USE_GPU,
                )

        outputs = []
        for variant_name, variant_image in _build_ocr_variants(image):
            text, confidence = _run_paddle_ocr_once(variant_image)
            if text:
                outputs.append(
                    {
                        "variant": variant_name,
                        "text": _clean_extracted_text(text),
                        "confidence": confidence,
                    }
                )

        if not outputs:
            return "", 0.0

        text = _merge_ocr_outputs(outputs)
        confidence = max(float(item.get("confidence") or 0.0) for item in outputs)
        return text, confidence if text else 0.0
    except Exception:
        return "", 0.0


def _run_paddle_ocr_once(image) -> tuple[str, float]:
    """
    Run one OCR pass for a prepared image variant.

    Handwritten prescriptions are often low-resolution, skewed, and shadowed.
    A single OCR pass can miss lower rows even when confidence looks high, so
    the public helper calls this across a small set of deterministic variants.
    """
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            image.convert("RGB").save(tmp, format="PNG")
            temp_path = tmp.name

        if hasattr(_paddleocr, "predict"):
            result = _paddleocr.predict(temp_path)
        else:
            result = _paddleocr.ocr(temp_path, cls=False)

        text, confidence = _parse_paddle_ocr_result(result)
        if not text:
            return "", 0.0
        return text, confidence
    except Exception:
        return "", 0.0
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except Exception:
                pass


def _build_ocr_variants(image) -> list[tuple[str, object]]:
    """
    Build a small set of deterministic OCR variants.

    We keep the list short because this runs in the ingestion worker. The goal is
    recall, not cosmetic enhancement: original preserves layout, while upscaled
    contrast/sharpness variants recover compressed handwriting.
    """
    try:
        from PIL import ImageEnhance, ImageFilter, ImageOps

        base = image.convert("RGB")
        variants = [("original", base)]

        scale = 3 if min(base.size) < 700 else 2
        upscaled = base.resize((base.width * scale, base.height * scale), Image.Resampling.LANCZOS)
        variants.append(("upscaled", upscaled))

        gray = ImageOps.grayscale(base)
        enhanced = gray.resize((base.width * scale, base.height * scale), Image.Resampling.LANCZOS)
        enhanced = ImageEnhance.Contrast(enhanced).enhance(1.7)
        enhanced = ImageEnhance.Sharpness(enhanced).enhance(1.5)
        variants.append(("contrast_sharp", enhanced.convert("RGB")))

        cropped = _crop_dark_borders(base)
        if cropped.size != base.size:
            cropped = cropped.resize((cropped.width * scale, cropped.height * scale), Image.Resampling.LANCZOS)
            cropped = ImageEnhance.Contrast(ImageOps.grayscale(cropped)).enhance(1.5).convert("RGB")
            variants.append(("cropped_contrast", cropped))

        return variants
    except Exception:
        return [("original", image)]


def _crop_dark_borders(image):
    try:
        from PIL import ImageOps

        gray = ImageOps.grayscale(image)
        # Treat near-black borders as background and crop to the brighter paper.
        mask = gray.point(lambda pixel: 255 if pixel > 35 else 0)
        bbox = mask.getbbox()
        if not bbox:
            return image

        left, top, right, bottom = bbox
        if (right - left) < image.width * 0.5 or (bottom - top) < image.height * 0.5:
            return image
        return image.crop((max(left - 8, 0), max(top - 8, 0), min(right + 8, image.width), min(bottom + 8, image.height)))
    except Exception:
        return image


def _merge_ocr_outputs(outputs: list[dict]) -> str:
    """
    Preserve useful OCR text while avoiding repeated full-pass variants.

    Multi-pass OCR is important for handwritten prescriptions, but storing every
    full pass makes raw_text look like duplicated generated data. We keep the
    strongest pass, then append only high-novelty medication-like segments from
    weaker passes.
    """
    try:
        ranked = sorted(
            outputs,
            key=lambda item: (
                len(set(re.findall(r"[a-z0-9]{2,}", str(item.get("text") or "").lower()))),
                float(item.get("confidence") or 0.0),
            ),
            reverse=True,
        )
        if not ranked:
            return ""

        base_text = _collapse_repeated_ocr_text(str(ranked[0].get("text") or "").strip())
        # The best PaddleOCR pass already contains the full page for our
        # supported parser path. Store that single best pass as raw_text to keep
        # the audit trail readable; variant-level text is intentionally not
        # concatenated because it creates duplicate medical rows downstream.
        return base_text
        merged_segments = _segment_ocr_text(base_text)
        seen_tokens = set(re.findall(r"[a-z0-9]{2,}", _normalize_for_dedupe(base_text)))

        for item in ranked[1:]:
            for segment in _segment_ocr_text(str(item.get("text") or "")):
                # Never append another whole-page/header segment. Secondary
                # passes are only allowed to contribute short missed rows.
                if len(segment) > 220:
                    continue
                if not _looks_like_medication_segment(segment):
                    continue
                tokens = set(re.findall(r"[a-z0-9]{2,}", _normalize_for_dedupe(segment)))
                if len(tokens) < 2:
                    continue
                novelty = len(tokens - seen_tokens) / max(len(tokens), 1)
                if novelty >= 0.45 or _contains_unseen_drug_like_token(tokens, seen_tokens):
                    merged_segments.append(segment)
                    seen_tokens.update(tokens)

        return _dedupe_preserve_order(merged_segments)
    except Exception:
        return str((outputs or [{}])[0].get("text") or "").strip()


def _collapse_repeated_ocr_text(text: str) -> str:
    """
    Remove repeated OCR sentences/rows from a single pass without dropping order.

    Some PaddleOCR layouts repeat the same recognized row after deskew/table
    processing. Keeping one normalized copy prevents duplicate medication rows
    while preserving the raw text needed for audit/debugging.
    """
    try:
        if not text:
            return ""
        if "\n" not in str(text):
            return str(text).strip()
        pieces = re.split(r"\n+", str(text))
        if len(pieces) <= 1:
            return str(text).strip()
        seen = set()
        kept = []
        for piece in pieces:
            clean = piece.strip()
            key = _normalize_for_dedupe(clean)
            if not key or key in seen:
                continue
            seen.add(key)
            kept.append(clean)
        return " ".join(kept).strip()
    except Exception:
        return str(text or "").strip()


def _segment_ocr_text(text: str) -> list[str]:
    try:
        cleaned = _clean_extracted_text(text)
        cleaned = re.sub(r"\s+", " ", cleaned)
        # Add soft boundaries around common prescription entry markers.
        cleaned = re.sub(
            r"\b(TAB|TABLET|CAP|CAPSULE|SYRUP|SYP|SYR|INJ|INJECTION|SUSP|SUSPENSION|DROP|DROPS|CREAM|OINTMENT|GEL|LOTION|SPRAY|INHALER|NEB|SOLUTION|SOLN|SACHET|VIAL|AMP|AMPOULE|Rx|In case|SOS)\b",
            r"\n\1",
            cleaned,
            flags=re.IGNORECASE,
        )
        pieces = []
        for line in cleaned.splitlines():
            line = line.strip(" .•*-")
            if len(line) >= 3:
                pieces.append(line)
        return pieces or ([cleaned] if cleaned else [])
    except Exception:
        return [str(text or "").strip()] if text else []


def _contains_unseen_drug_like_token(tokens: set[str], seen_tokens: set[str]) -> bool:
    try:
        ignore = {
            "tab", "tablet", "cap", "capsule", "syrup", "syp", "syr", "inj", "drop", "drops", "cream", "ointment", "gel", "once", "daily", "day", "week", "mg", "ml",
            "sos", "case", "fever", "pain", "cough", "doctor", "hospital",
        }
        new_tokens = tokens - seen_tokens - ignore
        return any(len(token) >= 5 for token in new_tokens)
    except Exception:
        return False


def _looks_like_medication_segment(segment: str) -> bool:
    try:
        lowered = _normalize_ocr_dose_text(str(segment or "")).lower()
        return bool(
            re.search(r"\b(tab|tablet|cap|capsule|syrup|syp|syr|inj|injection|susp|suspension|drop|drops|cream|ointment|gel|lotion|spray|inhaler|neb|solution|soln|sachet|vial|amp|ampoule|sos|od|bd|tds|qid|hs)\b", lowered)
            or re.search(r"\b\d+(?:\.\d+)?\s*(mg|mcg|g|ml|tsp|teaspoon|tbsp|tablespoon|spoon|drops?|puffs?|sprays?)\b", lowered)
            or re.search(r"\b\d\s*-\s*\d\s*-\s*\d\b", lowered)
        )
    except Exception:
        return False


def _dedupe_preserve_order(segments: list[str]) -> str:
    seen = set()
    result = []
    for segment in segments:
        normalized = _normalize_for_dedupe(segment)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(segment)
    return "\n".join(result).strip()


def _normalize_for_dedupe(text: str) -> str:
    try:
        return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()
    except Exception:
        return str(text or "").strip().lower()


def _clean_extracted_text(text: str) -> str:
    """
    Remove parser artifacts while preserving medical content and line breaks.

    This is not semantic filtering. It only removes null/control bytes and
    normalizes common PDF mojibake separators so raw parser text can still be
    audited and sent to extraction safely.
    """
    try:
        cleaned = str(text or "")
        replacements = {
            "\x00": "",
            "â€“": "-",
            "â€”": "-",
            "–": "-",
            "—": "-",
            "Âµ": "µ",
            "Â°": "°",
        }
        for old, new in replacements.items():
            cleaned = cleaned.replace(old, new)
        cleaned = re.sub(r"\((TSH|T3|T4)\)", r"\1", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"[ \t]+", " ", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()
    except Exception:
        return str(text or "")


def _parse_paddle_ocr_result(result) -> tuple[str, float]:
    try:
        if not result:
            return "", 0.0

        text_parts = []
        confidences = []

        for item in result:
            parsed = _parse_paddle_item(item)
            text_parts.extend(parsed[0])
            confidences.extend(parsed[1])

        text = " ".join(part for part in text_parts if part).strip()
        confidence = sum(confidences) / len(confidences) if confidences else 0.85
        return text, float(confidence) if text else 0.0
    except Exception:
        return "", 0.0


def _parse_paddle_item(item) -> tuple[list[str], list[float]]:
    texts = []
    confidences = []
    try:
        # PaddleOCR 3.x OCRResult behaves like a Mapping with rec_texts/scores.
        if isinstance(item, Mapping):
            rec_texts = item.get("rec_texts") or item.get("texts") or []
            rec_scores = item.get("rec_scores") or item.get("scores") or []
            texts.extend(str(text) for text in rec_texts if text)
            confidences.extend(float(score) for score in rec_scores if score is not None)
            return texts, confidences

        if hasattr(item, "res") and isinstance(item.res, dict):
            res = item.res
            rec_texts = res.get("rec_texts") or res.get("texts") or []
            rec_scores = res.get("rec_scores") or res.get("scores") or []
            texts.extend(str(text) for text in rec_texts if text)
            confidences.extend(float(score) for score in rec_scores if score is not None)
            return texts, confidences

        # PaddleOCR 2.x shape: [ [box, (text, score)], ... ]
        if isinstance(item, list):
            lines = item
            if len(item) == 2 and isinstance(item[1], (tuple, list)) and len(item[1]) >= 2:
                lines = [item]
            for line in lines:
                if line and len(line) > 1 and line[1]:
                    value = line[1]
                    if isinstance(value, (tuple, list)) and len(value) >= 2:
                        texts.append(str(value[0]))
                        confidences.append(float(value[1]))
    except Exception:
        pass
    return texts, confidences


def _transcribe_whisper(audio_bytes: bytes) -> str | None:
    """
    Transcribe audio using Whisper. Load model once at module level.
    Return transcript text or None on failure.
    """
    global _whisper_model
    temp_path = None
    try:
        if not _looks_like_audio(audio_bytes):
            return None

        import whisper

        if _whisper_model is None:
            _whisper_model = whisper.load_model(
                config.WHISPER_MODEL,
                device=config.WHISPER_DEVICE,
            )

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp.flush()
            temp_path = tmp.name

        result = _whisper_model.transcribe(temp_path)
        text = str(result.get("text") or "").strip()
        return text or None
    except Exception:
        return None
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except Exception:
                pass


def _looks_like_audio(audio_bytes: bytes) -> bool:
    try:
        if not audio_bytes or len(audio_bytes) < 12:
            return False

        header = bytes(audio_bytes[:16])
        return (
            header.startswith(b"RIFF")
            or header.startswith(b"ID3")
            or header.startswith(b"OggS")
            or header.startswith(b"fLaC")
            or header[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}
        )
    except Exception:
        return False


def _extract_pdf_text(file_bytes: bytes) -> tuple[str, float]:
    """
    Extract text from PDF using pdfplumber.
    If text is long enough, return (text, 0.9). Else return ("", 0.0).
    """
    temp_path = None
    try:
        import pdfplumber

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp.flush()
            temp_path = tmp.name

        logging.getLogger("pdfminer").setLevel(logging.ERROR)
        logging.getLogger("pdfminer.pdfinterp").setLevel(logging.ERROR)
        logging.getLogger("pdfminer.pdffont").setLevel(logging.ERROR)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with pdfplumber.open(temp_path) as pdf:
                parts = []
                for page in pdf.pages:
                    page_text = page.extract_text(x_tolerance=1, y_tolerance=3)
                    if page_text:
                        parts.append(page_text)

        text = _clean_extracted_text("\n".join(parts))
        return (text, 0.9) if len(text) >= config.PDF_TEXT_MIN_LENGTH else ("", 0.0)
    except Exception:
        return "", 0.0
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except Exception:
                pass


def _attempt_pattern_extraction(text: str, patterns: dict) -> dict | None:
    """
    Extract structured info using regex/keyword rules.
    patterns: {field_name: (regex_pattern, post_process_function)}
    Return dict if at least one field was extracted, else None.
    """
    try:
        if not text or not isinstance(patterns, dict):
            return None

        extracted = {}
        for field_name, rule in patterns.items():
            try:
                regex_pattern, post_process = rule
                match = re.search(regex_pattern, text, flags=re.IGNORECASE)
                if not match:
                    continue

                value = match.group(1) if match.groups() else match.group(0)
                extracted[field_name] = post_process(value) if post_process else value
            except Exception:
                continue

        return extracted or None
    except Exception:
        return None


def _parse_llm_json(raw: str | None) -> dict | None:
    try:
        if not raw:
            return None

        cleaned = re.sub(r"^```json\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end < start:
            return None

        return json.loads(cleaned[start : end + 1])
    except Exception:
        return None


def _openrouter_key() -> str | None:
    try:
        key = getattr(config, "LLM_EXTRACTION_API_KEY", None)
        if key:
            return key
        return getattr(config, "_ENV", {}).get("OPENROUTER_API_KEY")
    except Exception:
        return None


def _fallback_llm_extraction(schema_template: str, raw_text: str, schema_type: str) -> dict | None:
    """
    Unified LLM extraction entrypoint.
    Uses llm_gateway for model selection, chunking, and strict schema enforcement.
    """
    return llm_gateway.extract_structured_data(raw_text, schema_template, schema_type)


def get_extraction_schema(intent_type: str) -> str:
    if intent_type in {"new_prescription", "medication_report", "medication_due_now", "prescription"}:
        return llm_gateway.MEDICATION_SCHEMA_TEMPLATE
    if intent_type in {"lab_report", "vital_report"}:
        return llm_gateway.LAB_SCHEMA_TEMPLATE
    return llm_gateway.NOTE_SCHEMA_TEMPLATE


def classify_media_content(raw_text: str) -> str:
    """
    Classify extracted text to determine target schema.
    Returns: prescription | lab_report | general_note.
    """
    try:
        text = str(raw_text or "").lower()
        rx_score = 0
        rx_keywords = [
            "mg", "tablet", "tab", "capsule", "cap", "syrup", "prescribed",
            "prescription", " od", " bd", " tds", " sos", "doctor", "dr.", "dose",
            "once daily", "twice daily", "before food", "after food",
        ]
        for keyword in rx_keywords:
            if keyword in text:
                rx_score += 1
        if re.search(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml)\b", text):
            rx_score += 2
        if re.search(r"\b(?:od|bd|tds|qid|sos|hs)\b", text):
            rx_score += 1

        lab_score = 0
        lab_keywords = [
            "hba1c", "creatinine", "glucose", "mg/dl", "mmol/l", "reference",
            "range", "lab", "report", "blood test", "normal", "high", "low",
            "thyroid", "tsh", "t3", "t4", "hemoglobin", "cholesterol",
        ]
        for keyword in lab_keywords:
            if keyword in text:
                lab_score += 1
        if re.search(r"\b(?:hba1c|creatinine|glucose|tsh|cholesterol|hemoglobin)\b[^\n]{0,30}\d", text):
            lab_score += 2

        vital_patterns = [
            r"\d{2,3}/\d{2,3}",
            r"\bbp\s*:?\s*\d+",
            r"\bpulse\s*:?\s*\d+",
            r"\bsugar\s*:?\s*\d+",
            r"\bspo2\s*:?\s*\d+",
        ]
        for pattern in vital_patterns:
            if re.search(pattern, text):
                lab_score += 2

        if rx_score >= 3 and rx_score > lab_score:
            return "prescription"
        if lab_score >= 3 and lab_score >= rx_score:
            return "lab_report"
    except Exception:
        pass
    return "general_note"


def _schema_for_content_type(content_type: str) -> tuple[str, str]:
    if content_type == "prescription":
        return llm_gateway.MEDICATION_SCHEMA_TEMPLATE, "prescription"
    if content_type == "lab_report":
        return llm_gateway.LAB_SCHEMA_TEMPLATE, "lab_report"
    return llm_gateway.NOTE_SCHEMA_TEMPLATE, "general_note"


def validate_and_fill_missing(structured: dict, schema_type: str) -> dict:
    """
    Ensure required fields exist while avoiding unsafe guesses.
    """
    structured = dict(structured or {})
    try:
        if schema_type == "prescription":
            meds = structured.get("medications", [])
            if isinstance(meds, dict):
                meds = [meds]
            if not isinstance(meds, list):
                meds = []
            for med in meds:
                if not isinstance(med, dict):
                    continue
                if not med.get("drug_name_raw"):
                    med["drug_name_raw"] = "UNKNOWN_DRUG"
                    med["_flag"] = "missing_drug_name"
                med.setdefault("dose_amount", None)
                if med.get("frequency") is None:
                    med["frequency"] = "OD"
                if med.get("dose_unit") is None:
                    med["dose_unit"] = "mg"
            structured["medications"] = meds
            if meds and all(med.get("_flag") == "missing_drug_name" for med in meds if isinstance(med, dict)):
                structured["_review_required"] = True

        elif schema_type == "lab_report":
            labs = structured.get("lab_values", [])
            if isinstance(labs, dict):
                labs = [labs]
            if not isinstance(labs, list):
                labs = []
            for lab in labs:
                if not isinstance(lab, dict):
                    continue
                if lab.get("test_name") is None:
                    lab["test_name"] = "unknown_test"
                    lab["_flag"] = "missing_test_name"
                if lab.get("test_value") is None:
                    lab["_flag"] = "missing_value"
                if lab.get("unit") is None:
                    lab["unit"] = "mg/dL"
            structured["lab_values"] = labs
            if labs and all(isinstance(lab, dict) and lab.get("_flag") for lab in labs):
                structured["_review_required"] = True

        else:
            events = structured.get("events", [])
            if isinstance(events, dict):
                events = [events]
            if not isinstance(events, list):
                events = []
            for event in events:
                if not isinstance(event, dict):
                    continue
                event.setdefault("event_type", "other")
                event.setdefault("description", None)
                event.setdefault("time_of_day", None)
            structured["events"] = events
    except Exception:
        structured["_review_required"] = True
    return structured


def _hash_bytes(data: bytes) -> str:
    try:
        return hashlib.sha256(data or b"").hexdigest()
    except Exception:
        return ""


def classify_parsed_intent(raw_text: str, patient_context: dict) -> str:
    """
    Classify extracted text to determine the target media-ingestion DB table.
    Returns one of: new_prescription, lab_report, general_note.
    """
    try:
        from intent import classify_intent_with_confidence

        result = classify_intent_with_confidence(raw_text, pending_context=None)
        intent = result.get("intent", "unknown") if isinstance(result, dict) else "unknown"
    except Exception:
        intent = "unknown"

    intent_map = {
        "new_prescription": "new_prescription",
        "medication_report": "new_prescription",
        "medication_due_now": "new_prescription",
        "lab_report": "lab_report",
        "vital_report": "lab_report",
        "symptom_report": "general_note",
        "diet_report": "general_note",
        "sleep_report": "general_note",
        "mood_report": "general_note",
        "emotional_checkin": "general_note",
        "exercise_report": "general_note",
        "caregiver_observation": "general_note",
    }
    return intent_map.get(intent, "general_note")


def _resolve_drug_name(raw_name: str) -> tuple[str, float]:
    """
    Backward-compatible wrapper for the shared drug resolver.
    """
    return drug_resolver.resolve_drug_name(raw_name)


def _cached_drug_resolution(raw_name: str) -> str | None:
    """
    Check optional DB cache/formulary helpers first, then the local formulary
    table directly. This avoids depending on the removed resolve_drug_by_alias.
    """
    raw = str(raw_name or "").strip()
    if not raw:
        return None

    try:
        get_cached = getattr(db, "get_cached_interaction", None)
        if callable(get_cached):
            cached = get_cached(raw)
            if isinstance(cached, dict) and cached.get("canonical_name"):
                return str(cached["canonical_name"])
    except Exception:
        pass

    try:
        get_formulary = getattr(db, "get_from_formulary", None)
        if callable(get_formulary):
            cached = get_formulary(raw)
            if isinstance(cached, dict) and cached.get("canonical_name"):
                return str(cached["canonical_name"])
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

        # Current RxNav approximateTerm shape commonly returns candidate[].
        candidates = group.get("candidate") or []
        if candidates:
            name = candidates[0].get("name")
            if name:
                return str(name)

        # Compatibility with older/sample conceptGroup-style responses.
        concept_groups = group.get("conceptGroup") or []
        for concept_group in concept_groups:
            properties = concept_group.get("conceptProperties") or []
            if properties and properties[0].get("name"):
                return str(properties[0]["name"])
    except Exception:
        return None
    return None


def process_side_effect_lookup(
    patient_id: str,
    drug_name: str | None,
    symptom: str | None,
    raw_text: str,
) -> dict:
    """
    Async worker for side effects. Uses ModelRouter for model-specific prompts.
    Self-learning: explicit caregiver feedback in raw_text is recorded for later
    rule review without changing the lookup response contract.
    """
    try:
        resolved_drug, drug_confidence = _resolve_drug_name(drug_name or raw_text)
        resolved_drug = str(resolved_drug or "").strip().lower()
        symptom_text = str(symptom or raw_text or "").strip().lower()
        raw = str(raw_text or "").strip()
        _maybe_record_side_effect_feedback(patient_id, resolved_drug or drug_name, symptom, raw)

        for (known_drug, known_symptom), hint in SIDE_EFFECT_HINTS.items():
            if known_drug == resolved_drug and _side_effect_term_matches(known_symptom, symptom_text):
                return {
                    "status": "ok",
                    "reply": hint.strip(),
                    "source": "known_hint",
                    "drug_name": known_drug,
                    "symptom": known_symptom,
                    "confidence": 0.95,
                    "patient_id": patient_id,
                }

        raw_lower = raw.lower()
        for (known_drug, known_symptom), hint in SIDE_EFFECT_HINTS.items():
            if known_drug in raw_lower and _side_effect_term_matches(known_symptom, raw_lower):
                return {
                    "status": "ok",
                    "reply": hint.strip(),
                    "source": "known_hint",
                    "drug_name": known_drug,
                    "symptom": known_symptom,
                    "confidence": 0.90,
                    "patient_id": patient_id,
                }

        prompt = _side_effect_lookup_prompt(
            patient_id=patient_id,
            drug_name=resolved_drug or drug_name or "unknown",
            symptom=symptom or "unknown",
            raw_text=raw,
            drug_confidence=drug_confidence,
        )
        router = ModelRouter()
        schema = _side_effect_schema(resolved_drug or drug_name, symptom)
        if _is_complex_side_effect_query(raw, symptom_text):
            raw_reply = router.call_reasoning(prompt, max_context=5000)
            parsed = _parse_side_effect_json(raw_reply)
        else:
            parsed = router.call_primary(f"{prompt}\n/no_think", schema)

        if not _valid_side_effect_result(parsed):
            return {
                "status": "no_result",
                "reply": "I understood this may be a side-effect question. Please contact the doctor if symptoms are severe or persistent.",
                "source": "safe_fallback",
                "drug_name": resolved_drug or drug_name,
                "symptom": symptom,
                "confidence": 0.50,
                "patient_id": patient_id,
            }

        parsed["status"] = "ok"
        parsed["source"] = "model_router"
        parsed["drug_name"] = parsed.get("drug_name") or resolved_drug or drug_name
        parsed["symptom"] = parsed.get("symptom") or symptom
        parsed["patient_id"] = patient_id
        return parsed
    except Exception as error:
        return {
            "status": "error",
            "reply": "I could not complete the side-effect lookup right now. Please contact the doctor if symptoms are severe.",
            "source": "error",
            "error": str(error),
            "drug_name": drug_name,
            "symptom": symptom,
            "confidence": 0.0,
            "patient_id": patient_id,
        }


def _side_effect_term_matches(known_symptom: str, text: str) -> bool:
    terms = _side_effect_terms(known_symptom)
    haystack = str(text or "").lower()
    return any(term and term in haystack for term in terms)


def _side_effect_terms(known_symptom: str) -> list[str]:
    symptom = str(known_symptom or "").strip().lower()
    aliases = {
        "dizziness": ["dizziness", "dizzy", "lightheaded", "light headed", "chakkar"],
        "chakkar": ["chakkar", "dizzy", "dizziness"],
        "swelling": ["swelling", "swollen", "sojan", "sujan"],
        "sojan": ["sojan", "sujan", "swelling", "swollen"],
        "nausea": ["nausea", "nauseous", "matli", "jee michla"],
        "vomiting": ["vomiting", "vomit", "throwing up", "ulti"],
        "ulti": ["ulti", "vomit", "vomiting"],
        "diarrhea": ["diarrhea", "loose motion", "loose stools", "dast"],
        "loose motion": ["loose motion", "loose motions", "loose stools", "diarrhea"],
        "headache": ["headache", "head pain", "sir dard", "sar dard"],
        "cough": ["cough", "dry cough", "khansi"],
        "sweating": ["sweating", "sweat", "pasina"],
        "kamzori": ["kamzori", "weak", "weakness"],
        "weak": ["weak", "weakness", "kamzori"],
    }
    return aliases.get(symptom, [symptom])


def _side_effect_schema(drug_name: str | None, symptom: str | None) -> dict:
    return {
        "reply": "one short caregiver-facing answer under 280 chars",
        "mechanism": "simple reason this symptom may or may not relate",
        "action": "monitor|consult|urgent",
        "confidence": 0.0,
        "drug_name": drug_name,
        "symptom": symptom,
    }


def _is_complex_side_effect_query(raw_text: str, symptom_text: str) -> bool:
    text = str(raw_text or "").lower()
    symptoms = [part.strip() for part in re.split(r",| and | aur | with | plus ", symptom_text or "") if part.strip()]
    complex_terms = {
        "multiple",
        "many symptoms",
        "allergy",
        "rash",
        "swelling face",
        "breathing",
        "faint",
        "kidney",
        "liver",
        "pregnant",
        "interaction",
        "together",
    }
    return len(text.split()) > 45 or len(symptoms) > 2 or any(term in text for term in complex_terms)


def _maybe_record_side_effect_feedback(
    patient_id: str,
    drug_name: str | None,
    symptom: str | None,
    raw_text: str,
) -> None:
    """
    Record only explicit caregiver feedback phrases. Normal side-effect questions
    should not train the rules accidentally.
    """
    try:
        text = str(raw_text or "").strip().lower()
        feedback_markers = ("feedback:", "caregiver feedback", "mark this", "agent feedback")
        if not any(marker in text for marker in feedback_markers):
            return

        outcome = None
        for candidate in ("approved", "vetoed", "rejected", "escalated"):
            if candidate in text:
                outcome = candidate
                break
        if outcome is None:
            return

        drug = str(drug_name or "").strip().lower()
        side_effect = str(symptom or "side_effect").strip().lower()
        if not drug:
            resolved, _ = _resolve_drug_name(raw_text)
            drug = str(resolved or "").strip().lower()
        if not drug:
            return

        db.record_pharmagent_feedback(
            f"{drug}+{side_effect}",
            outcome,
            raw_text[:500],
            patient_id,
        )
    except Exception:
        pass


def _side_effect_lookup_prompt(
    patient_id: str,
    drug_name: str,
    symptom: str,
    raw_text: str,
    drug_confidence: float,
) -> str:
    return f"""
You are CareCircle's medication side-effect research assistant.
Use cautious caregiver-safe language. Do not diagnose, prescribe, or change treatment.
If symptoms are severe, recommend contacting the doctor.

Patient ID: {patient_id}
Drug: {drug_name}
Symptom: {symptom}
Drug resolution confidence: {drug_confidence}
Raw message: {raw_text}

Return ONLY valid JSON with this exact shape:
{{
  "reply": "one short caregiver-facing answer under 280 chars",
  "mechanism": "simple reason this symptom may or may not relate",
  "action": "monitor|consult|urgent",
  "confidence": 0.0,
  "drug_name": "{drug_name}",
  "symptom": "{symptom}"
}}

Rules:
- confidence must be a number from 0 to 1.
- action must be monitor, consult, or urgent.
- No markdown. No extra keys.
/no_think
""".strip()


def _parse_side_effect_json(raw: str | None) -> dict | None:
    try:
        cleaned = re.sub(
            r"^```json\s*|\s*```$",
            "",
            llm_gateway.strip_reasoning_artifacts(raw),
            flags=re.MULTILINE,
        )
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end < start:
            return None
        parsed = json.loads(cleaned[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _valid_side_effect_result(result: dict | None) -> bool:
    if not isinstance(result, dict):
        return False
    if not result.get("reply"):
        return False
    if result.get("action") not in {"monitor", "consult", "urgent"}:
        return False
    try:
        confidence = float(result.get("confidence"))
    except Exception:
        return False
    return 0.0 <= confidence <= 1.0


def _frequency_times_per_day(frequency: str) -> int:
    normalized = str(frequency or "").strip().lower()
    freq_map = {
        "od": 1,
        "qd": 1,
        "once daily": 1,
        "daily": 1,
        "bd": 2,
        "bid": 2,
        "twice daily": 2,
        "tds": 3,
        "tid": 3,
        "three times daily": 3,
        "qid": 4,
        "four times daily": 4,
        "hs": 1,
        "night": 1,
        "sos": 0,
        "prn": 0,
    }
    return freq_map.get(normalized, 1)


def _validate_dose(drug: str, dose: float, unit: str, frequency: str) -> dict:
    """
    Validate dose against known standard daily doses. Flag if > configured max.
    """
    connection = None
    try:
        dose_value = float(dose)
        if dose_value <= 0:
            return {
                "status": "suspicious_dose",
                "message": "Dose must be greater than zero",
                "is_suspicious": True,
            }

        times_per_day = _frequency_times_per_day(frequency)
        prescribed_daily = dose_value * times_per_day

        connection = db._connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT common_doses
                FROM drug_formulary
                WHERE canonical_name = %s;
                """,
                (drug,),
            )
            row = cursor.fetchone()

        if row and row[0]:
            standard_doses = [float(value) for value in row[0] if value is not None]
            if standard_doses:
                max_daily = max(standard_doses) * 4
                if prescribed_daily > max_daily * config.DOSE_MAX_MULTIPLIER:
                    return {
                        "status": "suspicious_dose",
                        "message": "Dose exceeds typical range",
                        "is_suspicious": True,
                    }

        return {
            "status": "active",
            "message": "Within typical range",
            "is_suspicious": False,
        }
    except Exception:
        return {
            "status": "unknown",
            "message": "Unable to validate dose",
            "is_suspicious": False,
        }
    finally:
        if connection is not None:
            connection.close()


def _medication_db_status(dose_check: dict) -> str:
    """
    Map validation result to statuses allowed by medications.status_check.
    Review flags live in alerts/structured_json, not the status column.
    """
    status = str((dose_check or {}).get("status") or "active")
    if status in {"active", "discontinued", "pending_confirmation", "discarded"}:
        return status
    if (dose_check or {}).get("is_suspicious") or status in {"suspicious_dose", "unknown"}:
        return "pending_confirmation"
    return "active"


def clean_drug_name(raw_name: str | None) -> tuple[str | None, str | None, float]:
    """
    Normalize a raw medication name without trusting the LLM as final truth.
    Returns (cleaned_raw, canonical_name, confidence).
    """
    raw = str(raw_name or "").strip()
    if not raw:
        return None, None, 0.0
    cleaned = re.sub(
        r"\b(tab|tablet|cap|capsule|inj|injection|syp|syr|syrup|susp|suspension|drop|drops|cream|ointment|gel|lotion|spray|inhaler|neb|nebulization|nebulisation|solution|soln|sachet|vial|amp|ampoule)\.?\b",
        " ",
        raw,
        flags=re.I,
    )
    cleaned = re.sub(r"\b(take|to|for|after|before|food|morning|night|daily|days?)\b", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"\b\d+\s*(mg|mcg|g|ml|iu|units?|%)\b", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"\b\d+\s*-\s*\d+\s*-\s*\d+(?:\s*-\s*\d+)?\b", " ", cleaned)
    cleaned = re.sub(r"[^A-Za-z0-9+\-/ ]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -/")
    if not cleaned:
        cleaned = raw

    canonical, confidence = _resolve_drug_name(cleaned)
    canonical = str(canonical or "").strip() or None
    confidence = float(confidence or 0.0)
    if not canonical:
        return cleaned, None, 0.0
    return cleaned, canonical, confidence


def normalize_dose_amount(raw_amount) -> tuple[float | None, float]:
    """
    Parse a numeric dose amount. Returns (value, confidence).
    """
    if raw_amount is None:
        return None, 0.0
    if isinstance(raw_amount, (int, float)) and not isinstance(raw_amount, bool):
        value = float(raw_amount)
    else:
        text = str(raw_amount or "").strip()
        match = re.search(r"(\d+(?:\.\d+)?)", text)
        if not match:
            return None, 0.0
        value = float(match.group(1))
    if value <= 0 or value > 10000:
        return value, 0.25
    return value, 1.0


def normalize_dose_unit(raw_unit: str | None) -> tuple[str | None, float]:
    """
    Normalize dose units to a controlled set.
    """
    unit = str(raw_unit or "").strip().lower()
    if not unit:
        return None, 0.0
    aliases = {
        "microgram": "mcg",
        "micrograms": "mcg",
        "µg": "mcg",
        "milligram": "mg",
        "milligrams": "mg",
        "gram": "g",
        "grams": "g",
        "millilitre": "ml",
        "millilitres": "ml",
        "milliliter": "ml",
        "milliliters": "ml",
        "tabs": "tablet",
        "tablets": "tablet",
        "caps": "capsule",
        "capsules": "capsule",
        "teaspoons": "teaspoon",
        "tsp.": "tsp",
        "tablespoons": "tablespoon",
        "tbsp.": "tbsp",
        "sprays": "spray",
        "puffs": "puff",
    }
    unit = aliases.get(unit, unit)
    allowed = set(getattr(config, "MEDICATION_ALLOWED_DOSE_UNITS", set()))
    if unit in allowed:
        return unit, 1.0
    return None, 0.0


def normalize_frequency(raw_frequency: str | None) -> tuple[str | None, float]:
    """
    Normalize prescription frequency shorthand and text.
    """
    text = str(raw_frequency or "").strip().lower()
    if not text:
        return None, 0.0
    text = re.sub(r"\s+", " ", text)
    aliases = getattr(config, "MEDICATION_FREQUENCY_ALIASES", {})
    if text in aliases:
        return aliases[text], 1.0
    match = re.search(r"\b([01]\s*-\s*[01]\s*-\s*[01](?:\s*-\s*[01])?)\b", text)
    if match:
        key = re.sub(r"\s+", "", match.group(1))
        return aliases.get(key), 1.0 if aliases.get(key) else 0.0
    upper = text.upper()
    if upper in {"OD", "BD", "TDS", "QID", "SOS", "HS"}:
        return upper, 1.0
    return None, 0.0


def validate_structured_medication(drug_dict: dict) -> dict:
    """
    Deterministically validate one LLM medication draft before activation.
    """
    med = drug_dict or {}
    raw_name = med.get("drug_name_raw") or med.get("drug_name")
    cleaned_raw, canonical_name, drug_confidence = clean_drug_name(raw_name)
    dose_amount, dose_confidence = normalize_dose_amount(med.get("dose_amount"))
    dose_unit, unit_confidence = normalize_dose_unit(med.get("dose_unit"))
    frequency, frequency_confidence = normalize_frequency(med.get("frequency"))

    unresolved = []
    if not canonical_name or drug_confidence < config.MEDICATION_DRUG_RESOLUTION_MIN_CONFIDENCE:
        unresolved.append("drug_name")
    if dose_amount is None or dose_confidence < 1.0:
        unresolved.append("dose_amount")
    if not dose_unit:
        unresolved.append("dose_unit")
    if not frequency:
        unresolved.append("frequency")

    dose_validation = {"status": "unknown", "is_suspicious": True, "message": "Dose not validated"}
    if canonical_name and dose_amount is not None and dose_unit and frequency:
        dose_validation = _validate_dose(canonical_name, dose_amount, dose_unit, frequency)
        if dose_validation.get("is_suspicious"):
            if "dose_amount" not in unresolved:
                unresolved.append("dose_amount")

    confidence = round(
        (drug_confidence * 0.4)
        + (dose_confidence * 0.2)
        + (unit_confidence * 0.2)
        + (frequency_confidence * 0.2),
        3,
    )
    final_status = (
        config.MEDICATION_STATUS_INTERACTION_PENDING
        if not unresolved and confidence >= config.MEDICATION_VALIDATION_MIN_CONFIDENCE
        else config.MEDICATION_STATUS_SUSPICIOUS
    )
    return {
        "canonical_drug_name": canonical_name,
        "cleaned_drug_name": cleaned_raw,
        "canonical_dose_amount": dose_amount,
        "canonical_dose_unit": dose_unit,
        "canonical_frequency": frequency,
        "validation_confidence": confidence,
        "drug_resolution_confidence": drug_confidence,
        "unresolved_fields": unresolved,
        "dose_validation": dose_validation,
        "final_validation_status": final_status,
    }


def extract_prescription_start_date(med: dict | None, structured_json: dict | None) -> str | None:
    """
    Use the prescription date as medication start_date.
    Returns ISO YYYY-MM-DD when a date is visible; never guesses if absent.
    """
    explicit_sources = [
        (med or {}).get("start_date"),
        (med or {}).get("date"),
        (structured_json or {}).get("start_date"),
        (structured_json or {}).get("date"),
        (structured_json or {}).get("report_date"),
        (structured_json or {}).get("prescription_date"),
    ]
    for source in explicit_sources:
        parsed = _parse_visible_date(source)
        if parsed:
            return parsed

    raw_text = (structured_json or {}).get("_source_raw_text")
    parsed = _parse_document_date_from_text(raw_text)
    if parsed:
        return parsed

    sources = [
        (med or {}).get("raw_segment"),
        raw_text,
    ]
    for source in sources:
        parsed = _parse_visible_date(source)
        if parsed:
            return parsed
    return None


def _parse_document_date_from_text(raw_text) -> str | None:
    """
    Prefer clinically labelled document dates over incidental address, phone,
    registration, OP number, or follow-up dates in OCR text.
    """
    lines = [line.strip() for line in str(raw_text or "").replace("\r", "\n").split("\n") if line.strip()]
    labelled_patterns = (
        r"\b(?:date|prescription\s*date|rx\s*date|visit\s*date|report\s*date)\b\s*[:\-]?\s*(.+)",
        r"(.+?)\s*\b(?:date|prescription\s*date|rx\s*date|visit\s*date|report\s*date)\b",
    )
    for line in lines:
        lowered = line.lower()
        if any(skip in lowered for skip in ("follow up", "follow-up", "review after", "d.no", "reg.", "ph:", "opno")):
            continue
        for pattern in labelled_patterns:
            match = re.search(pattern, line, flags=re.IGNORECASE)
            if not match:
                continue
            parsed = _parse_visible_date(match.group(1))
            if parsed:
                return parsed

    for line in lines:
        lowered = line.lower()
        if any(skip in lowered for skip in ("follow up", "follow-up", "review after", "d.no", "reg.", "ph:", "opno", "address")):
            continue
        parsed = _parse_visible_date(line)
        if parsed:
            return parsed
    return None


def derive_prescriber_specialty(med: dict | None, structured_json: dict | None) -> str | None:
    """
    Store doctor's specialty/department in prescribed_by when visible.
    This avoids saving a person name when the workflow needs clinical context.
    """
    text = " ".join(
        str(item or "")
        for item in [
            (med or {}).get("prescribed_by"),
            (structured_json or {}).get("doctor_name"),
            (structured_json or {}).get("lab_name"),
            (structured_json or {}).get("department"),
            (structured_json or {}).get("specialty"),
            (structured_json or {}).get("speciality"),
            (med or {}).get("raw_segment"),
            (structured_json or {}).get("_source_raw_text"),
        ]
    )
    return _infer_specialty_from_text(text)


def infer_scheduled_times(frequency: str | None, instructions: str | None) -> list[str]:
    """
    Convert frequency/instructions into stable IST medication reminder times.
    Stored as HH:MM strings; UI can label these as IST.
    """
    freq = str(frequency or "").strip().upper()
    text = str(instructions or "").strip().lower()

    if "breakfast" in text or "subah" in text or "morning" in text:
        if "before" in text or "pehle" in text or "empty stomach" in text:
            base = "07:30"
        else:
            base = "08:30"
        if freq in {"BD", "TDS", "QID"}:
            return _times_for_frequency(freq, text, morning_override=base)
        return [base]

    if "lunch" in text or "afternoon" in text or "dopahar" in text:
        return ["12:30" if "before" in text or "pehle" in text else "13:30"]

    if "dinner" in text or "night" in text or "raat" in text or freq == "HS":
        if freq in {"BD", "TDS", "QID"}:
            return _times_for_frequency(freq, text)
        return ["21:30" if freq == "HS" else ("19:30" if "before" in text or "pehle" in text else "20:30")]

    return _times_for_frequency(freq, text)


def _parse_visible_date(value) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    patterns = [
        r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b",
        r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})\b",
        r"\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+(\d{2,4})\b",
        r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+(\d{1,2}),?\s+(\d{2,4})\b",
    ]
    month_map = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    for index, pattern in enumerate(patterns):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        try:
            if index == 0:
                year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
            elif index == 1:
                day, month, year = int(match.group(1)), int(match.group(2)), _normalize_year(match.group(3))
            elif index == 2:
                day = int(match.group(1))
                month = month_map[match.group(2).lower()[:4] if match.group(2).lower().startswith("sept") else match.group(2).lower()[:3]]
                year = _normalize_year(match.group(3))
            else:
                month = month_map[match.group(1).lower()[:4] if match.group(1).lower().startswith("sept") else match.group(1).lower()[:3]]
                day = int(match.group(2))
                year = _normalize_year(match.group(3))
            return date(year, month, day).isoformat()
        except Exception:
            continue
    return None


def _normalize_year(value) -> int:
    year = int(value)
    if year < 100:
        return 2000 + year if year < 70 else 1900 + year
    return year


def _infer_specialty_from_text(text: str) -> str | None:
    lowered = str(text or "").lower()
    specialty_patterns = [
        ("cardiologist", ["cardiologist", "cardiology", "heart specialist", "interventional cardiology"]),
        ("dentist", ["dentist", "dental", "orthodontist", "endodontist", "periodontist"]),
        ("endocrinologist", ["endocrinologist", "diabetologist", "diabetes", "thyroid"]),
        ("pulmonologist", ["pulmonologist", "pulmonology", "chest physician", "respiratory", "asthma"]),
        ("ent specialist", ["ent", "ear nose throat", "otorhinolaryngology"]),
        ("pediatrician", ["pediatrician", "paediatrician", "pediatrics", "paediatrics", "child specialist"]),
        ("dermatologist", ["dermatologist", "dermatology", "skin"]),
        ("orthopedist", ["orthopedic", "orthopaedic", "orthopedist", "orthopaedist", "bone", "joint replacement"]),
        ("neurologist", ["neurologist", "neurology", "neuro physician"]),
        ("nephrologist", ["nephrologist", "nephrology", "renal", "kidney"]),
        ("gastroenterologist", ["gastroenterologist", "gastroenterology", "hepatologist", "liver specialist"]),
        ("gynecologist", ["gynecologist", "gynaecologist", "obstetrician", "obstetrics"]),
        ("urologist", ["urologist", "urology"]),
        ("ophthalmologist", ["ophthalmologist", "ophthalmology", "eye specialist"]),
        ("psychiatrist", ["psychiatrist", "psychiatry"]),
        ("oncologist", ["oncologist", "oncology", "cancer"]),
        ("general physician", ["general physician", "internal medicine", "consultant physician", "family physician", "mbbs"]),
    ]
    for specialty, needles in specialty_patterns:
        if any(needle in lowered for needle in needles):
            return specialty
    return None


def _times_for_frequency(freq: str, instructions: str, morning_override: str | None = None) -> list[str]:
    freq = str(freq or "").upper()
    text = str(instructions or "").lower()
    morning = morning_override or ("07:30" if "before" in text or "empty stomach" in text or "pehle" in text else "08:30")
    lunch = "12:30" if "before" in text or "pehle" in text else "13:30"
    dinner = "19:30" if "before" in text or "pehle" in text else "20:30"
    mapping = {
        "OD": [morning],
        "BD": [morning, dinner],
        "TDS": [morning, lunch, dinner],
        "QID": [morning, "12:30", "17:30", "21:30"],
        "HS": ["21:30"],
        "SOS": [],
        "WEEKLY": [morning],
    }
    return mapping.get(freq, [])


def _interaction_requires_hold(pharma_result: dict | None) -> bool:
    if not isinstance(pharma_result, dict):
        return True
    if pharma_result.get("status") == "error":
        return True
    evaluation = pharma_result.get("evaluation") if isinstance(pharma_result.get("evaluation"), dict) else {}
    if evaluation.get("interactions", []):
        return True
    concrete_warnings = []
    for warning in (
        evaluation.get("medication_context_warnings", [])
        + evaluation.get("renal_warnings", [])
        + evaluation.get("condition_warnings", [])
    ):
        warning_type = str((warning or {}).get("type") or "")
        warning_status = str((warning or {}).get("status") or "")
        severity = str((warning or {}).get("severity") or "medium").lower()
        if warning_type.endswith("_missing") or warning_status == "missing":
            continue
        if severity in {"critical", "high"}:
            concrete_warnings.append(warning)
    if concrete_warnings:
        evaluation["blocking_context_warnings"] = concrete_warnings
        return True
    severity = str(pharma_result.get("max_severity") or evaluation.get("max_severity") or "none").lower()
    return severity in {"critical", "high"} and bool(concrete_warnings)


def _primary_interaction_message(patient_id: str, medication_name: str, pharma_result: dict) -> str:
    patient_name = db.get_patient_name(patient_id) or "Patient"
    severity = str(pharma_result.get("max_severity") or "review").upper()
    evaluation = pharma_result.get("evaluation") if isinstance(pharma_result.get("evaluation"), dict) else {}
    checked = ", ".join(str(item) for item in evaluation.get("active_meds_checked", []) if item) or "active medicines"
    return (
        f"Medication safety review needed for {patient_name}: {medication_name}. "
        f"Severity: {severity}. Checked against {checked}. "
        "This medicine has NOT been activated. Reply in the review flow to approve or veto."
    )


def _notify_primary_caregiver_for_medication_hold(patient_id: str, message: str) -> dict:
    try:
        import notification_dispatcher

        caregivers = db.get_caregivers(patient_id)
        primary = next(
            (
                caregiver
                for caregiver in caregivers
                if str(caregiver.get("role") or "").lower().replace(" ", "_") == "primary_caregiver"
            ),
            caregivers[0] if caregivers else {},
        )
        return notification_dispatcher.dispatch_user_message(
            primary.get("phone"),
            message,
            patient_id=patient_id,
            priority="critical" if "CRITICAL" in message.upper() else "high",
        )
    except Exception as error:
        return {"status": "failed", "error": str(error)}


def process_medication_candidate_after_llm(
    patient_id: str,
    profile_id: str | None,
    med: dict,
    structured_json: dict | None,
    media_upload_id: str | None,
    from_phone: str | None = None,
    source_type: str = "prescription_photo",
) -> dict:
    """
    Safe post-LLM medication state machine.
    LLM output becomes a draft; deterministic validation and interaction checks
    decide whether the medication can become active.
    """
    raw_name = (med or {}).get("drug_name_raw") or (med or {}).get("drug_name")
    start_date = extract_prescription_start_date(med, structured_json)
    specialty = derive_prescriber_specialty(med, structured_json)
    draft_id = db.create_draft_medication(
        patient_id=patient_id,
        raw_drug_name=raw_name,
        structured_medication=med,
        media_upload_id=media_upload_id,
        prescribed_by=specialty,
        source_type=source_type,
        start_date=start_date,
        scheduled_times=[],
    )
    med["medication_id"] = str(draft_id)
    med["_db_status"] = config.MEDICATION_STATUS_DRAFT_EXTRACTED

    validation = validate_structured_medication(med)
    scheduled_times = infer_scheduled_times(
        validation.get("canonical_frequency"),
        " ".join(str(item or "") for item in [med.get("instructions"), med.get("advice")]),
    )
    validation["start_date"] = start_date
    validation["prescriber_specialty"] = specialty
    validation["scheduled_times"] = scheduled_times
    med["start_date"] = start_date
    med["prescribed_by"] = specialty
    med["scheduled_times"] = scheduled_times
    med["deterministic_validation"] = validation
    db.update_medication_validation_status(
        medication_id=draft_id,
        status=validation["final_validation_status"],
        canonical_drug_name=validation.get("canonical_drug_name"),
        dose_amount=validation.get("canonical_dose_amount"),
        dose_unit=validation.get("canonical_dose_unit"),
        frequency=validation.get("canonical_frequency"),
        confidence=validation.get("validation_confidence") or 0.0,
        unresolved_fields=validation.get("unresolved_fields") or [],
        validation_payload=validation,
        scheduled_times=scheduled_times,
    )

    if validation["final_validation_status"] != config.MEDICATION_STATUS_INTERACTION_PENDING:
        task_id = db.create_pending_verification_task(
            patient_id=patient_id,
            medication_id=draft_id,
            reason="deterministic_validation_unresolved",
            payload={"medication": med, "validation": validation, "media_upload_id": media_upload_id},
            from_phone=from_phone,
        )
        db.reject_or_hold_medication(
            medication_id=draft_id,
            status=config.MEDICATION_STATUS_SUSPICIOUS,
            reason="deterministic_validation_unresolved",
            payload={"validation": validation, "pending_task_id": task_id},
        )
        if getattr(config, "MEDICATION_NOTIFY_ON_REVIEW_NEEDED", True):
            _notify_primary_caregiver_for_medication_hold(
                patient_id,
                f"Medication review needed: {raw_name or 'unknown medicine'} could not be safely verified and was not activated.",
            )
        research_tasks = []
        try:
            import pharma_agent

            research_name = validation.get("canonical_drug_name") or raw_name
            research_tasks = pharma_agent.enqueue_research_for_medication_candidate(
                patient_id=patient_id,
                medication_id=str(draft_id),
                drug_name=research_name,
                trigger="held_medication_candidate",
                from_phone=from_phone,
            )
        except Exception as error:
            print(f"held medication research enqueue failed: {error}")
        med["_db_status"] = config.MEDICATION_STATUS_SUSPICIOUS
        med["pending_verification_task_id"] = task_id
        med["pharma_research_tasks_queued"] = research_tasks
        return {
            "status": config.MEDICATION_STATUS_SUSPICIOUS,
            "medication_id": str(draft_id),
            "active": False,
            "validation": validation,
            "pending_task_id": task_id,
            "research_tasks_queued": research_tasks,
        }

    import pharma_agent

    engine = pharma_agent.PharmaSafetyEngine()
    active_meds = db.get_active_medications_schedule(patient_id)
    patient_context = {
        "active_meds": active_meds,
        "conditions": db.get_patient_conditions(patient_id),
        "renal_markers": db.get_patient_latest_renal_markers(patient_id),
        "patient_name": db.get_patient_name(patient_id) or "Patient",
        "dose_amount": validation.get("canonical_dose_amount"),
        "prescribed_by": specialty,
        "trigger": "pre_activation_validation",
        "medication_id": str(draft_id),
        "skip_external": True,
    }
    evaluation = engine.evaluate(patient_id, validation["canonical_drug_name"], patient_context)
    blocking_findings = (
        evaluation.get("interactions", [])
        + [
            warning
            for warning in (
                evaluation.get("medication_context_warnings", [])
                + evaluation.get("renal_warnings", [])
                + evaluation.get("condition_warnings", [])
            )
            if not str((warning or {}).get("type") or "").endswith("_missing")
            and str((warning or {}).get("status") or "") != "missing"
            and str((warning or {}).get("severity") or "medium").lower() in {"critical", "high"}
        ]
    )
    evaluation["blocking_findings"] = blocking_findings
    pharma_result = {
        "status": "completed",
        "patient_id": patient_id,
        "new_drug": validation["canonical_drug_name"],
        "dose_amount": validation.get("canonical_dose_amount"),
        "trigger": "pre_activation_validation",
        "max_severity": engine._max_severity(blocking_findings) if blocking_findings else "none",
        "interactions_count": len(evaluation.get("interactions", [])),
        "alerts_created": [],
        "approvals_created": [],
        "evaluation": evaluation,
    }
    med["pharma_agent_result"] = pharma_result

    if not _interaction_requires_hold(pharma_result):
        db.promote_medication_to_active(
            medication_id=draft_id,
            activation_decision="validated_no_interaction",
            interaction_payload=pharma_result,
        )
        med["_db_status"] = config.MEDICATION_STATUS_ACTIVE
        return {
            "status": config.MEDICATION_STATUS_ACTIVE,
            "medication_id": str(draft_id),
            "active": True,
            "validation": validation,
            "pharma_agent": pharma_result,
        }

    severity = str((pharma_result or {}).get("max_severity") or "medium").lower()
    if severity not in {"critical", "high", "medium", "low"}:
        severity = "medium"
    evaluation = pharma_result.get("evaluation") if isinstance((pharma_result or {}).get("evaluation"), dict) else {}
    interactions = evaluation.get("interactions") or [{}]
    approval_hashes = []
    alert_ids = []
    for interaction in interactions:
        existing_drug = interaction.get("existing_drug") or interaction.get("drug_b") or "active_medication"
        rule_hash = db.create_veto_approval_record(
            patient_id=patient_id,
            medication_id=draft_id,
            new_drug=validation["canonical_drug_name"],
            existing_drug=existing_drug,
            severity=severity,
            interaction_payload={"pharma_agent": pharma_result, "interaction": interaction},
        )
        approval_hashes.append(rule_hash)
    alert_message = _primary_interaction_message(patient_id, validation["canonical_drug_name"], pharma_result)
    alert_id = db.create_interaction_alert(
        patient_id=patient_id,
        medication_id=draft_id,
        severity=severity,
        message=alert_message,
        payload={"validation": validation, "pharma_agent": pharma_result, "approval_hashes": approval_hashes},
    )
    alert_ids.append(alert_id)
    db.reject_or_hold_medication(
        medication_id=draft_id,
        status=config.MEDICATION_STATUS_VETO_REQUIRED,
        reason="interaction_or_context_review_required",
        payload={"validation": validation, "pharma_agent": pharma_result, "alert_ids": alert_ids},
        approval_rule_hash=approval_hashes[0] if approval_hashes else None,
    )
    notification = _notify_primary_caregiver_for_medication_hold(patient_id, alert_message)
    med["_db_status"] = config.MEDICATION_STATUS_VETO_REQUIRED
    med["approval_hashes"] = approval_hashes
    med["alert_ids"] = alert_ids
    return {
        "status": config.MEDICATION_STATUS_VETO_REQUIRED,
        "medication_id": str(draft_id),
        "active": False,
        "validation": validation,
        "pharma_agent": pharma_result,
        "approval_hashes": approval_hashes,
        "alert_ids": alert_ids,
        "notification": notification,
    }


def write_medication_from_json(
    patient_id,
    profile_id,
    structured_json,
    media_upload_id,
    from_phone: str | None = None,
    trigger_pharma_agent: bool = False,
) -> list[str]:
    """
    Map LLM-extracted medication JSON to DB INSERT.
    Returns list of medication IDs that were safely activated.
    """
    inserted_ids = []
    try:
        medications = (structured_json or {}).get("medications", [])
        if isinstance(medications, dict):
            medications = [medications]

        for med in medications:
            if not isinstance(med, dict):
                continue
            drug_raw = med.get("drug_name_raw")
            if not drug_raw:
                continue

            canonical, conf = _resolve_drug_name(drug_raw)
            dose = _to_float(med.get("dose_amount"))
            med["drug_name_canonical"] = canonical
            med["drug_resolution_confidence"] = conf
            if canonical and db.is_duplicate_medication(patient_id, canonical, dose, None):
                med["_db_status"] = "duplicate_skipped"
                continue
            result = process_medication_candidate_after_llm(
                patient_id=patient_id,
                profile_id=profile_id,
                med=med,
                structured_json=structured_json or {},
                media_upload_id=media_upload_id,
                from_phone=from_phone,
                source_type="prescription_photo",
            )
            med["safe_ingestion_result"] = result
            if result.get("active"):
                inserted_ids.append(str(result.get("medication_id")))
    except Exception as error:
        print(error)
    return inserted_ids


def _trigger_pharma_agent_for_prescription(
    patient_id: str,
    drug_name: str,
    dose_amount: float | None,
    prescribed_by: str | None,
    from_phone: str | None,
) -> bool:
    """
    Fire PharmaAgent immediately after a prescription medication insert.
    The agent owns its own error/audit behavior; ingestion should never block
    on this safety review.
    """
    try:
        import pharma_agent

        pharma_agent.process_new_medication(
            patient_id=patient_id,
            new_drug=drug_name,
            dose_amount=dose_amount,
            prescribed_by=prescribed_by,
            from_phone=from_phone,
            trigger="prescription_photo",
        )
        return True
    except Exception as error:
        print(f"PharmaAgent trigger failed: {error}")
        return False


def write_lab_from_json(patient_id, profile_id, structured_json, media_upload_id) -> list[str]:
    """
    Map LLM-extracted lab JSON to DB INSERT.
    Returns list of inserted lab report IDs.
    """
    inserted_ids = []
    try:
        labs = (structured_json or {}).get("lab_values", [])
        if isinstance(labs, dict):
            labs = [labs]

        for lab in labs:
            if not isinstance(lab, dict):
                continue
            test_name = _normalize_lab_test_name(lab.get("test_name"))
            value = _to_float(lab.get("test_value"))
            if not test_name or value is None:
                continue

            status = "active"
            ref_low = _to_float(lab.get("reference_range_low"))
            ref_high = _to_float(lab.get("reference_range_high"))

            limits = config.LAB_PHYSICAL_LIMITS.get(test_name)
            if limits and (value < limits[0] or value > limits[1]):
                status = "suspicious"
                db.create_alert(
                    patient_id,
                    "suspicious_lab_value",
                    "high",
                    f"{test_name} = {value} is outside physical limits.",
                    {"test_name": test_name, "value": value, "media_upload_id": media_upload_id},
                )
            else:
                last = db.get_last_lab_value(patient_id, test_name)
                if last and last.get("test_value"):
                    try:
                        last_val = float(last["test_value"])
                    except Exception:
                        last_val = 0.0
                    if last_val > 0 and (
                        value > last_val * config.LAB_VALUE_JUMP_MULTIPLIER
                        or value < last_val / config.LAB_VALUE_JUMP_MULTIPLIER
                    ):
                        status = "suspicious"
                        db.create_alert(
                            patient_id,
                            "lab_baseline_jump",
                            "medium",
                            f"{test_name} jumped from {last_val} to {value}.",
                            {
                                "test_name": test_name,
                                "previous_value": last_val,
                                "new_value": value,
                                "media_upload_id": media_upload_id,
                            },
                        )

            lab_id = db.insert_lab_report(
                patient_id=patient_id,
                test_name=test_name,
                test_value=value,
                unit=lab.get("unit"),
                reference_low=ref_low,
                reference_high=ref_high,
                confidence=0.85,
                status=status,
                media_upload_id=media_upload_id,
            )
            if lab_id:
                inserted_ids.append(str(lab_id))
    except Exception as error:
        print(error)
    return inserted_ids


def write_note_from_json(patient_id, profile_id, structured_json, media_upload_id) -> None:
    """
    Map general note JSON to audit_log as a structured observation.
    """
    try:
        events = (structured_json or {}).get("events", [])
        if isinstance(events, dict):
            events = [events]

        for event in events:
            if not isinstance(event, dict):
                continue
            db.write_audit(
                patient_id=patient_id,
                profile_id=profile_id,
                entity_type="caregiver_observation",
                entity_id=media_upload_id,
                action="OBSERVATION_LOGGED",
                actor_role="caregiver",
                new_value={
                    "event_type": event.get("event_type"),
                    "description": event.get("description"),
                    "time_of_day": event.get("time_of_day"),
                    "source": "voice_note",
                    "media_upload_id": media_upload_id,
                },
            )
    except Exception as error:
        print(error)


def _summary(
    success: bool,
    medications_added: int = 0,
    alerts_created: int = 0,
    error_message: str | None = None,
    **extra,
) -> dict:
    result = {
        "success": success,
        "medications_added": medications_added,
        "alerts_created": alerts_created,
        "error_message": error_message,
    }
    result.update(extra)
    return result


def _fail_task(task_id, message: str) -> dict:
    try:
        db.update_pending_task_status(task_id, "failed", {"error": message}, message)
    except Exception:
        pass
    return _summary(False, error_message=message)


def _to_float(value) -> float | None:
    try:
        if value is None:
            return None
        match = re.search(r"\d+(?:\.\d+)?", str(value))
        return float(match.group(0)) if match else None
    except Exception:
        return None


def _normalize_frequency(value: str | None) -> str:
    try:
        text = str(value or "").strip().lower()
        aliases = {
            "once": "OD",
            "once daily": "OD",
            "daily": "OD",
            "od": "OD",
            "qd": "OD",
            "twice": "BD",
            "twice daily": "BD",
            "bd": "BD",
            "bid": "BD",
            "three times": "TDS",
            "three times daily": "TDS",
            "tds": "TDS",
            "tid": "TDS",
            "four times": "QID",
            "qid": "QID",
            "night": "HS",
            "bedtime": "HS",
            "hs": "HS",
            "sos": "SOS",
            "as needed": "SOS",
        }
        return aliases.get(text, str(value or "").strip())
    except Exception:
        return str(value or "").strip()


def _prescription_patterns() -> dict:
    return {
        "drug_name": (
            r"\b([A-Z][A-Za-z0-9-]{2,}(?:\s+[A-Z][A-Za-z0-9-]{2,})?)\s+\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml|units?)\b",
            lambda value: value.strip(),
        ),
        "dose_amount": (
            r"\b(\d+(?:\.\d+)?)\s*(?:mg|mcg|g|ml|units?)\b",
            lambda value: float(value),
        ),
        "dose_unit": (
            r"\b\d+(?:\.\d+)?\s*(mg|mcg|g|ml|units?)\b",
            lambda value: value.strip(),
        ),
        "frequency": (
            r"\b(OD|BD|TDS|QID|HS|SOS|once daily|twice daily|three times daily|daily|bedtime|as needed)\b",
            _normalize_frequency,
        ),
        "instructions": (
            r"\b(before food|after food|with food|empty stomach|khane ke baad|khane se pehle|raat ko|subah)\b",
            lambda value: value.strip(),
        ),
        "duration": (
            r"\b(?:for|duration|x)\s*(\d+\s*(?:days?|weeks?|months?))\b",
            lambda value: value.strip(),
        ),
        "prescribed_by": (
            r"\b(?:Dr\.?|Doctor)\s+([A-Za-z][A-Za-z .-]{2,40})",
            lambda value: f"Dr. {value.strip()}",
        ),
    }


def _essential_prescription_fields(data: dict | None) -> bool:
    return bool(data and data.get("drug_name") and _to_float(data.get("dose_amount")) is not None)


def _prescription_has_medications(data: dict | None) -> bool:
    try:
        meds = (data or {}).get("medications")
        return isinstance(meds, list) and any(
            isinstance(med, dict) and med.get("drug_name_raw") for med in meds
        )
    except Exception:
        return False


def _extract_prescription_medications_deterministic(raw_text: str) -> dict:
    """
    Extract multiple medication rows from OCR text without forcing a single match.

    This handles common prescription OCR shapes:
    - TAB METFORMIN 500mg BD
    - TAB VITAMIN C one gram once a day
    - SYRUP ALEX 2/3 teaspoon 3 times a day
    """
    try:
        segments = _split_prescription_segments(raw_text)
        medications = []
        seen = set()
        doctor = _extract_prescribed_by(raw_text)
        for segment in segments:
            med = _parse_prescription_segment(segment, doctor)
            if not med:
                continue
            key = _medication_dedupe_key(med)
            if not key or key in seen:
                continue
            seen.add(key)
            medications.append(med)

        return {
            "type": "prescription",
            "medications": medications,
            "doctor_name": doctor,
            "date": extract_prescription_start_date({}, {"_source_raw_text": raw_text}),
            "start_date": extract_prescription_start_date({}, {"_source_raw_text": raw_text}),
        }
    except Exception:
        return {"type": "prescription", "medications": [], "doctor_name": None, "date": None, "start_date": None}


def _split_prescription_segments(raw_text: str) -> list[str]:
    try:
        text = _clean_extracted_text(raw_text)
        text = re.sub(r"\s+", " ", text)
        # Add boundaries before medication markers and after common frequency endings.
        text = re.sub(
            r"\b(Rx|Lx|TAB|TABLET|CAP|CAPSULE|SYRUP|SYP|SYR|INJ|INJECTION|SUSP|SUSPENSION|DROP|DROPS|CREAM|OINTMENT|GEL|LOTION|SPRAY|INHALER|NEB|NEBULIZATION|NEBULISATION|SOLUTION|SOLN|SACHET|VIAL|AMP|AMPOULE)\b",
            r"\n\1",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"\b(once a day|once daily|twice daily|three times a day|3 times a day|once a week|SOS|BD|TDS|QID|HS|\d\s*-\s*\d\s*-\s*\d)\b",
            r"\1\n",
            text,
            flags=re.IGNORECASE,
        )
        segments = []
        for raw_segment in text.splitlines():
            segment = raw_segment.strip(" .•*-:;")
            if len(segment) < 3:
                continue
            if _looks_like_medication_segment(segment):
                segments.append(segment)
        return segments
    except Exception:
        return []


def _parse_prescription_segment(segment: str, doctor: str | None = None) -> dict | None:
    try:
        original = str(segment or "").strip()
        if not original:
            return None

        cleaned = re.sub(r"^[*•.\-\s]+", "", original)
        cleaned = re.sub(r"^(?:rx|lx)\.?\s+", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(
            r"^(?:tab|tablet|cap|capsule|syrup|syp|syr|inj|injection|susp|suspension|drop|drops|cream|ointment|gel|lotion|spray|inhaler|neb|nebulization|nebulisation|solution|soln|sachet|vial|amp|ampoule)\.?\s+",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.split(r"\b(?:D\.?\s*RAJ|DR\.?\s+|MBBS|SENIOR\s*CONSULTANT|Residence)\b", cleaned, maxsplit=1, flags=re.IGNORECASE)[0]
        cleaned = re.sub(r"\b(?:in case of|in case if|in case|for)\s+(?:fever|pain|throat|cough|cold)\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.strip(" .:-")
        if not cleaned:
            return None

        dose_amount, dose_unit = _extract_dose_from_segment(cleaned)
        frequency = _extract_frequency_from_segment(cleaned)
        drug_name = _extract_drug_name_from_segment(cleaned)
        if not drug_name:
            return None

        return {
            "drug_name_raw": drug_name,
            "drug_name_canonical": None,
            "dose_amount": dose_amount,
            "dose_unit": dose_unit,
            "frequency": frequency,
            "instructions": _extract_instructions_from_segment(original),
            "advice": _extract_advice_from_segment(original),
            "prescribed_by": doctor,
            "date": None,
            "raw_segment": original,
            "extraction_method": "deterministic_multi",
        }
    except Exception:
        return None


def _extract_dose_from_segment(segment: str) -> tuple[float | None, str | None]:
    try:
        normalized_segment = _normalize_ocr_dose_text(segment)
        fraction = re.search(r"\b(\d+)\s*/\s*(\d+)\s*(?:tea|teaspoon|spoon)", normalized_segment, flags=re.IGNORECASE)
        if fraction and float(fraction.group(2)) != 0:
            return float(fraction.group(1)) / float(fraction.group(2)), "teaspoon"

        numeric = re.search(
            r"\b(\d+(?:\.\d+)?)\s*(mg|mcg|g|gram|grams|ml|iu|units?|tsp|teaspoon|tbsp|tablespoon|spoon|drops?|puffs?|sprays?|sachet|vial|amp|ampoule|application|applications)\b",
            normalized_segment,
            flags=re.IGNORECASE,
        )
        if numeric:
            unit = numeric.group(2).lower()
            if unit in {"gram", "grams"}:
                unit = "g"
            return float(numeric.group(1)), unit

        word_map = {
            "one": 1.0,
            "two": 2.0,
            "three": 3.0,
            "half": 0.5,
        }
        word = re.search(r"\b(one|two|three|half)\s+(gram|grams|g|teaspoon|spoon|tsp|tbsp|drop|drops|puff|puffs|spray|sprays)\b", normalized_segment, flags=re.IGNORECASE)
        if word:
            unit = word.group(2).lower()
            if unit in {"gram", "grams"}:
                unit = "g"
            return word_map[word.group(1).lower()], unit
    except Exception:
        pass
    return None, None


def _normalize_ocr_dose_text(segment: str) -> str:
    try:
        text = str(segment or "")
        # OCR often reads handwritten dose rows like "Telma-AM to 30 1-0-0".
        # Treat the number immediately before an Indian regimen as mg when no
        # explicit unit is present, and remove the noisy "to" linker.
        text = re.sub(
            r"\bto\s+(\d{1,4})(?=\s+\d\s*-\s*\d\s*-\s*\d\b)",
            r"\1 mg",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"\b(\d{1,4})(?=\s+\d\s*-\s*\d\s*-\s*\d\b)",
            lambda m: m.group(0) if re.search(r"(?:mg|mcg|g|ml)\s*$", text[: m.start()], flags=re.IGNORECASE) else f"{m.group(1)} mg",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(r"\b([Iil])O\s*mg\b", "10 mg", text, flags=re.IGNORECASE)
        text = re.sub(r"\b(\d+)OO\s*mg\b", r"\g<1>00 mg", text, flags=re.IGNORECASE)
        text = re.sub(r"\b(\d+)O\s*mg\b", r"\g<1>0 mg", text, flags=re.IGNORECASE)
        text = re.sub(r"\b(\d+)SO\s*M\b", r"\g<1>50 mg", text, flags=re.IGNORECASE)
        text = re.sub(r"\b(\d+)S0\s*M\b", r"\g<1>50 mg", text, flags=re.IGNORECASE)
        text = re.sub(r"\b(\d+)\s*g\b", lambda m: f"{m.group(1)} mg" if float(m.group(1)) >= 10 else m.group(0), text, flags=re.IGNORECASE)
        text = re.sub(r"\b(\d+)\s*/\s*B\b", r"\1/3", text, flags=re.IGNORECASE)
        text = re.sub(r"\b3\s*tines?\s*a\s*day\b", "three times a day", text, flags=re.IGNORECASE)
        return text
    except Exception:
        return str(segment or "")


def _extract_frequency_from_segment(segment: str) -> str | None:
    try:
        regimen = re.search(r"\b([01])\s*-\s*([01])\s*-\s*([01])\b", segment)
        if regimen:
            pattern = "".join(regimen.groups())
            regimen_map = {
                "100": "OD",
                "010": "OD",
                "001": "HS",
                "101": "BD",
                "110": "BD",
                "011": "BD",
                "111": "TDS",
            }
            if pattern in regimen_map:
                return regimen_map[pattern]
        patterns = [
            (r"\bSOS\b", "SOS"),
            (r"\bOD\b|\bonce daily\b|\bonce a day\b|\bdaily\b", "OD"),
            (r"\bBD\b|\btwice daily\b|\btwice a day\b", "BD"),
            (r"\bTDS\b|\bthree times daily\b|\bthree times a day\b|\b3\s*times\b|\b3\s*tines?\s*a\s*day\b", "TDS"),
            (r"\bQID\b|\bfour times daily\b|\bfour times a day\b", "QID"),
            (r"\bHS\b|\bbedtime\b|\bnight\b", "HS"),
            (r"\bonce a week\b|\bweekly\b", "weekly"),
        ]
        for pattern, value in patterns:
            if re.search(pattern, segment, flags=re.IGNORECASE):
                return value
    except Exception:
        pass
    return None


def _extract_drug_name_from_segment(segment: str) -> str | None:
    try:
        normalized = _normalize_ocr_dose_text(segment)
        stop_match = re.search(
            r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|g|gram|grams|ml|iu|units?|tsp|teaspoon|tbsp|tablespoon|spoon|drops?|puffs?|sprays?|sachet|vial|amp|ampoule|application|applications)\b|\b\d+\s*/\s*\d+\s*(?:tea|teaspoon|spoon)?\b|\b(?:one|two|three|half)\s+(?:gram|grams|g|teaspoon|spoon|tsp|tbsp|drop|drops|puff|puffs|spray|sprays)\b|\b(?:once|twice|three|four|daily|sos|od|bd|tds|qid|hs)\b|\b\d\s*-\s*\d\s*-\s*\d\b",
            normalized,
            flags=re.IGNORECASE,
        )
        raw_name = normalized[: stop_match.start()] if stop_match else normalized
        raw_name = re.sub(
            r"\b(shake well|complete course|avoid alcohol|do not drive|apply locally|gargle|steam inhalation|dilute before use|rinse mouth|keep refrigerated)\b",
            " ",
            raw_name,
            flags=re.IGNORECASE,
        )
        raw_name = re.sub(r"[^A-Za-z0-9/+ -]+", " ", raw_name)
        raw_name = re.sub(r"\b(?:tab|tablet|cap|capsule|syrup|syp|syr|inj|injection|susp|suspension|drop|drops|cream|ointment|gel|lotion|spray|inhaler|neb|nebulization|nebulisation|solution|soln|sachet|vial|amp|ampoule|in|case|of|if|fever|pain|throat|cough|and|for|to)\b", " ", raw_name, flags=re.IGNORECASE)
        raw_name = re.sub(r"\s+", " ", raw_name).strip(" -/")
        if len(raw_name) < 2:
            return None
        if raw_name.lower() in {"tab", "cap", "syrup", "syp", "syr", "inj", "drop", "medicine"}:
            return None
        return _normalize_drug_name_ocr(raw_name)
    except Exception:
        return None


def _normalize_drug_name_ocr(raw_name: str) -> str:
    try:
        name = re.sub(r"\s+", " ", str(raw_name or "")).strip()
        name = re.sub(r"^(?:tab|tablet|cap|capsule|syrup|syp|syr|inj|injection|susp|suspension|drop|drops|cream|ointment|gel|lotion|spray|inhaler|neb|nebulization|nebulisation|solution|soln|sachet|vial|amp|ampoule)\.?\s+", "", name, flags=re.IGNORECASE)
        name = re.sub(r"\s*-\s*", "-", name)
        name = re.sub(r"\bto\b\s*$", "", name, flags=re.IGNORECASE)
        replacements = {
            r"\b2ING\b": "ZINC",
            r"\bCROUIN\b": "CROCIN",
            r"\bCROUN\b": "CROCIN",
            r"\bCHLORD\s+QUINE\b": "CHLOROQUINE",
            r"\bHYDRONY\b": "HYDROXY",
        }
        for pattern, replacement in replacements.items():
            name = re.sub(pattern, replacement, name, flags=re.IGNORECASE)
        return name.strip()
    except Exception:
        return str(raw_name or "").strip()


def _extract_instructions_from_segment(segment: str) -> str | None:
    try:
        lowered = segment.lower()
        instructions = []
        for phrase in ["in case of fever", "in case of throat pain", "in case of cough", "before food", "after food", "with food", "empty stomach"]:
            if phrase in lowered:
                instructions.append(phrase)
        return "; ".join(instructions) if instructions else None
    except Exception:
        return None


def _extract_advice_from_segment(segment: str) -> str | None:
    try:
        lowered = segment.lower()
        advice = []
        for phrase in [
            "shake well",
            "complete course",
            "avoid alcohol",
            "do not drive",
            "apply locally",
            "gargle",
            "steam inhalation",
            "dilute before use",
            "rinse mouth",
            "keep refrigerated",
        ]:
            if phrase in lowered:
                advice.append(phrase)
        return "; ".join(advice) if advice else None
    except Exception:
        return None


def _extract_prescribed_by(raw_text: str) -> str | None:
    try:
        match = re.search(r"\bDr\.?\s+([A-Za-z][A-Za-z .-]{2,50})", raw_text or "", flags=re.IGNORECASE)
        if match:
            return "Dr. " + re.sub(r"\s+", " ", match.group(1)).strip()
    except Exception:
        pass
    return None


def _medication_dedupe_key(med: dict) -> tuple | None:
    try:
        name = _normalize_for_dedupe(med.get("drug_name_raw") or "")
        dose = _to_float(med.get("dose_amount"))
        unit = str(med.get("dose_unit") or "").lower()
        freq = str(med.get("frequency") or "").lower()
        if not name:
            return None
        return (name, dose, unit, freq)
    except Exception:
        return None


def _prescription_llm_prompt() -> str:
    return (
        "Extract prescription medication details as JSON with keys: "
        "drug_name, dose_amount, dose_unit, frequency, instructions, duration, "
        "prescribed_by, confidence_per_field. Use null for unknown fields. "
        "Do not infer numbers or doses that are not present in the OCR text."
    )


def _numeric_value_allowed(value, raw_text: str) -> bool:
    try:
        raw_numbers = {match.group(0).lstrip("0") or "0" for match in re.finditer(r"\d+(?:\.\d+)?", raw_text or "")}
        for match in re.finditer(r"\d+(?:\.\d+)?", str(value)):
            number = match.group(0).lstrip("0") or "0"
            if number not in raw_numbers:
                return False
        return True
    except Exception:
        return False


def _filter_hallucinated_numbers(data: dict, raw_text: str) -> dict:
    """
    Drop LLM-extracted numeric values that cannot be grounded in OCR text.
    """
    filtered = {}
    for key, value in (data or {}).items():
        try:
            if value is None:
                filtered[key] = None
                continue
            if isinstance(value, dict):
                nested = _filter_hallucinated_numbers(value, raw_text)
                if nested:
                    filtered[key] = nested
                continue
            if re.search(r"\d", str(value)) and not _numeric_value_allowed(value, raw_text):
                continue
            filtered[key] = value
        except Exception:
            continue
    return filtered


def _insert_medication_from_prescription(
    patient_id: str,
    structured_json: dict,
    canonical_drug: str,
    drug_confidence: float,
    dose_validation: dict,
    raw_text: str,
    upload_id: str | None,
) -> str | None:
    connection = None
    try:
        status = _medication_db_status(dose_validation)
        dose_amount = _to_float(structured_json.get("dose_amount"))
        if db.is_duplicate_medication(patient_id, canonical_drug, dose_amount, None):
            return None
        confidence = min(
            float(drug_confidence or 0.5),
            float(structured_json.get("parser_confidence") or 0.8),
        )
        start_date = extract_prescription_start_date(structured_json, {"_source_raw_text": raw_text})
        specialty = derive_prescriber_specialty(structured_json, {"_source_raw_text": raw_text})
        frequency = _normalize_frequency(structured_json.get("frequency"))
        scheduled_times = infer_scheduled_times(
            frequency,
            " ".join(str(item or "") for item in [structured_json.get("instructions"), structured_json.get("advice")]),
        )
        connection = db._connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO medications (
                    patient_id,
                    drug_name,
                    dose_amount,
                    dose_unit,
                    frequency,
                    instructions,
                    advice,
                    duration,
                    prescribed_by,
                    start_date,
                    scheduled_times,
                    status,
                    confidence,
                    source_type,
                    raw_text,
                    media_upload_id,
                    recorded_at,
                    updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'prescription_photo', %s, %s, NOW(), NOW())
                RETURNING id::text;
                """,
                (
                    db._uuid_or_none(patient_id),
                    canonical_drug,
                    dose_amount,
                    structured_json.get("dose_unit"),
                    frequency,
                    structured_json.get("instructions"),
                    structured_json.get("advice"),
                    structured_json.get("duration"),
                    specialty,
                    start_date,
                    scheduled_times,
                    status,
                    confidence,
                    raw_text,
                    db._uuid_or_none(upload_id),
                ),
            )
            row = cursor.fetchone()
        connection.commit()
        return row[0] if row else None
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
        return None
    finally:
        if connection is not None:
            connection.close()


def _safe_media_upload(
    task: dict,
    profile: dict | None,
    temp_file_path: str,
    raw_text: str,
    ocr_confidence: float,
    file_hash: str | None = None,
) -> str | None:
    """
    Create media_uploads row using schema-approved final_status values.
    """
    return db.insert_media_upload(
        patient_id=task.get("patient_id"),
        profile_id=(profile or {}).get("id"),
        pending_task_id=task.get("id"),
        file_path=temp_file_path,
        file_type="image",
        parser_type="paddleocr",
        raw_text=raw_text,
        structured_json={"file_hash": file_hash} if file_hash else None,
        parser_confidence=ocr_confidence,
        final_status="active",
    )


def _run_prescription_post_processing(patient_id: str, drug_name: str, duration: str | None, from_phone: str | None) -> None:
    try:
        import pharma_agent

        pharma_agent.process_new_medication(
            patient_id=patient_id,
            new_drug=drug_name,
            dose_amount=None,
            prescribed_by=None,
            from_phone=from_phone,
            trigger="prescription_photo",
        )
    except Exception:
        pass

    try:
        import crisis

        if hasattr(crisis, "get_emergency_packet"):
            crisis.get_emergency_packet(patient_id)
    except Exception:
        pass

    if duration:
        try:
            db.create_pending_task(
                patient_id,
                "llm_reasoning",
                None,
                None,
                from_phone,
                {
                    "task": "schedule_refill_reminder",
                    "drug_name": drug_name,
                    "duration": duration,
                },
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Voice note processing notes
# ---------------------------------------------------------------------------
# Audit review showed common spoken/text-equivalent messages such as:
# "took night meds", "Dad was weak today but took all medicines", "nahi li",
# and compound messages like "Hi, took Metformin, when is next appointment?".
# The voice path therefore does two things:
# 1. Routes the transcript through the same router/handlers as text chat.
# 2. For longer or unclear transcripts, extracts structured events so medication
#    reports, symptoms, and diet notes can still be recorded deterministically.
#
# Future improvements:
# - Store Whisper segment timestamps for more precise event timing.
# - Add Hinglish speech-normalization before event extraction.
# - Add a dedicated symptom_log table; for now symptoms/diet are audit entries.
# - Add speaker diarization for family/caregiver group voice notes.


def _voice_summary(
    success: bool,
    events_recorded: int = 0,
    alerts_created: int = 0,
    error_message: str | None = None,
    **extra,
) -> dict:
    result = {
        "success": success,
        "events_recorded": events_recorded,
        "alerts_created": alerts_created,
        "error_message": error_message,
    }
    result.update(extra)
    return result


def _voice_llm_prompt() -> str:
    return (
        "Extract care events from this voice transcript as JSON. Return exactly: "
        "{\"events\":[{\"event_type\":\"taken|missed|symptom|diet\","
        "\"medication_name\":string|null,\"time_of_day\":string|null,"
        "\"details\":string|null}]}. Include only events clearly stated."
    )


def _is_long_or_compound_transcript(transcript: str) -> bool:
    try:
        words = transcript.split()
        if len(words) > 12:
            return True
        lowered = transcript.lower()
        return any(marker in lowered for marker in [",", " and ", " also ", " aur ", " but ", " phir "])
    except Exception:
        return False


def _extract_time_of_day(text: str) -> str | None:
    try:
        lowered = str(text or "").lower()
        if any(word in lowered for word in ["morning", "subah", "breakfast"]):
            return "morning"
        if any(word in lowered for word in ["afternoon", "dopahar", "lunch"]):
            return "afternoon"
        if any(word in lowered for word in ["evening", "shaam"]):
            return "evening"
        if any(word in lowered for word in ["night", "raat", "bedtime", "dinner"]):
            return "night"
    except Exception:
        return None
    return None


def _extract_medication_name_from_text(text: str) -> str | None:
    try:
        excluded = {
            "took", "taken", "missed", "skipped", "skip", "medicine", "medicines",
            "tablet", "tablets", "dose", "night", "morning", "evening", "today",
            "aaj", "subah", "shaam", "raat", "nahi", "nhi", "liya", "khaya",
            "dad", "uncle", "patient", "papa", "mummy",
        }
        words = [
            word
            for word in re.findall(r"\b[A-Za-z][A-Za-z0-9-]{2,}\b", text or "")
            if word.lower() not in excluded
        ]
        return words[0] if words else None
    except Exception:
        return None


def _extract_voice_events_deterministic(transcript: str) -> list[dict]:
    events = []
    try:
        text = str(transcript or "")
        clauses = [
            clause.strip()
            for clause in re.split(r",|;|\.|\band\b|\balso\b|\baur\b|\bbut\b|\bphir\b", text, flags=re.IGNORECASE)
            if clause.strip()
        ] or [text]

        med_action_words = {
            "taken": ["took", "taken", "le li", "liya", "kha li", "done", "de di", "given"],
            "missed": ["missed", "skipped", "skip", "nahi li", "nhi li", "not taken", "bhool"],
        }
        symptom_words = [
            "pain", "dard", "fever", "bukhar", "weak", "kamzori", "chakkar",
            "dizzy", "vomit", "ulti", "nausea", "breathing", "saans", "cough",
        ]
        diet_words = ["breakfast", "lunch", "dinner", "khana", "khaya", "diet", "nahi khaya", "fasting"]

        for clause in clauses:
            clause_lower = clause.lower()
            event_type = None
            for candidate, words in med_action_words.items():
                if any(word in clause_lower for word in words):
                    event_type = candidate
                    break

            if event_type:
                events.append(
                    {
                        "event_type": event_type,
                        "medication_name": _extract_medication_name_from_text(clause),
                        "time_of_day": _extract_time_of_day(clause),
                        "details": clause,
                        "source": "deterministic",
                    }
                )
                continue

            if any(word in clause_lower for word in symptom_words):
                events.append(
                    {
                        "event_type": "symptom",
                        "medication_name": None,
                        "time_of_day": _extract_time_of_day(clause),
                        "details": clause,
                        "source": "deterministic",
                    }
                )
                continue

            if any(word in clause_lower for word in diet_words):
                events.append(
                    {
                        "event_type": "diet",
                        "medication_name": None,
                        "time_of_day": _extract_time_of_day(clause),
                        "details": clause,
                        "source": "deterministic",
                    }
                )
    except Exception:
        return events
    return events


def _normalize_voice_llm_events(data: dict | None) -> list[dict]:
    try:
        if not isinstance(data, dict):
            return []
        raw_events = data.get("events")
        if isinstance(raw_events, dict):
            raw_events = [raw_events]
        if not isinstance(raw_events, list):
            return []

        events = []
        for item in raw_events:
            if not isinstance(item, dict):
                continue
            event_type = str(item.get("event_type") or "").strip().lower()
            if event_type not in {"taken", "missed", "symptom", "diet", "sleep", "activity", "mood", "other"}:
                continue
            events.append(
                {
                    "event_type": event_type,
                    "medication_name": item.get("medication_name"),
                    "time_of_day": item.get("time_of_day"),
                    "details": item.get("details") or item.get("description"),
                    "source": "llm_fallback",
                }
            )
        return events
    except Exception:
        return []


def _match_active_medication(patient_id: str, medication_name: str | None) -> dict | None:
    try:
        medications = db.get_active_medications_schedule(patient_id)
        if not medications:
            return None

        if medication_name:
            canonical, _ = _resolve_drug_name(medication_name)
            candidates = {medication_name.lower(), canonical.lower()}
            for medication in medications:
                drug_name = str(medication.get("drug_name") or "")
                if drug_name.lower() in candidates or any(candidate and candidate in drug_name.lower() for candidate in candidates):
                    return medication

        if len(medications) == 1:
            return medications[0]
    except Exception:
        return None
    return None


def _record_voice_event(patient_id: str, profile: dict | None, event: dict, transcript: str) -> tuple[bool, str | None]:
    try:
        profile_id = (profile or {}).get("id")
        event_type = str(event.get("event_type") or "").lower()
        details = str(event.get("details") or transcript)

        if event_type in {"taken", "missed"}:
            medication = _match_active_medication(patient_id, event.get("medication_name"))
            if not medication:
                return False, "medication_not_matched"
            db.log_medication_event(
                patient_id,
                str(medication["id"]),
                str(profile_id),
                "missed" if event_type == "missed" else "taken",
                details,
                source_type="voice",
            )
            return True, None

        if event_type in {"symptom", "diet", "sleep", "activity", "mood", "other"}:
            db.write_audit(
                patient_id,
                profile_id,
                f"voice_{event_type}_event",
                None,
                f"VOICE_{event_type.upper()}_EVENT_RECORDED",
                str((profile or {}).get("role") or "unknown"),
                {
                    "details": details,
                    "time_of_day": event.get("time_of_day"),
                    "transcript": transcript,
                    "source": event.get("source"),
                },
            )
            return True, None
    except Exception as error:
        return False, str(error)
    return False, "unsupported_event_type"


def _voice_symptom_needs_alert(event: dict) -> bool:
    try:
        if str(event.get("event_type") or "").lower() != "symptom":
            return False
        text = str(event.get("details") or "").lower()
        return any(
            word in text
            for word in [
                "severe", "bahut", "zyada", "chest pain", "seene mein dard",
                "saans nahi", "breathing", "unconscious", "behosh", "collapse",
                "heart attack", "emergency",
            ]
        )
    except Exception:
        return False


def _safe_audio_media_upload(
    task: dict,
    profile: dict | None,
    file_path: str,
    transcript: str,
    file_hash: str | None = None,
) -> str | None:
    return db.insert_media_upload(
        patient_id=task.get("patient_id"),
        profile_id=(profile or {}).get("id"),
        pending_task_id=task.get("id"),
        file_path=file_path,
        file_type="audio",
        parser_type="whisper",
        raw_text=transcript,
        structured_json={"file_hash": file_hash} if file_hash else None,
        parser_confidence=0.85,
        final_status="active",
    )


def _async_document_summary(result: dict, media_hint: str) -> dict:
    """
    Adapt AsyncDocumentPipeline output to the legacy media processor contract.
    """
    result = dict(result or {})
    extracted = result.get("extracted_data") if isinstance(result.get("extracted_data"), dict) else {}
    doc_type = result.get("document_type") or "unknown"
    success = result.get("status") == "success"
    error_message = result.get("error_message") or result.get("error")
    inserted_count = int(result.get("inserted_count") or 0)

    medications = extracted.get("medications") if isinstance(extracted.get("medications"), list) else []
    lab_values = extracted.get("lab_values") or extracted.get("tests") or []
    if isinstance(lab_values, dict):
        lab_values = [lab_values]
    if not isinstance(lab_values, list):
        lab_values = []
    events = extracted.get("events") if isinstance(extracted.get("events"), list) else []

    summary = {
        "success": success,
        "error_message": None if success else (error_message or "Document processing failed."),
        "document_type": doc_type,
        "media_hint": media_hint,
        "extracted_data": extracted,
        "validation_errors": result.get("validation_errors") or [],
        "needs_review": bool(result.get("needs_review")),
        "_review_required": bool(result.get("needs_review")),
        "review_reason": result.get("review_reason"),
        "processing_time_ms": result.get("processing_time_ms"),
        "inserted_count": inserted_count,
        "extraction_method": result.get("extraction_method"),
        "classification": result.get("classification"),
    }
    inserted_ids = result.get("inserted_ids") if isinstance(result.get("inserted_ids"), dict) else {}
    summary["media_upload_id"] = result.get("media_upload_id")
    summary["pharma_agent_triggered"] = bool(result.get("pharma_agent_triggered"))

    if doc_type == "prescription":
        summary["medications"] = medications
        summary["medications_added"] = inserted_ids.get("medication_ids") or []
        summary["medications_recorded"] = inserted_count
    elif doc_type == "lab_report":
        summary["lab_values"] = lab_values
        summary["tests_recorded"] = inserted_ids.get("lab_ids") or inserted_count
    elif doc_type == "voice_note":
        summary["events"] = events
        summary["events_recorded"] = inserted_count
    else:
        summary["events"] = events
        summary["events_recorded"] = inserted_count if events else 0

    return summary


def _finalize_async_document_task(task_id: str | None, result: dict, summary: dict) -> None:
    try:
        status = "done" if result.get("status") == "success" else "failed"
        db.update_pending_task_status(task_id, status, summary, summary.get("error_message"))
    except Exception:
        pass


def _run_prescription_async_post_processing(
    patient_id: str | None,
    from_phone: str | None,
    extracted_data: dict,
) -> bool:
    """
    Preserve prescription post-hooks after routing through AsyncDocumentPipeline.
    """
    triggered = False
    if not patient_id:
        return triggered
    try:
        medications = extracted_data.get("medications") if isinstance(extracted_data, dict) else []
        if isinstance(medications, dict):
            medications = [medications]
        if not isinstance(medications, list):
            medications = []

        for med in medications:
            if not isinstance(med, dict):
                continue
            drug_name = med.get("drug_name_canonical") or med.get("drug_name_raw") or med.get("drug_name")
            if not drug_name:
                continue
            _run_prescription_post_processing(
                str(patient_id),
                str(drug_name),
                med.get("duration"),
                from_phone,
            )
            triggered = True
    except Exception as error:
        print(f"Async prescription post-processing failed: {error}")
    return triggered


def process_voice_note(task: dict) -> dict:
    """
    Process a voice note task through the unified async document pipeline.
    """
    task_id = (task or {}).get("id")
    patient_id = (task or {}).get("patient_id")
    from_phone = (task or {}).get("from_phone")

    try:
        try:
            audio_bytes = _download_media(task["media_url"])
        except Exception:
            result = _voice_summary(False, error_message="Download failed. Please try again or resend the voice note.")
            db.update_pending_task_status(task_id, "failed", result, result["error_message"])
            return result

        import hashlib

        file_hash = hashlib.sha256(audio_bytes).hexdigest()
        if patient_id and db.is_duplicate_media(str(patient_id), file_hash):
            result = _voice_summary(False, error_message="Duplicate file already processed.")
            result["duplicate"] = True
            db.update_pending_task_status(task_id, "failed", result, result["error_message"])
            return result

        profile = db.get_profile_by_phone(from_phone) if from_phone else None
        profile_id = (profile or {}).get("id")

        pipeline = AsyncDocumentPipeline()
        result = pipeline.process(
            audio_bytes,
            "audio",
            str(patient_id or ""),
            profile_id,
            file_hash,
            pending_task_id=task_id,
            file_path=(task or {}).get("media_url"),
        )
        summary = _async_document_summary(result, "audio")
        summary["file_hash"] = file_hash
        if result.get("status") == "success" and patient_id:
            _refresh_crisis_cache(str(patient_id))
        _finalize_async_document_task(task_id, result, summary)
        return summary
    except Exception as error:
        message = str(error) or "Voice note processing failed."
        result = _voice_summary(False, error_message=message)
        db.update_pending_task_status(task_id, "failed", result, message)
        return result


# ---------------------------------------------------------------------------
# PDF lab report notes
# ---------------------------------------------------------------------------
# Audit/log review showed current live data mainly contains query/alert logs and
# recent labs such as blood_pressure_systolic/diastolic. For Indian lab PDFs, the
# first supported deterministic patterns focus on high-frequency terms:
# HbA1c/glycated hemoglobin, creatinine, glucose/sugar, hemoglobin/Hb, pulse,
# SpO2, and temperature. Reports often show values as:
#   TEST_NAME  VALUE UNIT  REFERENCE_LOW - REFERENCE_HIGH
# or:
#   TEST_NAME: VALUE UNIT (ref 4.0-5.6)
#
# Future improvements:
# - Add table-structure extraction for multi-column PDFs where test/value/unit
#   are split across columns.
# - Add lab-specific synonym dictionaries for Thyrocare, Dr Lal, Apollo, etc.
# - Add image orientation/deskew before OCR fallback.
# - Use patient baseline z-score once enough historical lab_reports exist.


def _normalize_lab_test_name(name: str) -> str:
    try:
        text = re.sub(r"[^a-zA-Z0-9% ]+", " ", str(name or "").lower())
        text = re.sub(r"\s+", " ", text).strip()
        aliases = {
            "hb": "hemoglobin",
            "haemoglobin": "hemoglobin",
            "hemoglobin": "hemoglobin",
            "glycated hemoglobin": "hba1c",
            "glycosylated hemoglobin": "hba1c",
            "hba1c": "hba1c",
            "hb a1c": "hba1c",
            "triiodothyronine t3 free": "free_t3",
            "triiodothyronine free": "free_t3",
            "t3 free": "free_t3",
            "free t3": "free_t3",
            "thyroxine t4 free": "free_t4",
            "thyroxine free": "free_t4",
            "t4 free": "free_t4",
            "free t4": "free_t4",
            "thyroid stimulating hormone": "tsh",
            "thyroid stimulating hormone tsh": "tsh",
            "tsh": "tsh",
            "creatinine": "creatinine",
            "serum creatinine": "creatinine",
            "glucose": "glucose",
            "blood glucose": "glucose",
            "blood sugar": "glucose",
            "fasting glucose": "glucose",
            "fasting blood sugar": "glucose",
            "random glucose": "glucose",
            "random blood sugar": "glucose",
            "pulse": "pulse",
            "spo2": "spo2",
            "oxygen saturation": "spo2",
            "temperature": "temperature",
            "temp": "temperature",
            "systolic bp": "blood_pressure_systolic",
            "blood pressure systolic": "blood_pressure_systolic",
            "diastolic bp": "blood_pressure_diastolic",
            "blood pressure diastolic": "blood_pressure_diastolic",
        }
        return aliases.get(text, text.replace(" ", "_"))
    except Exception:
        return str(name or "").lower().replace(" ", "_")


def _lab_name_regex() -> str:
    return (
        r"Triiodothyronine\s*(?:\(?T3\)?)?,?\s*Free|Thyroxine\s*(?:\(?T4\)?)?,?\s*Free|"
        r"Thyroid Stimulating Hormone(?:\s*\(TSH\))?|\bTSH\b|\bT3\b|\bT4\b|"
        r"HbA1c|Hb A1c|Glycated Hemoglobin|Glycosylated Hemoglobin|"
        r"Serum Creatinine|Creatinine|Hemoglobin|Haemoglobin|\bHb\b|"
        r"Fasting Blood Sugar|Random Blood Sugar|Blood Sugar|Blood Glucose|"
        r"Fasting Glucose|Random Glucose|Glucose|SpO2|Oxygen Saturation|"
        r"Temperature|Temp|Pulse|Systolic BP|Diastolic BP"
    )


def _extract_reference_range(fragment: str) -> tuple[float | None, float | None]:
    try:
        range_patterns = [
            r"(?:ref(?:erence)?(?:\s*range)?|normal)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)",
            r"\(?\s*(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s*\)?",
        ]
        for pattern in range_patterns:
            match = re.search(pattern, fragment, flags=re.IGNORECASE)
            if match:
                return float(match.group(1)), float(match.group(2))
    except Exception:
        pass
    return None, None


def _extract_lab_tests_deterministic(text: str) -> list[dict]:
    """
    Extract known lab rows from text with conservative regex rules.
    """
    tests = []
    try:
        if not text:
            return []

        text = _clean_extracted_text(text)
        name_pattern = _lab_name_regex()
        row_pattern = re.compile(
            rf"({name_pattern})\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*([a-zA-Z/%µ°]+)?([^\n\r]{{0,80}})",
            flags=re.IGNORECASE,
        )
        seen = set()
        for match in row_pattern.finditer(text):
            raw_name = match.group(1)
            value = _to_float(match.group(2))
            if value is None:
                continue

            unit = (match.group(3) or "").strip()
            tail = match.group(4) or ""
            reference_low, reference_high = _extract_reference_range(tail)
            test_name = _normalize_lab_test_name(raw_name)
            key = (test_name, value, unit)
            if key in seen:
                continue
            seen.add(key)
            tests.append(
                {
                    "test_name": test_name,
                    "value": value,
                    "unit": unit,
                    "reference_range_low": reference_low,
                    "reference_range_high": reference_high,
                    "flag": None,
                    "source": "deterministic",
                }
            )
    except Exception:
        return tests
    return tests


def _lab_llm_prompt() -> str:
    return (
        "Extract all lab report test results as JSON. Return exactly: "
        "{\"tests\":[{\"test_name\":string,\"value\":number,\"unit\":string|null,"
        "\"reference_range_low\":number|null,\"reference_range_high\":number|null,"
        "\"flag\":string|null}]}. Use null for missing fields. "
        "Only include values visibly present in the text."
    )


def _normalize_llm_lab_tests(data: dict | None, raw_text: str) -> list[dict]:
    try:
        if not isinstance(data, dict):
            return []
        raw_tests = data.get("tests")
        if isinstance(raw_tests, dict):
            raw_tests = [raw_tests]
        if not isinstance(raw_tests, list):
            return []

        tests = []
        for item in raw_tests:
            if not isinstance(item, dict):
                continue
            value = _to_float(item.get("value"))
            if value is None or not _numeric_value_allowed(value, raw_text):
                continue
            ref_low = _to_float(item.get("reference_range_low"))
            ref_high = _to_float(item.get("reference_range_high"))
            tests.append(
                {
                    "test_name": _normalize_lab_test_name(str(item.get("test_name") or "")),
                    "value": value,
                    "unit": item.get("unit"),
                    "reference_range_low": ref_low,
                    "reference_range_high": ref_high,
                    "flag": item.get("flag"),
                    "source": "llm_fallback",
                }
            )
        return [test for test in tests if test.get("test_name")]
    except Exception:
        return []


def _extract_pdf_via_ocr(file_bytes: bytes) -> tuple[str, float]:
    try:
        from pdf2image import convert_from_bytes

        images = convert_from_bytes(file_bytes, dpi=config.PDF_DPI)
        page_texts = []
        page_confidences = []
        for image in images:
            img_bytes = io.BytesIO()
            image.save(img_bytes, format="PNG")
            page_text, page_conf = _run_paddle_ocr(img_bytes.getvalue())
            if page_text:
                page_texts.append(page_text)
            if page_conf:
                page_confidences.append(page_conf)
        text = "\n".join(page_texts).strip()
        if not text:
            return "", 0.0
        confidence = sum(page_confidences) / len(page_confidences) if page_confidences else 0.8
        return _clean_extracted_text(text), min(float(confidence), 0.8)
    except Exception:
        return "", 0.0


def _physical_limit_ok(test_name: str, value: float) -> bool:
    try:
        limits = config.LAB_PHYSICAL_LIMITS.get(test_name)
        if not limits:
            return True
        low, high = limits
        return float(low) <= float(value) <= float(high)
    except Exception:
        return False


def _is_abnormal_by_reference(value: float, low, high) -> bool:
    try:
        if low is not None and float(value) < float(low):
            return True
        if high is not None and float(value) > float(high):
            return True
    except Exception:
        return False
    return False


def _has_suspicious_lab_jump(patient_id: str, test_name: str, value: float) -> bool:
    try:
        previous = db.get_last_lab_value(patient_id, test_name)
        if not previous:
            return False
        old = _to_float(previous.get("test_value"))
        new = float(value)
        if old is None or old <= 0 or new <= 0:
            return False
        jump = max(new / old, old / new)
        return jump > config.LAB_VALUE_JUMP_MULTIPLIER
    except Exception:
        return False


def _safe_pdf_media_upload(
    task: dict,
    profile: dict | None,
    file_path: str,
    parser_type: str,
    raw_text: str,
    confidence: float,
    file_hash: str | None = None,
) -> str | None:
    return db.insert_media_upload(
        patient_id=task.get("patient_id"),
        profile_id=(profile or {}).get("id"),
        pending_task_id=task.get("id"),
        file_path=file_path,
        file_type="pdf",
        parser_type=parser_type,
        raw_text=raw_text,
        structured_json={"file_hash": file_hash} if file_hash else None,
        parser_confidence=confidence,
        final_status="active",
    )


def _refresh_crisis_cache(patient_id: str) -> None:
    try:
        import crisis

        patient_name = db.get_patient_name(patient_id) or "Patient"
        if hasattr(crisis, "build_crisis_card"):
            card = crisis.build_crisis_card(patient_id, patient_name)
            db.upsert_crisis_cache(patient_id, card)
        elif hasattr(crisis, "get_emergency_packet"):
            crisis.get_emergency_packet(patient_id)
    except Exception:
        pass


def process_pdf_report(task: dict) -> dict:
    """
    Process a PDF report through the unified async document pipeline.
    """
    task_id = (task or {}).get("id")
    patient_id = (task or {}).get("patient_id")
    from_phone = (task or {}).get("from_phone")

    try:
        try:
            file_bytes = _download_media(task["media_url"])
        except Exception:
            result = {
                "success": False,
                "tests_recorded": 0,
                "alerts_created": 0,
                "error_message": "Download failed. Please try again or resend the PDF.",
            }
            db.update_pending_task_status(task_id, "failed", result, result["error_message"])
            return result

        import hashlib

        file_hash = hashlib.sha256(file_bytes).hexdigest()
        if patient_id and db.is_duplicate_media(str(patient_id), file_hash):
            result = {
                "success": False,
                "tests_recorded": 0,
                "alerts_created": 0,
                "error_message": "Duplicate file already processed.",
                "duplicate": True,
            }
            db.update_pending_task_status(task_id, "failed", result, result["error_message"])
            return result

        profile = db.get_profile_by_phone(from_phone) if from_phone else None
        profile_id = (profile or {}).get("id")

        pipeline = AsyncDocumentPipeline()
        result = pipeline.process(
            file_bytes,
            "pdf",
            str(patient_id or ""),
            profile_id,
            file_hash,
            pending_task_id=task_id,
            file_path=(task or {}).get("media_url"),
        )
        summary = _async_document_summary(result, "pdf")
        summary["file_hash"] = file_hash
        if result.get("status") == "success" and patient_id:
            _refresh_crisis_cache(str(patient_id))
        _finalize_async_document_task(task_id, result, summary)
        return summary
    except Exception as error:
        message = str(error) or "PDF report processing failed."
        result = {
            "success": False,
            "tests_recorded": 0,
            "alerts_created": 0,
            "error_message": message,
        }
        db.update_pending_task_status(task_id, "failed", result, message)
        return result


def process_prescription_photo(task: dict) -> dict:
    """
    Process a prescription photo through the unified async document pipeline.
    """
    task_id = (task or {}).get("id")
    patient_id = (task or {}).get("patient_id")
    from_phone = (task or {}).get("from_phone")

    try:
        try:
            image_bytes = _download_media(task["media_url"])
        except Exception:
            return _fail_task(task_id, "Download failed. Please try again or resend the photo.")

        import hashlib

        file_hash = hashlib.sha256(image_bytes).hexdigest()
        if patient_id and db.is_duplicate_media(str(patient_id), file_hash):
            result = _summary(False, error_message="Duplicate file already processed.", duplicate=True)
            db.update_pending_task_status(task_id, "failed", result, result["error_message"])
            return result

        profile = db.get_profile_by_phone(from_phone) if from_phone else None
        profile_id = (profile or {}).get("id")

        pipeline = AsyncDocumentPipeline()
        result = pipeline.process(
            image_bytes,
            "image",
            str(patient_id or ""),
            profile_id,
            file_hash,
            pending_task_id=task_id,
            file_path=(task or {}).get("media_url"),
        )
        summary = _async_document_summary(result, "image")
        summary["file_hash"] = file_hash

        if result.get("status") == "success" and patient_id:
            extracted_data = result.get("extracted_data") if isinstance(result.get("extracted_data"), dict) else {}
            if result.get("pharma_agent_triggered"):
                summary["pharma_agent_triggered"] = True
            else:
                summary["pharma_agent_triggered"] = _run_prescription_async_post_processing(
                    str(patient_id),
                    from_phone,
                    extracted_data,
                )
            _refresh_crisis_cache(str(patient_id))

        _finalize_async_document_task(task_id, result, summary)
        return summary
    except Exception as error:
        message = str(error) or "Prescription photo processing failed."
        return _fail_task(task_id, message)
