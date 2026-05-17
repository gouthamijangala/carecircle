"""
Unified document extraction helpers for media ingestion.

The extractor accepts bytes plus a media hint and returns plain text, optional
layout objects, the extraction method, and a character count. Parser failures
are intentionally converted to empty results so background workers can mark the
task failed or route to fallback logic without crashing the server.
"""

from __future__ import annotations

import io
import logging
import os
import tempfile
import threading
from typing import Any

import config
from PIL import Image

try:
    from paddleocr import PaddleOCR
except Exception:
    PaddleOCR = None

try:
    import whisper
except Exception:
    whisper = None

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None

try:
    import pypdfium2 as pdfium
except Exception:
    pdfium = None

try:
    import pdfplumber
except Exception:
    pdfplumber = None

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None

try:
    from paddleocr import PPStructure
except Exception:  # PaddleOCR 3.x may not expose PPStructure from the package root.
    PPStructure = None


for noisy_logger in ("pdfminer", "pdfplumber", "pypdf"):
    logging.getLogger(noisy_logger).setLevel(logging.ERROR)


_OCR_MODEL = None
_ASR_MODEL = None
_STRUCTURE_MODEL = None
_MODEL_LOCK = threading.Lock()


class DocumentExtractor:
    """
    Contract:
      Input: file_bytes (bytes), media_hint ("image"|"pdf"|"audio")
      Output: dict {"text": str, "layout": list[dict], "method": str, "char_count": int}
      Error: Returns empty text on failure, never raises.
    """

    def __init__(self):
        self.ocr = None
        self.structure = None
        self.asr = None

    def extract(self, file_bytes: bytes, media_hint: str = "image") -> dict:
        try:
            if media_hint == "audio":
                return self._extract_audio(file_bytes)
            if media_hint == "pdf":
                return self._extract_pdf(file_bytes)
            return self._extract_image(file_bytes)
        except Exception as error:
            print(f"Extraction failed: {error}")
            return self._empty_result("failed")

    def _extract_audio(self, audio_bytes: bytes) -> dict:
        asr = self._get_asr()
        if asr is None:
            return self._empty_result("failed")

        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp.flush()
                temp_path = tmp.name

            result = asr.transcribe(temp_path)

            text = self._clean_text(result.get("text"))
            return {"text": text, "layout": [], "method": "asr", "char_count": len(text)}
        except Exception as error:
            print(f"Audio extraction failed: {error}")
            return self._empty_result("failed")
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass

    def _extract_pdf(self, pdf_bytes: bytes) -> dict:
        extractors = (
            ("pymupdf", self._extract_pdf_with_fitz),
            ("pdfplumber", self._extract_pdf_with_pdfplumber),
            ("pypdf", self._extract_pdf_with_pypdf),
        )

        best = self._empty_result("failed")
        errors: list[str] = []
        for method, extractor in extractors:
            try:
                text = self._clean_text(extractor(pdf_bytes))
                if len(text) > best["char_count"]:
                    best = {"text": text, "layout": [], "method": method, "char_count": len(text)}
                if len(text) >= config.PDF_TEXT_DENSITY_THRESHOLD:
                    return best
            except Exception as error:
                errors.append(f"{method}: {error}")

        ocr_result = self._pdf_ocr_fallback(pdf_bytes)
        if ocr_result["text"]:
            return ocr_result

        if best["text"]:
            return best
        if errors:
            print("PDF extraction failed: " + " | ".join(errors))
        return self._empty_result("failed")

    def _extract_pdf_with_fitz(self, pdf_bytes: bytes) -> str:
        if fitz is None:
            return ""
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            return "\n".join(page.get_text() or "" for page in doc).strip()

    def _extract_pdf_with_pdfplumber(self, pdf_bytes: bytes) -> str:
        if pdfplumber is None:
            return ""
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(pdf_bytes)
                tmp.flush()
                temp_path = tmp.name
            with pdfplumber.open(temp_path) as pdf:
                return "\n".join(page.extract_text() or "" for page in pdf.pages).strip()
        finally:
            if temp_path:
                self._cleanup_temp_path(temp_path)

    def _extract_pdf_with_pypdf(self, pdf_bytes: bytes) -> str:
        if PdfReader is None:
            return ""
        reader = PdfReader(io.BytesIO(pdf_bytes))
        return "\n".join(page.extract_text() or "" for page in reader.pages).strip()

    def _pdf_ocr_fallback(self, pdf_bytes: bytes) -> dict:
        if fitz is None and pdfium is None:
            return self._empty_result("failed")

        pages_text: list[str] = []
        layout: list[dict] = []

        try:
            for page_number, image in enumerate(self._render_pdf_pages(pdf_bytes), start=1):
                page_result = self._ocr_image(image)
                if page_result["text"]:
                    pages_text.append(page_result["text"])
                for item in page_result["layout"]:
                    item["page"] = page_number
                    layout.append(item)

            text = self._clean_text("\n".join(pages_text))
            return {"text": text, "layout": layout, "method": "ocr", "char_count": len(text)}
        except Exception as error:
            print(f"PDF OCR fallback failed: {error}")
            return self._empty_result("failed")

    def _render_pdf_pages(self, pdf_bytes: bytes) -> list[Image.Image]:
        """
        Render scanned/image PDFs for OCR.
        Prefer PyMuPDF when installed; otherwise use pypdfium2, which is already
        present via pdfplumber and does not require Poppler.
        """
        scale = config.PDF_OCR_FALLBACK_DPI / 72
        if fitz is not None:
            images: list[Image.Image] = []
            with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
                for page in doc:
                    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
                    images.append(Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB"))
            return images

        if pdfium is None:
            return []
        document = pdfium.PdfDocument(pdf_bytes)
        try:
            images = []
            for index in range(len(document)):
                bitmap = document[index].render(scale=scale)
                images.append(bitmap.to_pil().convert("RGB"))
            return images
        finally:
            try:
                document.close()
            except Exception:
                pass

    def _extract_image(self, img_bytes: bytes) -> dict:
        try:
            image = Image.open(io.BytesIO(img_bytes))
            result = self._ocr_image(image)
            method = "ocr" if result["text"] else "failed"
            return {
                "text": result["text"],
                "layout": result["layout"],
                "method": method,
                "char_count": len(result["text"]),
            }
        except Exception as error:
            print(f"Image extraction failed: {error}")
            return self._empty_result("failed")

    def _ocr_image(self, image: Image.Image) -> dict:
        ocr = self._get_ocr()
        if ocr is None:
            return {"text": "", "layout": []}

        try:
            result = self._call_ocr(ocr, image)
            text, layout = self._parse_ocr_result(result)
            return {"text": text, "layout": layout}
        except Exception as error:
            print(f"OCR failed: {error}")
            return {"text": "", "layout": []}

    def _call_ocr(self, ocr, image: Image.Image) -> Any:
        prepared = image.convert("RGB")
        if hasattr(ocr, "predict"):
            image_input, temp_path = self._ocr_temp_file(prepared)
            try:
                return ocr.predict(image_input)
            finally:
                self._cleanup_temp_path(temp_path)
        image_input, temp_path = self._ocr_input(prepared)
        try:
            return ocr.ocr(image_input, cls=True)
        finally:
            self._cleanup_temp_path(temp_path)

    def _parse_ocr_result(self, result: Any) -> tuple[str, list[dict]]:
        text_parts: list[str] = []
        layout: list[dict] = []

        if result is None:
            return "", []

        for block in result:
            if isinstance(block, dict):
                texts = self._first_present(block, ("rec_texts", "texts")) or []
                scores = self._first_present(block, ("rec_scores", "scores")) or []
                boxes = self._first_present(block, ("rec_boxes", "dt_polys", "boxes"))
                if boxes is None:
                    boxes = []
                for index, text in enumerate(texts):
                    if not text:
                        continue
                    text_value = self._clean_text(text)
                    text_parts.append(text_value)
                    layout.append(
                        {
                            "text": text_value,
                            "confidence": self._safe_float(scores[index] if index < len(scores) else None),
                            "box": self._json_safe(boxes[index] if index < len(boxes) else None),
                        }
                    )
                continue

            lines = block if isinstance(block, list) else []
            for line in lines:
                parsed = self._parse_ocr_line(line)
                if not parsed:
                    continue
                text_parts.append(parsed["text"])
                layout.append(parsed)

        return self._clean_text("\n".join(text_parts)), layout

    def _parse_ocr_line(self, line: Any) -> dict | None:
        try:
            box, payload = line
            text, confidence = payload
            text_value = self._clean_text(text)
            if not text_value:
                return None
            return {
                "text": text_value,
                "confidence": self._safe_float(confidence),
                "box": self._json_safe(box),
            }
        except Exception:
            return None

    def _first_present(self, block: dict, keys: tuple[str, ...]):
        for key in keys:
            if key in block and block.get(key) is not None:
                return block.get(key)
        return None

    def _init_ocr(self):
        try:
            if PaddleOCR is None:
                return None
            try:
                return PaddleOCR(
                    lang="en",
                    ocr_version="PP-OCRv5",
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                )
            except Exception:
                return PaddleOCR(lang="en")
        except Exception as error:
            print(f"OCR model initialization failed: {error}")
            return None

    def _init_structure(self):
        if PPStructure is None:
            return None
        try:
            return PPStructure(table=True, ocr=True)
        except Exception as error:
            print(f"PPStructure initialization failed: {error}")
            return None

    def _init_asr(self):
        try:
            if whisper is None:
                return None
            return whisper.load_model(config.WHISPER_MODEL, device=config.WHISPER_DEVICE)
        except Exception as error:
            print(f"Whisper model initialization failed: {error}")
            return None

    def _get_ocr(self):
        global _OCR_MODEL
        if self.ocr is None:
            with _MODEL_LOCK:
                if _OCR_MODEL is None:
                    _OCR_MODEL = self._init_ocr()
                self.ocr = _OCR_MODEL
        return self.ocr

    def _get_structure(self):
        global _STRUCTURE_MODEL
        if self.structure is None and config.ENABLE_HYBRID_PDF_EXTRACTION:
            with _MODEL_LOCK:
                if _STRUCTURE_MODEL is None:
                    _STRUCTURE_MODEL = self._init_structure()
                self.structure = _STRUCTURE_MODEL
        return self.structure

    def _get_asr(self):
        global _ASR_MODEL
        if self.asr is None:
            with _MODEL_LOCK:
                if _ASR_MODEL is None:
                    _ASR_MODEL = self._init_asr()
                self.asr = _ASR_MODEL
        return self.asr

    def _ocr_input(self, image: Image.Image):
        try:
            import numpy as np

            return np.asarray(image), None
        except Exception:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                temp_path = tmp.name
            image.save(temp_path, format="PNG")
            return temp_path, temp_path

    def _ocr_temp_file(self, image: Image.Image) -> tuple[str, str]:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            temp_path = tmp.name
        image.save(temp_path, format="PNG")
        return temp_path, temp_path

    def _cleanup_temp_path(self, temp_path: str | None) -> None:
        if not temp_path:
            return
        try:
            os.unlink(temp_path)
        except Exception:
            pass

    def _empty_result(self, method: str) -> dict:
        return {"text": "", "layout": [], "method": method, "char_count": 0}

    def _clean_text(self, value: Any) -> str:
        text = str(value or "")
        if "\x00" in text:
            text = text.replace("\x00", "")
        return text.strip()

    def _safe_float(self, value: Any) -> float | None:
        try:
            return float(value)
        except Exception:
            return None

    def _json_safe(self, value: Any) -> Any:
        if value is None:
            return None
        if hasattr(value, "tolist"):
            return value.tolist()
        if isinstance(value, tuple):
            return [self._json_safe(item) for item in value]
        if isinstance(value, list):
            return [self._json_safe(item) for item in value]
        return value
