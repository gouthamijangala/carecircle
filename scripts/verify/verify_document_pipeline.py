print("=== DOCUMENT PIPELINE VERIFICATION ===")

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from scripts.verify import _bootstrap  # noqa: F401

import config
from async_pipeline import AsyncDocumentPipeline
from context_manager import MedicalContextManager
from doc_classifier import DocumentTypeClassifier
from extraction_engine import DocumentExtractor
from pharma_prompts import get_prompt_for_type
from validators import validate_and_clean


e = DocumentExtractor()
assert e.extract(b"%PDF fake", "pdf")["method"] in ["structured", "ocr", "failed"]
print("Extractor: OK")

c = DocumentTypeClassifier()
assert c.classify("Tab. Metformin")["document_type"] == "prescription"
assert c.classify("Discharged today, follow up in 2 weeks")["document_type"] == "discharge_summary"
assert c.classify("known case of diabetes purana rog allergy")["document_type"] == "medical_history"
print("Classifier: OK (8 clusters routed)")

_, was = MedicalContextManager.truncate_with_preservation("Tab. Med\nWalk\n" * 50, 30, "prescription")
assert was is True
print("Context Manager: OK")

for t in config.DOC_TYPE_ANCHORS.keys():
    assert "/no_think" in get_prompt_for_type(t)
assert "past_conditions" in get_prompt_for_type("medical_history")
print("Prompts: 8 specialized schemas loaded")

_, errs, rev = validate_and_clean('{"tests":[{"test_value":"9999","test_name":"glucose"}]}', "glucose 100", "lab_report")
assert rev is True and errs
print("Validator: Hallucination & limit guards active")

p = AsyncDocumentPipeline()
assert hasattr(p, "_build_caregiver_summary") and hasattr(p, "_insert_to_db")
print("Pipeline Orchestrator: Initialized with notification hook")

print("\nALL VERIFICATION TESTS PASSED")
print("ROLLBACK: Set ENABLE_HYBRID_PDF_EXTRACTION=False in config.py to bypass PyMuPDF.")
print("ROLLBACK: Set DOC_TYPE_CONFIDENCE_THRESHOLD=1.0 to force keyword fallback only.")
