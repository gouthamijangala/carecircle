"""
Background media ingestion orchestrator for CareCircle.

This module is intentionally thin: it fetches pending_tasks rows, chooses the
correct ingestion processor, and records the final task status. The processors
own parsing/validation/DB domain writes.

Rollback considerations:
- Processing functions are not wrapped in a single DB transaction because they
  may call external parsers/LLMs and write audit rows incrementally.
- If a processor fails after partial writes, the task is marked failed and the
  media_uploads/audit rows remain as a forensic trail for manual correction.
- Existing Supabase schemas may reject intermediate status values such as
  'processing'. `_mark_processing` records an audit heartbeat and leaves the row
  queued until the processor writes done/failed.
"""

from concurrent.futures import ThreadPoolExecutor
import threading
import time

import config
import crisis
import db
import ingestion
import notification_dispatcher


executor = ThreadPoolExecutor(max_workers=config.INGESTION_THREAD_POOL_SIZE)
_submitted_task_ids: set[str] = set()
_submitted_task_lock = threading.Lock()
_pharma_poller_started = False


def derive_task_type(mime_type: str) -> str:
    """
    Return the pending_tasks.task_type for a media MIME type.
    """
    media_type = str(mime_type or "").strip().lower()
    if media_type.startswith("image/"):
        return "ocr_prescription"
    if media_type.startswith("audio/"):
        return "transcribe_audio"
    if media_type == "application/pdf":
        return "parse_pdf"
    return ""


def _existing_or_derived_task_type(task: dict) -> str:
    try:
        media_type = task.get("media_type")
        derived = derive_task_type(media_type)
        if derived:
            return derived
        return str(task.get("task_type") or "").strip()
    except Exception:
        return ""


def _mark_processing(task_id: str) -> None:
    """
    Record processing start without violating deployment-specific CHECK constraints.
    """
    claimed = db.mark_pending_task_in_progress(task_id)
    try:
        db.write_audit(
            None,
            None,
            "pending_tasks",
            task_id,
            "MEDIA_TASK_PROCESSING_STARTED",
            "system",
            {"task_id": task_id, "claimed": claimed},
        )
    except Exception:
        pass


def _task_payload(task: dict) -> dict:
    """
    Convert DB row values into the compact processor contract.
    """
    return {
        "id": str(task.get("id") or ""),
        "media_url": task.get("media_url"),
        "media_type": task.get("media_type"),
        "patient_id": str(task.get("patient_id") or ""),
        "from_phone": task.get("from_phone"),
        "payload": task.get("payload") or {},
    }


def _finalize_task(task_id: str, result: dict) -> None:
    try:
        task = db.get_pending_task(task_id) or {"id": task_id}
        if result.get("success"):
            db.update_pending_task_status(task_id, "done", result, None)
            _log_completion(task_id, result)
            if not _is_pharma_research_task(task):
                _send_processing_result(task_id)
            patient_id = task.get("patient_id")
            if patient_id and not _is_pharma_research_task(task):
                _run_post_hooks(str(patient_id), result)
        else:
            error_message = str(result.get("error_message") or "Media task failed.")
            db.update_pending_task_status(task_id, "failed", result, error_message)
            if not _is_pharma_research_task(task):
                _send_processing_result(task_id)
    except Exception as error:
        try:
            db.update_pending_task_status(task_id, "failed", {"error": str(error)}, str(error))
        except Exception:
            pass


def _is_pharma_research_task(task: dict) -> bool:
    return str((task or {}).get("task_type") or "") == "pharm_research"


def _log_completion(task_id: str, result: dict) -> None:
    """
    Audit successful media task completion.
    """
    try:
        db.write_audit(
            None,
            None,
            "pending_tasks",
            task_id,
            "MEDIA_TASK_COMPLETED",
            "system",
            {
                "task_id": task_id,
                "result_summary": result,
                "notification": "queued",
            },
        )
    except Exception:
        pass


def _count_result_items(value) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return len(value)
    try:
        return int(value or 0)
    except Exception:
        return 0


def _send_user_notification(task: dict, result: dict) -> None:
    """
    Fire-and-forget notification to the user after processing completes.
    Runs in a background thread so it never blocks the pipeline.
    """
    def _notify():
        try:
            from_phone = task.get("from_phone") or "system"

            if not result.get("success"):
                message = f"❌ I couldn't process your file. {result.get('error_message', 'Please try again.')}"
            elif _count_result_items(result.get("medications_added")):
                count = _count_result_items(result.get("medications_added"))
                message = f"✅ Saved {count} medication(s) to the records. Check your daily briefing for details."
            elif _count_result_items(result.get("tests_recorded")):
                count = _count_result_items(result.get("tests_recorded"))
                message = f"✅ Saved {count} lab test(s). Check your daily briefing for flagged results."
            elif _count_result_items(result.get("events_recorded")):
                count = _count_result_items(result.get("events_recorded"))
                message = f"✅ Saved {count} update(s) to the care timeline."
            else:
                message = "✅ File processed. No actionable data was found."

            notification_dispatcher.dispatch_user_message(from_phone, message)
        except Exception:
            pass

    threading.Thread(target=_notify, daemon=True).start()


def _send_processing_result(task_id: str) -> None:
    """
    Fetch task result from DB and send an appropriate completion message.
    """
    def _notify():
        try:
            task = db.get_pending_task(task_id)
            if not task:
                return

            result = task.get("result_summary") or {}
            from_phone = task.get("from_phone")
            status = str(task.get("status") or "").lower()

            medications = result.get("medications") or result.get("medications_added") or []
            labs = result.get("lab_values") or result.get("tests_recorded") or []
            events = result.get("events") or result.get("events_recorded") or []

            if status == "failed":
                message = f"Could not process file: {task.get('error_message') or 'Unknown error'}"
            elif result.get("error"):
                message = f"File processed with issues: {result['error']}"
            elif result.get("_review_required"):
                count = _count_result_items(medications) or _count_result_items(labs)
                message = f"Found {count} item(s), but some data is unclear. Please review and confirm."
            elif _count_result_items(medications):
                message = f"Saved {_count_result_items(medications)} medication(s). Tap to review."
            elif _count_result_items(labs):
                abnormal = sum(1 for item in labs if isinstance(item, dict) and item.get("_flag")) if isinstance(labs, list) else 0
                message = f"Saved {_count_result_items(labs)} lab result(s)." + (
                    f" {abnormal} flagged for review." if abnormal else ""
                )
            elif _count_result_items(events):
                message = f"Saved {_count_result_items(events)} care update(s)."
            else:
                message = "File received but no medical data was found. Please send a clearer image."

            notification_dispatcher.dispatch_user_message(from_phone, message)
        except Exception:
            pass

    threading.Thread(target=_notify, daemon=True).start()


def _run_post_hooks(patient_id: str, result: dict) -> None:
    """
    Run post-parse hooks after successful DB write.
    """
    def _hooks():
        try:
            try:
                patient_name = db.get_patient_name(patient_id) or "Patient"
                card = crisis.build_crisis_card(patient_id, patient_name)
                db.upsert_crisis_cache(patient_id, card)
            except Exception:
                pass

            if not result.get("pharma_agent_triggered"):
                import pharma_agent

                medications_added_list = (
                    result.get("medications_added")
                    if isinstance(result.get("medications_added"), list)
                    else []
                )
                for med_id in medications_added_list:
                    med = db.get_medication_by_id(med_id)
                    if med:
                        try:
                            pharma_agent.process_new_medication(
                                patient_id=patient_id,
                                new_drug=med["drug_name"],
                                dose_amount=med.get("dose_amount"),
                                prescribed_by=med.get("prescribed_by"),
                                from_phone=None,
                                trigger="async_pipeline",
                            )
                        except Exception as error:
                            print(f"PharmaAgent async trigger failed for med {med_id}: {error}")
        except Exception:
            pass

    threading.Thread(target=_hooks, daemon=True).start()


def send_user_notification(task: dict, result: dict) -> None:
    task_id = str((task or {}).get("id") or "")
    if task_id:
        _send_processing_result(task_id)
    else:
        _send_user_notification(task, result)


def run_post_hooks(patient_id: str, result: dict) -> None:
    _run_post_hooks(patient_id, result)


def _process_pharm_research_task(task: dict) -> dict:
    payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
    request_type = str(payload.get("request_type") or "").strip()
    patient_id = str(task.get("patient_id") or payload.get("patient_id") or "")

    if request_type == "new_medication_safety_check":
        medication_id = payload.get("medication_id")
        med = db.get_medication_by_id(medication_id)
        if not med:
            return {
                "success": False,
                "error_message": f"Medication not found for PharmaAgent task: {medication_id}",
                "request_type": request_type,
            }

        import pharma_agent

        result = pharma_agent.process_new_medication(
            patient_id=patient_id or med.get("patient_id"),
            new_drug=med.get("drug_name"),
            dose_amount=med.get("dose_amount"),
            prescribed_by=med.get("prescribed_by"),
            from_phone=task.get("from_phone"),
            trigger="queued_medication_insert",
            medication_id=str(medication_id or ""),
        )
        return {
            "success": result.get("status") in {"completed", "skipped"},
            "request_type": request_type,
            "medication_id": str(medication_id or ""),
            "drug_name": med.get("drug_name"),
            "pharma_agent_result": result,
            "max_severity": result.get("max_severity"),
            "interactions_count": result.get("interactions_count"),
            "alerts_created": result.get("alerts_created") or [],
            "error_message": result.get("error"),
        }

    if request_type == "interaction_research":
        import pharma_research

        result = pharma_research.run_interaction_research(
            patient_id=patient_id,
            medication_id=payload.get("medication_id") or None,
            drug_a=payload.get("drug_a"),
            drug_b=payload.get("drug_b"),
            trigger=payload.get("trigger") or "interaction_research",
        )
        return {
            "success": bool(result.get("success")),
            "request_type": request_type,
            "research_key": result.get("research_key"),
            "report_id": result.get("report_id"),
            "status": result.get("status"),
            "severity": result.get("severity"),
            "confidence": result.get("confidence"),
            "gates": result.get("gates"),
            "error_message": result.get("error_message"),
        }

    if request_type == "medication_side_effect_lookup":
        result = ingestion.process_side_effect_lookup(
            patient_id=patient_id,
            drug_name=payload.get("drug_name"),
            symptom=payload.get("symptom"),
            raw_text=payload.get("raw_text") or "",
        )
        reply = result.get("reply") if isinstance(result, dict) else None
        if reply and task.get("from_phone"):
            notification_dispatcher.dispatch_user_message(task.get("from_phone"), reply)
        return {
            "success": isinstance(result, dict) and not result.get("error"),
            "request_type": request_type,
            "result": result,
            "error_message": result.get("error") if isinstance(result, dict) else "Side-effect lookup failed",
        }

    return {
        "success": False,
        "request_type": request_type or "unknown",
        "error_message": "Unsupported PharmaAgent research task",
    }


def process_task(task_id: str) -> None:
    """
    Fetch and process one pending media task.
    """
    try:
        # Step 1: Fetch the pending task. Missing/invalid ids are no-ops.
        task = db.get_pending_task(task_id)
        if not task:
            return
        if str(task.get("status") or "queued") != "queued":
            return

        # Step 2: Mark as actively processing. See `_mark_processing` note.
        _mark_processing(task_id)

        # Step 3: Route by media MIME type first, then existing task_type.
        task_type = _existing_or_derived_task_type(task)
        processor_task = _task_payload(task)

        # Step 4: Dispatch to the appropriate media processor.
        if task_type == "ocr_prescription":
            result = ingestion.process_prescription_photo(processor_task)
        elif task_type == "parse_pdf":
            result = ingestion.process_pdf_report(processor_task)
        elif task_type == "transcribe_audio":
            result = ingestion.process_voice_note(processor_task)
        elif task_type == "pharm_research":
            result = _process_pharm_research_task(task)
        else:
            result = {
                "success": False,
                "error_message": "Unknown task type",
            }

        # Steps 5-6: Persist terminal status, notify the user, and run hooks.
        _finalize_task(task_id, result)
    except Exception as error:
        # Step 7: Catch-all so executor threads never crash the app.
        try:
            db.update_pending_task_status(
                task_id,
                "failed",
                {"success": False, "error_message": str(error)},
                str(error),
            )
        except Exception:
            pass


def submit_task(task_id: str):
    """
    Submit a task to the module-level executor.
    """
    task_key = str(task_id or "")
    if not task_key:
        return None
    with _submitted_task_lock:
        if task_key in _submitted_task_ids:
            return None
        _submitted_task_ids.add(task_key)

    future = executor.submit(process_task, task_key)
    future.add_done_callback(lambda _future: _discard_submitted_task(task_key))
    return future


def _discard_submitted_task(task_id: str) -> None:
    with _submitted_task_lock:
        _submitted_task_ids.discard(str(task_id or ""))


def start_pharma_task_poller() -> bool:
    """
    Start a lightweight poller for queued PharmaAgent tasks.
    This covers medication inserts that happen outside the media upload thread.
    """
    global _pharma_poller_started
    if _pharma_poller_started or not getattr(config, "PHARMA_TASK_POLLER_ENABLED", True):
        return False
    _pharma_poller_started = True
    threading.Thread(target=_pharma_task_poller_loop, daemon=True).start()
    return True


def _pharma_task_poller_loop() -> None:
    interval = max(1.0, float(getattr(config, "PHARMA_TASK_POLLER_INTERVAL_SECONDS", 5.0)))
    batch_size = max(1, int(getattr(config, "PHARMA_TASK_POLLER_BATCH_SIZE", 5)))
    while True:
        try:
            db.requeue_stale_pending_tasks(
                "pharm_research",
                stale_minutes=int(getattr(config, "PHARMA_STALE_TASK_REQUEUE_MINUTES", 10)),
                limit=batch_size,
            )
            for task in db.get_queued_pending_tasks("pharm_research", limit=batch_size):
                submit_task(str(task.get("id") or ""))
            try:
                import pharma_promotion

                pharma_promotion.process_due_auto_approvals(limit=batch_size)
            except Exception:
                pass
        except Exception:
            pass
        time.sleep(interval)
