# Contributing

Thanks for helping improve CareCircle. This repository is being migrated from a flat local app into a cleaner package layout, so changes should be small, verified, and backwards-compatible.

## Local Setup

```powershell
.\venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Use `.env.example` as the configuration template. Never commit `.env` or real API keys.

## Before Opening A PR

Run the baseline checks:

```powershell
.\venv\Scripts\python.exe scripts\verify\run_baseline.py
```

At minimum, run:

```powershell
.\venv\Scripts\python.exe scripts\verify\verify_structure_imports.py
.\venv\Scripts\python.exe -m py_compile main.py db.py ingestion.py handlers.py
```

## Restructure Rules

- Keep root compatibility imports working until migration is complete.
- Do not move `db.py`, `ingestion.py`, `main.py`, `handlers.py`, or `pharma_agent.py` without compatibility wrappers and passing verification.
- Keep SQL access centralized through `db.py` or the package DB facade.
- Avoid committing generated logs, local model files, uploaded media, or secrets.

