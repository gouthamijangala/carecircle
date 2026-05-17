# Media Ingestion Pipeline — Verification & Rollback

This document validates the CareCircle media ingestion pipeline from local helper
functions through background orchestration and `/api/send` media ACK behavior.

## Prerequisites

- Install dependencies:

```powershell
python -m pip install paddleocr paddlepaddle==3.2.2 "numpy<2.4,>=1.24" openai-whisper pdfplumber pdf2image pillow openai requests
```

Windows compatibility note: `paddlepaddle==3.3.1` can trigger a PaddleOCR runtime
`ConvertPirAttribute2RuntimeAttribute` error on this project’s Windows CPU setup.
Use `paddlepaddle==3.2.2` with `numpy<2.4`.

- Download/install Poppler for PDF-to-image support:

https://github.com/oschwartz10612/poppler-windows/releases/

- Verify Poppler is on `PATH`:

```powershell
pdftoppm -h
```

- Ensure LM Studio is running at `http://192.168.1.93:1234/v1` with `qwen/qwen3-4b` loaded.

- Ensure the media ingestion SQL migration has been applied in Supabase:
  `drug_formulary`, `media_uploads`, and added columns on `pending_tasks`, `medications`, and `lab_reports`.

## Step-By-Step Verification

### 1. Config Check

```powershell
python -c "import config; assert config.INGESTION_ENABLED; print('Config OK')"
```

Pass criteria: prints `Config OK`.

Fail criteria: import error or assertion failure means `config.py` is missing the ingestion settings.

### 2. Import And Compile Check

```powershell
python -m py_compile ingestion.py pipeline.py main.py db.py
```

Pass criteria: command exits with no output.

Fail criteria: syntax/import errors must be fixed before runtime testing.

### 3. Dependency Check

```powershell
python -c "import paddleocr, paddle, whisper, pdfplumber, pdf2image, PIL, openai, requests; print('Dependencies OK')"
python -m pip check
```

Pass criteria: imports succeed and `pip check` reports no broken requirements.

Fail criteria: missing modules or dependency conflicts.

### 4. OCR Test

Requires a real image at `tests/fixtures/test_prescription.jpg`.

```powershell
python -c "from ingestion import _run_paddle_ocr; from PIL import Image; import io; img = Image.open('tests/fixtures/test_prescription.jpg'); img_bytes = io.BytesIO(); img.save(img_bytes, format='JPEG'); text, conf = _run_paddle_ocr(img_bytes.getvalue()); print(f'OCR text ({conf}):', text[:100]); assert conf >= 0 and isinstance(text, str)"
```

Pass criteria: prints OCR text and confidence without crashing.

Fail criteria: empty text for a clear prescription image, model initialization errors, or image parsing errors.

### 5. Whisper Test

Requires a real audio file at `tests/fixtures/test_voice.wav`.

```powershell
python -c "from ingestion import _transcribe_whisper; audio_bytes = open('tests/fixtures/test_voice.wav','rb').read(); text = _transcribe_whisper(audio_bytes); print('Transcript:', text); assert text is None or isinstance(text, str)"
```

Pass criteria: clear speech returns transcript text. Silence or invalid audio may return `None`.

Fail criteria: ffmpeg/model errors for a valid audio file.

### 6. PDF Text Extraction Test

Requires a real PDF at `tests/fixtures/test_lab.pdf`.

```powershell
python -c "from ingestion import _extract_pdf_text; data = open('tests/fixtures/test_lab.pdf','rb').read(); text, conf = _extract_pdf_text(data); print('PDF text:', conf, text[:200]); assert conf >= 0 and isinstance(text, str)"
```

Pass criteria: text-based PDFs return text with confidence `0.9`.

Fail criteria: readable text PDF returns empty text.

### 7. Lab Extraction Unit Test

```powershell
python -c "from ingestion import _extract_lab_tests_deterministic; text='HbA1c 7.2 % ref 4.0-5.6\nCreatinine 1.1 mg/dL ref 0.7-1.3\nGlucose 180 mg/dL 70-140'; tests = _extract_lab_tests_deterministic(text); print(tests); assert len(tests) == 3"
```

Pass criteria: extracts `hba1c`, `creatinine`, and `glucose`.

Fail criteria: missed common lab names or incorrect numeric parsing.

### 8. Voice Event Extraction Unit Test

```powershell
python -c "from ingestion import _extract_voice_events_deterministic; events = _extract_voice_events_deterministic('Dad was weak today but took Metformin at night and dinner nahi khaya'); print(events); assert len(events) >= 3"
```

Pass criteria: extracts symptom, medication, and diet events.

Fail criteria: compound Hinglish transcript is treated as one unstructured event.

### 9. Processor Shape Tests

These use fake URLs and should fail cleanly without crashing.

```powershell
python -c "from ingestion import process_prescription_photo; task = {'id':'t1','media_url':'https://fake','patient_id':'d0000001-0002-0001-0001-000000000001','from_phone':'+919876543210'}; result = process_prescription_photo(task); print(result); assert 'success' in result and 'error_message' in result"
python -c "from ingestion import process_pdf_report; task = {'id':'t1','media_url':'https://fake','patient_id':'d0000001-0002-0001-0001-000000000001','from_phone':'+919876543210'}; result = process_pdf_report(task); print(result); assert 'success' in result and 'error_message' in result"
python -c "from ingestion import process_voice_note; task = {'id':'t1','media_url':'https://fake','patient_id':'d0000001-0002-0001-0001-000000000001','from_phone':'+919876543210'}; result = process_voice_note(task); print(result); assert 'success' in result and 'error_message' in result"
```

Pass criteria: each returns a dict with `success` and `error_message`.

Fail criteria: unhandled exception, traceback, or missing summary keys.

### 10. Pipeline Test

```powershell
python -c "from pipeline import process_task; process_task('fake-uuid'); print('Pipeline structure OK')"
```

Pass criteria: prints `Pipeline structure OK`.

Fail criteria: missing imports, executor setup error, or unhandled DB exception.

### 11. Webhook Text Regression Test

Server must be running.

```powershell
curl -X POST http://localhost:8000/api/send -H "Content-Type: application/json" -d "{\"phone\":\"+919876543211\",\"message\":\"Hi\"}" | python -m json.tool
```

Pass criteria: normal text response with `status: ok`, `intent`, `confidence`, and `source`.

Fail criteria: text flow is blocked by media handling changes.

### 12. Webhook Media ACK Test

Server must be running.

```powershell
curl -X POST http://localhost:8000/api/send -H "Content-Type: application/json" -d "{\"phone\":\"+919876543211\",\"message\":\"test\",\"media_url\":\"https://fake\",\"media_type\":\"image/jpeg\"}" | python -m json.tool
```

Pass criteria: response in under 5 seconds with:

```json
{
  "status": "ok",
  "reply": "Received. Processing your document... I'll confirm when done.",
  "task_id": "..."
}
```

Fail criteria: request waits for OCR/LLM work instead of returning immediate ACK.

### 13. Health Check

```powershell
curl http://localhost:8000/health/pipeline | python -m json.tool
```

Pass criteria: returns:

```json
{
  "ocr_loaded": false,
  "whisper_loaded": false,
  "thread_pool_active": true
}
```

`ocr_loaded` and `whisper_loaded` may remain `false` until the first real OCR or voice task lazily loads the model.

Fail criteria: endpoint missing, non-JSON response, or `thread_pool_active` is `false`.

### 14. Database Verification

Run in Supabase SQL Editor:

```sql
SELECT id, task_type, status, media_url, media_type, result_summary, error_message, created_at, completed_at
FROM pending_tasks
ORDER BY created_at DESC
LIMIT 10;

SELECT id, file_type, parser_type, parser_confidence, final_status, created_at
FROM media_uploads
ORDER BY created_at DESC
LIMIT 10;

SELECT id, drug_name, source_type, media_upload_id, confidence, recorded_at
FROM medications
WHERE source_type = 'prescription_photo'
ORDER BY recorded_at DESC
LIMIT 10;

SELECT id, test_name, test_value, unit, status, media_upload_id, confidence, created_at
FROM lab_reports
ORDER BY created_at DESC
LIMIT 10;
```

Pass criteria: media ACK creates `pending_tasks`; successful real files create `media_uploads` and domain records.

Fail criteria: pending tasks never advance beyond `queued`/`in_progress`, or parser results are not linked to `media_upload_id`.

## Rollback Instructions

If any issue arises:

1. Set `config.INGESTION_ENABLED = False`.
2. Restart the server.
3. System should revert to text-only processing with no schema rollback required.

For a code-only rollback:

1. Remove or bypass the media block in `main.py` inside `/api/send`.
2. Keep `/health/pipeline` if useful for diagnostics.
3. Restart the server.

For component-level rollback:

- OCR issues: set `config.PADDLEOCR_USE_GPU = False` and keep `PADDLEOCR_LANG = "en"`.
- Whisper issues: set `config.WHISPER_MODEL = "small"` or `"tiny"` for faster CPU inference.
- PDF OCR fallback issues: verify Poppler with `pdftoppm -h`; if unavailable, rely on `pdfplumber` text extraction only.
- LLM fallback issues: set `LLM_EXTRACTION_PRIMARY = None` or stop LM Studio to force deterministic-only extraction.
- Dose validation too strict: increase `config.DOSE_MAX_MULTIPLIER`.
- Lab jump validation too strict: increase `config.LAB_VALUE_JUMP_MULTIPLIER`.

## Monitoring

- Check `pending_tasks.status` for `queued`, `in_progress`, `done`, and `failed`.
- Check `pending_tasks.result_summary` and `error_message` for worker outcomes.
- Check `media_uploads.structured_json` for parser output and validation metadata.
- Check `audit_log` for `MEDIA_TASK_COMPLETED`, voice symptom/diet events, and parser-related failures.
- Watch server logs for ingestion errors, OCR model load messages, Whisper model load messages, and LLM extraction failures.
