# Operations

## Local Server

```powershell
.\venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

## Baseline Checks

```powershell
.\venv\Scripts\python.exe -m py_compile main.py db.py ingestion.py handlers.py
.\venv\Scripts\python.exe verify_appointment_workflow.py
.\venv\Scripts\python.exe verify_layers_4_6.py
.\venv\Scripts\python.exe verify_document_pipeline.py
.\venv\Scripts\python.exe verify_pharma_agent.py
```

## Health Endpoints

- `/health`
- `/health/pipeline`
- `/health/pharma`

## Notes

- Heavy OCR/embedding dependencies should be installed outside the server reload process.
- Keep `.env` local and use `.env.example` for shared documentation.

