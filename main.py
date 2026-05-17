from pathlib import Path
import base64
import json
import os
import re
import sys
import threading
import uuid
from datetime import date, datetime
from decimal import Decimal

import config
import appointment_manager
import crisis
import crisis_runtime
import db
import db_observability
import daily_summary
import handlers
import ingestion
import intent_lock
import intent_embedding
import llm_gateway
import safety_policy
from dependencies import verify_and_install_dependencies
from db import create_pending_task, get_profile_by_phone
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from pipeline import derive_task_type, executor, process_task, start_pharma_task_poller
from router import get_final_intent


# Embedding integration:
# Set EMBEDDING_ENABLED=False in config.py to roll back to keyword-only routing.
# Preload is handled during FastAPI startup, not at module import time, so
# uvicorn reload/shutdown does not race with model loading.
_embedding_preload_checked = False


def _json_safe(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return value


app = FastAPI()
BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "index.html"
UPLOAD_DIR = Path("/tmp/uploaded_media") if os.getenv("VERCEL") else BASE_DIR / "uploaded_media"
UPLOAD_DIR.mkdir(exist_ok=True)


@app.on_event("startup")
async def log_embedding_startup_health() -> None:
    global _embedding_preload_checked
    if not _embedding_preload_checked:
        _embedding_preload_checked = True
        if config.EMBEDDING_ENABLED and getattr(config, "EMBEDDING_PRELOAD_ON_STARTUP", False):
            if not verify_and_install_dependencies(
                auto_install=getattr(config, "EMBEDDING_AUTO_INSTALL_DEPENDENCIES", False)
            ):
                print("WARNING: Embedding dependencies missing; disabling embedding")
                config.EMBEDDING_ENABLED = False
            else:
                try:
                    intent_embedding.load_model()
                except Exception as error:
                    print(f"WARNING: Embedding pre-load failed: {error}")
                    config.EMBEDDING_ENABLED = False
        elif config.EMBEDDING_ENABLED:
            print("Embedding preload skipped at startup. Set EMBEDDING_PRELOAD_ON_STARTUP=true to enable it.")

    try:
        db.log_embedding_decision(
            None,
            None,
            "system_startup",
            "embedding_system",
            1.0 if getattr(intent_embedding, "EMBEDDING_AVAILABLE", False) else 0.0,
            "system",
            "startup_check",
        )
    except Exception as error:
        print(f"WARNING: Embedding startup audit failed: {error}")

    try:
        threading.Thread(target=llm_gateway.refresh_all_model_health, daemon=True).start()
    except Exception as error:
        print(f"WARNING: LLM gateway health refresh failed: {error}")

    try:
        start_pharma_task_poller()
    except Exception as error:
        print(f"WARNING: PharmaAgent task poller failed to start: {error}")

    try:
        db.ensure_safe_medication_ingestion_schema()
    except Exception as error:
        print(f"WARNING: Safe medication ingestion schema setup failed: {error}")

    try:
        db.ensure_pharma_medication_insert_trigger()
    except Exception as error:
        print(f"WARNING: PharmaAgent medication trigger setup failed: {error}")

    try:
        db.ensure_pharma_research_tables()
    except Exception as error:
        print(f"WARNING: PharmaAgent research table setup failed: {error}")

    try:
        db.ensure_pharma_rule_registry_schema()
    except Exception as error:
        print(f"WARNING: PharmaAgent rule registry setup failed: {error}")

    try:
        db.ensure_renal_dosing_rules()
    except Exception as error:
        print(f"WARNING: PharmaAgent renal rules setup failed: {error}")

    try:
        db.ensure_notification_outbox()
    except Exception as error:
        print(f"WARNING: Notification outbox setup failed: {error}")

    try:
        db.ensure_care_coordination_tables()
    except Exception as error:
        print(f"WARNING: Care coordination table setup failed: {error}")

    try:
        db.ensure_appointment_workflow_schema()
    except Exception as error:
        print(f"WARNING: Appointment workflow schema setup failed: {error}")

    try:
        daily_summary.start_daily_summary_scheduler()
    except Exception as error:
        print(f"WARNING: Daily summary scheduler failed to start: {error}")

    try:
        appointment_manager.start_appointment_reminder_scheduler()
    except Exception as error:
        print(f"WARNING: Appointment reminder scheduler failed to start: {error}")


def _bounded_confidence(value, default: float = 0.0) -> float:
    try:
        numeric = float(value)
    except Exception:
        numeric = default
    return round(max(0.0, min(1.0, numeric)), 2)


def _confidence_label(confidence: float) -> str:
    if confidence >= 0.9:
        return "high"
    if confidence >= 0.7:
        return "medium"
    if confidence > 0:
        return "low"
    return "none"


async def _read_send_payload(request: Request) -> dict:
    """
    Parse normal JSON first, with a small local fallback for PowerShell curl.exe
    bodies that arrive as {phone:+91...,message:Hi} after quote stripping.
    Rollback: replace this helper call with await request.json().
    """
    try:
        return await request.json()
    except Exception:
        raw = (await request.body()).decode("utf-8", errors="ignore").strip()
        if raw.startswith("'") and raw.endswith("'"):
            raw = raw[1:-1].strip()
        try:
            return json.loads(raw)
        except Exception:
            match = re.match(r"^\{\s*phone\s*:\s*([^,]+)\s*,\s*message\s*:\s*(.*?)\s*\}$", raw)
            if not match:
                raise
            return {
                "phone": match.group(1).strip().strip("\"'"),
                "message": match.group(2).strip().strip("\"'"),
            }


@app.get("/")
async def read_index() -> FileResponse:
    return FileResponse(INDEX_FILE)


def _media_extension(content_type: str, filename: str = "") -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".pdf", ".wav", ".mp3", ".ogg", ".webm", ".m4a"}:
        return suffix

    normalized = str(content_type or "").lower()
    if normalized == "application/pdf":
        return ".pdf"
    if normalized == "audio/webm":
        return ".webm"
    if normalized == "audio/ogg":
        return ".ogg"
    if normalized == "audio/mpeg":
        return ".mp3"
    if normalized == "audio/wav":
        return ".wav"
    if normalized == "image/png":
        return ".png"
    if normalized == "image/webp":
        return ".webp"
    return ".jpg"


@app.post("/api/upload-media")
async def upload_media(request: Request) -> JSONResponse:
    """
    Local demo upload bridge.
    Browser-selected files are sent as base64 JSON, stored locally, and exposed
    as a localhost media_url that the background parser pipeline can download.
    Rollback: remove this endpoint and send externally hosted media_url values.
    """
    try:
        data = await request.json()
        content_type = str(data.get("media_type") or data.get("content_type") or "application/octet-stream")
        filename = str(data.get("filename") or "upload")
        raw_data = str(data.get("data_base64") or "")
        if "," in raw_data and raw_data.lower().startswith("data:"):
            raw_data = raw_data.split(",", 1)[1]

        file_bytes = base64.b64decode(raw_data, validate=True)
        extension = _media_extension(content_type, filename)
        stored_name = f"{uuid.uuid4().hex}{extension}"
        stored_path = UPLOAD_DIR / stored_name
        stored_path.write_bytes(file_bytes)

        return JSONResponse(
            {
                "status": "ok",
                "media_url": f"{str(request.base_url).rstrip('/')}/media/{stored_name}",
                "media_type": content_type,
                "filename": filename,
                "size": len(file_bytes),
            }
        )
    except Exception as error:
        return JSONResponse({"status": "error", "error": str(error)}, status_code=200)


@app.get("/media/{filename}")
async def read_uploaded_media(filename: str) -> FileResponse:
    safe_name = Path(filename).name
    return FileResponse(UPLOAD_DIR / safe_name)


@app.get("/health/pipeline")
async def pipeline_health():
    return {
        "ocr_loaded": hasattr(ingestion, "_paddleocr") and ingestion._paddleocr is not None,
        "whisper_loaded": hasattr(ingestion, "_whisper_model") and ingestion._whisper_model is not None,
        "thread_pool_active": executor._max_workers > 0,
    }


@app.get("/health/pharma")
async def pharma_health():
    try:
        from pharma_agent import PharmaSafetyEngine
        from db import get_all_drug_interactions
        import config

        # 1. Check if Engine initializes correctly.
        engine = PharmaSafetyEngine()
        rule_count = len(engine.critical_pairs)

        # 2. Check config and DB rule source.
        is_enabled = getattr(config, "PHARMA_AGENT_ENABLED", False)
        db_rule_count = len(get_all_drug_interactions())
        is_serverless = bool(os.getenv("VERCEL"))
        research_summary = db.get_pharma_research_summary() if not is_serverless else {}
        rule_registry = db.get_drug_interaction_registry_summary() if not is_serverless else {}
        renal_rule_count = db.ensure_renal_dosing_rules() if not is_serverless else None
        outbox_ready = db.ensure_notification_outbox() if not is_serverless else None
        coordination_ready = db.ensure_care_coordination_tables() if not is_serverless else None

        # 3. Check LLM Gateway availability. Prefer cached health when present,
        # then fall back to a live best-model check outside serverless health probes.
        try:
            cached_health = llm_gateway.get_cached_model_health()
            llm_ok = any(item.get("alive") for item in cached_health)
            if not llm_ok and not is_serverless:
                llm_ok = llm_gateway.get_best_available_model() is not None
        except Exception:
            cached_health = []
            llm_ok = False

        diagnostics = []
        if not is_enabled:
            diagnostics.append("PHARMA_AGENT_ENABLED is false")
        if rule_count <= 0:
            diagnostics.append("PharmaSafetyEngine loaded no interaction rules")
        if db_rule_count <= 0:
            diagnostics.append("drug_interactions has no active rules")
        if not llm_ok:
            diagnostics.append("No live LLM model currently available for explanations")
        if renal_rule_count is not None and renal_rule_count <= 0:
            diagnostics.append("renal_dosing_rules has no active rules")
        if outbox_ready is False:
            diagnostics.append("notification_outbox is not ready")
        if coordination_ready is False:
            diagnostics.append("care coordination tables are not ready")

        status = "healthy" if (is_enabled and rule_count > 0) else "degraded"
        return {
            "pharma_agent_status": status,
            "rules_loaded": rule_count,
            "llm_available": llm_ok,
            "self_learning_enabled": getattr(config, "PHARMA_SELF_LEARNING_ENABLED", False),
            "diagnostics": diagnostics,
            "db_rules_loaded": db_rule_count,
            # Backward-compatible keys used by earlier UI/debug checks.
            "pharma_agent_enabled": is_enabled,
            "rule_engine_loaded": rule_count > 0,
            "llm_gateway_available": llm_ok,
            "llm_gateway_health": cached_health,
            "research": research_summary,
            "rule_registry": rule_registry,
            "renal_rules_loaded": renal_rule_count,
            "notification_outbox_ready": outbox_ready,
            "care_coordination_ready": coordination_ready,
            "serverless_fast_check": is_serverless,
            "daily_summary_enabled": getattr(config, "DAILY_SUMMARY_ENABLED", False),
            "day_brief_hour_local": getattr(config, "DAILY_DAY_BRIEF_HOUR_LOCAL", 10),
            "night_summary_hour_local": getattr(config, "DAILY_NIGHT_SUMMARY_HOUR_LOCAL", 22),
            "apprise_enabled": getattr(config, "APPRISE_ENABLED", False),
            "apprise_configured": bool(getattr(config, "APPRISE_URLS", [])),
        }
    except Exception as error:
        return {
            "pharma_agent_status": "error",
            "detail": str(error),
        }


@app.get("/health/system")
async def system_health():
    """
    Read-only robustness health endpoint.
    It does not run migrations or perform slow model refreshes.
    """
    db_health = db_observability.database_health() if config.DB_OBSERVABILITY_ENABLED else {"status": "disabled"}
    return {
        "guardrails_enabled": config.SYSTEM_GUARDRAILS_ENABLED,
        "intent_locking_enabled": config.INTENT_LOCKING_ENABLED,
        "crisis_fast_path_enabled": config.CRISIS_FAST_PATH_ENABLED,
        "notification_dispatch_enabled": config.NOTIFICATION_DISPATCH_ENABLED,
        "db": db_health,
        "llm_gateway_health": llm_gateway.get_cached_model_health(),
    }


@app.get("/api/task-status/{task_id}")
async def get_task_status(task_id: str):
    task = db.get_pending_task(task_id)
    if not task:
        return JSONResponse(_json_safe({"status": "not_found", "reply": "Task not found"}))
    status = task.get("status")
    result = task.get("result_summary") or {}
    meds = result.get("medications") or result.get("medications_added") or []
    labs = result.get("lab_values") or result.get("tests_recorded") or []
    events = result.get("events") or result.get("events_recorded") or []

    def _count(value) -> int:
        if isinstance(value, list):
            return len(value)
        if isinstance(value, dict):
            return len(value)
        try:
            return int(value or 0)
        except Exception:
            return 0

    reply = "Processing..."
    if status == "done":
        reply = f"Done. Saved {_count(meds)} med(s), {_count(labs)} lab(s), {_count(events)} update(s)."
    elif status == "failed":
        reply = f"Failed: {task.get('error_message') or 'Unknown error'}"

    return _json_safe({
        "status": status,
        "reply": reply,
        "result": result,
        "error": task.get("error_message"),
    })


@app.get("/api/crisis/card")
async def crisis_card(phone: str | None = None):
    profile = _dashboard_profile(phone)
    if not profile or not profile.get("patient_id"):
        return JSONResponse(_json_safe({"status": "not_found", "reply": "No linked patient found."}), status_code=200)
    packet = crisis.get_emergency_packet(str(profile["patient_id"]))
    return {
        "status": "ok",
        "card": packet,
        "formatted": crisis_runtime.build_patient_crisis_reply(profile),
        "quality": packet.get("quality") or crisis_runtime.crisis_readiness(str(profile["patient_id"])),
    }


def _dashboard_profile(phone: str | None) -> dict | None:
    try:
        if phone:
            return get_profile_by_phone(phone)
        default_phone = getattr(config, "DEMO_DEFAULT_PHONE", "")
        return get_profile_by_phone(default_phone) if default_phone else None
    except Exception:
        return None


@app.get("/api/pharma/dashboard")
async def pharma_dashboard(phone: str | None = None):
    profile = _dashboard_profile(phone)
    if not profile or not profile.get("patient_id"):
        return JSONResponse(_json_safe({"status": "not_found", "reply": "No linked patient found."}), status_code=200)

    patient_id = str(profile["patient_id"])
    patient_name = db.get_patient_name(patient_id) or profile.get("patient_name") or "Patient"
    packet = crisis.get_emergency_packet(patient_id)
    if isinstance(packet, dict) and "quality" not in packet:
        packet["quality"] = crisis.score_crisis_card_quality(packet)

    return JSONResponse(
        _json_safe({
            "status": "ok",
            "profile": {
                "id": str(profile.get("id") or ""),
                "full_name": profile.get("full_name"),
                "role": profile.get("role"),
                "phone": profile.get("phone"),
                "patient_id": patient_id,
                "patient_name": patient_name,
            },
            "alerts": db.get_pharma_alerts(patient_id, config.PHARMA_DASHBOARD_ALERT_LIMIT),
            "approvals": db.get_agent_approvals_for_patient(patient_id, config.PHARMA_DASHBOARD_ALERT_LIMIT),
            "research_reports": db.get_recent_pharma_research_reports(patient_id, config.PHARMA_DASHBOARD_ALERT_LIMIT),
            "rule_registry": db.get_drug_interaction_registry_summary(),
            "audit_summary": db.get_pharma_audit_summary(patient_id, config.PHARMA_DASHBOARD_AUDIT_HOURS),
            "crisis_quality": (packet or {}).get("quality", {}),
            "llm_gateway_health": llm_gateway.get_cached_model_health(),
        })
    )


@app.get("/api/alerts")
async def patient_alerts(phone: str | None = None, limit: int = 20):
    profile = _dashboard_profile(phone)
    if not profile or not profile.get("patient_id"):
        return JSONResponse(_json_safe({"status": "not_found", "alerts": []}), status_code=200)
    safe_limit = max(1, min(int(limit or 20), 50))
    return _json_safe({
        "status": "ok",
        "patient_id": str(profile["patient_id"]),
        "alerts": db.get_pharma_alerts(str(profile["patient_id"]), limit=safe_limit),
        "crisis_readiness": crisis_runtime.crisis_readiness(str(profile["patient_id"])),
    })


@app.post("/api/pharma/approval-action")
async def pharma_approval_action(request: Request):
    try:
        data = await request.json()
        phone = str(data.get("phone") or "").strip()
        rule_hash = str(data.get("rule_hash") or "").strip()
        action = str(data.get("action") or "").strip().lower()
        profile = get_profile_by_phone(phone)
        if not profile or not profile.get("patient_id"):
            return JSONResponse(_json_safe({"status": "not_found", "reply": "No linked caregiver profile found."}), status_code=200)
        if profile.get("role") != "primary_caregiver":
            return JSONResponse(
                _json_safe({
                    "status": "denied",
                    "reply": "Only the primary caregiver can approve or veto PharmaAgent decisions.",
                }),
                status_code=200,
            )

        status_map = {
            "approve": "human_approved",
            "approved": "human_approved",
            "human_approved": "human_approved",
            "finalize": "finalized",
            "finalized": "finalized",
            "veto": "vetoed",
            "vetoed": "vetoed",
        }
        target_status = status_map.get(action)
        if not rule_hash or not target_status:
            return JSONResponse(_json_safe({"status": "error", "reply": "rule_hash and a valid action are required."}), status_code=200)

        import pharma_promotion

        if target_status == "vetoed":
            result = pharma_promotion.veto_approval(rule_hash, actor=phone)
        else:
            result = pharma_promotion.finalize_approval(rule_hash, actor=phone)

        if result.get("status") in {"not_found", "blocked", "error"}:
            return JSONResponse(_json_safe(result), status_code=200)
        return JSONResponse(_json_safe({"status": "ok", **result}))
    except Exception as error:
        return JSONResponse(_json_safe({"status": "error", "reply": str(error)}), status_code=200)


@app.post("/api/pharma/verify-rules")
async def pharma_verify_rules(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    try:
        import pharma_research

        limit = max(1, min(int(data.get("limit") or config.PHARMA_RULE_LIVE_VERIFICATION_LIMIT), 50))
        force = bool(data.get("force", False))
        result = pharma_research.verify_drug_interaction_registry(limit=limit, force=force)
        return JSONResponse(_json_safe(result), status_code=200)
    except Exception as error:
        return JSONResponse(_json_safe({"status": "error", "error": str(error)}), status_code=200)


@app.post("/api/send")
async def send_message(request: Request) -> JSONResponse:
    try:
        data = await _read_send_payload(request)
        phone = data["phone"]
        message = data["message"]

        profile = get_profile_by_phone(phone)
        if profile is None:
            profile = {
                "id": None,
                "phone": phone,
                "full_name": "Unknown",
                "role": "unknown",
                "patient_id": None,
                "patient_name": "the patient",
            }

        # Media ingestion pipeline:
        # If a media_url is present, enqueue background processing and return an
        # immediate ACK. Rollback: remove this block and text-message behavior
        # below remains unchanged.
        media_url = data.get("media_url")
        if media_url:
            media_type = data.get("media_type", "image/jpeg")
            task_type = derive_task_type(media_type)
            task_id = create_pending_task(
                patient_id=profile["patient_id"],
                task_type=task_type,
                media_url=media_url,
                media_type=media_type,
                from_phone=profile["phone"],
                payload={"message": data.get("message", "")},
            )
            if task_id:
                executor.submit(process_task, task_id)
                return JSONResponse(
                    {
                        "status": "ok",
                        "reply": "Received. Processing your document... I'll confirm when done.",
                        "task_id": str(task_id),
                    }
                )
            return JSONResponse(
                {
                    "status": "error",
                    "reply": "I received the document, but could not queue it for processing.",
                    "task_id": None,
                },
                status_code=200,
            )

        pending = db.get_pending_task_for_phone(phone)
        intent_label, router_confidence, router_source = get_final_intent(
            message,
            pending_context=pending,
            patient_id=profile.get("patient_id"),
            profile_id=profile.get("id"),
        )
        result = handlers.route_message(
            message,
            profile,
            pending,
            routed_intent=intent_label,
            routed_confidence=router_confidence,
            routed_source=router_source,
        )

        db.log_incoming_message(phone, message, 0, None, None, profile_id=profile["id"])

        received_log = f"RECEIVED: [{phone}] {message}\n"
        sys.stdout.buffer.write(received_log.encode("utf-8"))
        sys.stdout.buffer.flush()
        intent_log = f"INTENT: {intent_label} ({router_source}, {router_confidence})\n"
        sys.stdout.buffer.write(intent_log.encode("utf-8"))
        sys.stdout.buffer.flush()

        final_intent = str(result.get("intent") or intent_label)
        final_confidence = _bounded_confidence(result.get("confidence", router_confidence))
        final_source = str(result.get("source") or router_source)
        intent_lock_info = intent_lock.response_metadata(final_intent, final_confidence, final_source, pending)
        scope_info = safety_policy.redacted_scope(profile, final_intent)

        reply_limit = 1200 if final_intent.startswith("crisis") else 380
        reply = str(result.get("reply", ""))[:reply_limit]
        return JSONResponse(
            {
                "status": "ok",
                "reply": reply,
                "intent": final_intent,
                "confidence": final_confidence,
                "confidence_score": final_confidence,
                "confidence_label": result.get("confidence_label") or _confidence_label(final_confidence),
                "confidence_reason": result.get("confidence_reason") or f"router:{router_source}",
                "classifier_confidence": _bounded_confidence(result.get("classifier_confidence", final_confidence)),
                "classifier_reason": result.get("classifier_reason", "api:bounded"),
                "normalized_text": result.get("normalized_text", ""),
                "layer": result.get("layer", "api"),
                "source": final_source,
                "caregiver_alerts": result.get("caregiver_alerts", []),
                "intent_lock": intent_lock_info,
                "scope": scope_info,
            }
        )
    except Exception:
        return JSONResponse(
            {
                "status": "error",
                "reply": "Something went wrong. Please try again.",
                "intent": "unknown",
                "confidence": 0.0,
                "confidence_score": 0.0,
                "confidence_label": "none",
                "confidence_reason": "api:exception",
                "classifier_confidence": 0.0,
                "classifier_reason": "api:exception",
                "normalized_text": "",
                "layer": "api_error",
                "source": "error",
            },
            status_code=200,
        )
