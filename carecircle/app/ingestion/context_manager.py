"""
Context preservation helpers for medical document extraction.

The goal is to reduce long parser output before LLM extraction while keeping
clinically relevant rows such as medications, lab values, patient identifiers,
dates, discharge notes, and referral language.
"""

from __future__ import annotations


class MedicalContextManager:
    """
    Contract:
      Input: raw_text (str), max_tokens (int), doc_type (str)
      Output: tuple (preserved_text: str, was_truncated: bool)
      Error: Returns original text if truncation logic fails.
    """

    @staticmethod
    def truncate_with_preservation(text: str, max_tokens: int, doc_type: str) -> tuple:
        try:
            if not text or max_tokens <= 0:
                return text, False

            lines = text.split("\n")
            critical_keywords = MedicalContextManager._critical_keywords(doc_type)

            critical_lines = [
                line
                for line in lines
                if any(keyword in line.lower() for keyword in critical_keywords)
            ]
            non_critical_lines = [
                line
                for line in lines
                if not any(keyword in line.lower() for keyword in critical_keywords)
            ]

            critical_text = "\n".join(critical_lines)
            critical_tokens = len(critical_text.split())
            original_tokens = len(text.split())

            if critical_tokens >= max_tokens:
                return critical_text[: max_tokens * 5], True

            remaining = max_tokens - critical_tokens
            non_critical_words = " ".join(non_critical_lines).split()
            non_critical_text = " ".join(non_critical_words[:remaining])
            combined = f"{critical_text}\n{non_critical_text}".strip()
            was_truncated = original_tokens > len(combined.split())

            return combined, was_truncated
        except Exception:
            return text, False

    @staticmethod
    def _critical_keywords(doc_type: str) -> list[str]:
        base_keywords = [
            "tab",
            "cap",
            "inj",
            "mg/dl",
            "g/dl",
            "mmol",
            "patient",
            "name:",
            "date:",
            "test",
            "result",
            "discharge",
            "referral",
        ]

        if doc_type == "prescription":
            return base_keywords + ["rx", "dose", "od", "bd", "tds", "after food", "before food"]
        if doc_type == "lab_report":
            return base_keywords + ["reference", "range", "high", "low", "creatinine", "hba1c"]
        if doc_type == "discharge_summary":
            return base_keywords + ["diagnosis", "follow-up", "follow up", "hospital", "admitted"]
        return base_keywords

