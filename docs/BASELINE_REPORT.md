# Phase 0 Baseline Report

Date: 2026-05-18

## Purpose

This report captures the current working baseline before repository restructuring. Future phases should compare against this file before and after moving code, scripts, documentation, or configuration.

## Repository State

- Workspace: `C:\Users\DELL\Documents\100x-Project`
- Git status: initialized as a Git repository on branch `main`.
- Python runtime: `Python 3.11.9`
- Active interpreter: `C:\Users\DELL\Documents\100x-Project\venv\Scripts\python.exe`
- Current app entrypoint: `main:app`
- Existing server batch file: `run_carecircle_server.bat`

## Current Server Commands

Preferred local development command:

```powershell
.\venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Existing batch command:

```bat
"C:\Users\DELL\AppData\Local\Programs\Python\Python311\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8000 --log-level info
```

Note: the batch file now prefers the project virtual environment and falls back to `python` if the virtual environment is missing.

## Verification Commands

The following commands were run as the Phase 0 baseline:

```powershell
.\venv\Scripts\python.exe -m py_compile main.py db.py ingestion.py handlers.py
.\venv\Scripts\python.exe verify_appointment_workflow.py
.\venv\Scripts\python.exe verify_layers_4_6.py
.\venv\Scripts\python.exe verify_document_pipeline.py
.\venv\Scripts\python.exe verify_pharma_agent.py
```

## Results

| Check | Result | Notes |
| --- | --- | --- |
| Core Python compile | PASS | `main.py`, `db.py`, `ingestion.py`, and `handlers.py` compiled successfully. |
| Appointment workflow | PASS | Intent routing, DB fetch, filtering, confirmation context, draft completion, specialty mapping, and reminder fetch passed. |
| Layers 4-6 | PASS | Scheduler config, DB setup/input/output, brief templates, and research gates passed. |
| Document pipeline | PASS | Extractor, classifier, context manager, prompts, validators, and pipeline orchestrator passed. |
| PharmaAgent | PASS | End-to-end verification script passed. |

## Non-Blocking Observations

- `verify_document_pipeline.py` intentionally tests invalid fake PDF bytes and logs PDF parser fallback errors before passing.
- Loading embedding/model weights can print progress output during verification.
- This workspace is initialized as a Git repository; create commits before risky restructuring work begins.
