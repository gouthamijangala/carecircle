"""
Embedding-backed document type classifier for parsed media text.

The classifier compares raw parser text against configurable anchor phrases and
falls back to a keyword score when embedding confidence is below the configured
threshold. It is intentionally fail-closed: callers receive "unknown" on any
load or runtime failure.
"""

from __future__ import annotations

import threading

import config

try:
    from sentence_transformers import SentenceTransformer, util
except Exception:
    SentenceTransformer = None
    util = None


_MODEL = None
_ANCHORS = None
_MODEL_LOCK = threading.Lock()


class DocumentTypeClassifier:
    """
    Contract:
      Input: raw_text (str)
      Output: dict {"document_type": str, "confidence": float, "method": str}
      Error: Returns "unknown" with 0.0 confidence on failure.
    """

    def __init__(self):
        self.model = None
        self._anchors = None

    def _load(self):
        global _MODEL, _ANCHORS
        if self.model and self._anchors:
            return
        if SentenceTransformer is None or util is None:
            raise RuntimeError("sentence_transformers is not installed")
        with _MODEL_LOCK:
            if _MODEL is None or _ANCHORS is None:
                _MODEL = SentenceTransformer(config.DOC_TYPE_EMBEDDING_MODEL)
                _ANCHORS = {
                    key: _MODEL.encode(anchor_text)
                    for key, anchor_text in config.DOC_TYPE_ANCHORS.items()
                }
            self.model = _MODEL
            self._anchors = _ANCHORS

    def classify(self, raw_text: str) -> dict:
        try:
            self._load()
            if not raw_text or not raw_text.strip():
                return {"document_type": "unknown", "confidence": 0.0, "method": "empty"}

            text_slice = raw_text[: config.MAX_OCR_CONTEXT_TOKENS]
            emb = self.model.encode(text_slice)
            scores = {
                key: util.cos_sim(emb, anchor_embedding).item()
                for key, anchor_embedding in self._anchors.items()
            }
            best = max(scores, key=scores.get)
            confidence = float(scores[best])
            method = "embedding"

            if confidence < config.DOC_TYPE_CONFIDENCE_THRESHOLD:
                method = "keyword"
                best, confidence = self._keyword_classify(raw_text)

            return {
                "document_type": best if confidence > 0.1 else "unknown",
                "confidence": round(float(confidence), 2),
                "method": method,
            }
        except Exception:
            best, confidence = self._keyword_classify(raw_text or "")
            return {
                "document_type": best if confidence > 0.1 else "unknown",
                "confidence": round(float(confidence), 2),
                "method": "keyword_fallback",
            }

    def _keyword_classify(self, raw_text: str) -> tuple[str, float]:
        text = raw_text.lower()
        scores = {}
        for key, anchor_text in config.DOC_TYPE_ANCHORS.items():
            words = {word.lower() for word in anchor_text.split() if word.strip()}
            words.update(self._keyword_aliases(key))
            matches = sum(1 for word in words if word in text)
            scores[key] = matches / max(3, min(len(words), 8))

        best = max(scores, key=scores.get)
        return best, float(scores[best])

    def _keyword_aliases(self, document_type: str) -> set[str]:
        if document_type == "prescription":
            return {"tab", "tab.", "cap", "cap.", "rx", "od", "bd", "tds", "mg", "1-0-0"}
        if document_type == "lab_report":
            return {"hb", "cbc", "rbc", "sgpt", "sgot", "hdl", "ldl", "triglycerides"}
        if document_type == "discharge_summary":
            return {"discharge", "discharged", "hospital", "follow up", "follow-up"}
        if document_type == "referral_letter":
            return {"refer", "referred", "consultation"}
        return set()
