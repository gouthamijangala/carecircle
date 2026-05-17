# Known Issues

Date: 2026-05-18

This file records known non-blocking issues before the codebase restructuring phases begin.

## Repository

- The workspace is now initialized as a Git repository on branch `main`, but no baseline commit has been created yet.
- `docs/` did not exist before Phase 0 and was created for baseline documentation.

## Runtime Commands

- `run_carecircle_server.bat` now prefers the project virtual environment and falls back to `python` if the virtual environment is missing.

## Verification Noise

- `verify_document_pipeline.py` logs expected PDF parser fallback errors for fake PDF bytes:
  - `PDF OCR fallback failed: Failed to load document`
  - `PDF extraction failed: pdfplumber: No /Root object`
- These messages are currently non-blocking because the verification script passes.

## Restructure Risks

- The project is currently a flat module layout with many imports such as `import db`, `import config`, and `from ingestion import ...`.
- Large root modules such as `db.py`, `ingestion.py`, `pharma_agent.py`, and `main.py` should not be moved until compatibility wrappers and verification coverage are in place.
