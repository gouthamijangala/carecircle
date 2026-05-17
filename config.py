from pathlib import Path


def _load_env_file() -> dict[str, str]:
    env_path = Path(__file__).resolve().parent / ".env"
    values: dict[str, str] = {}

    if not env_path.exists():
        return values

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()

    return values


_ENV = _load_env_file()

SUPABASE_URL = _ENV.get("SUPABASE_URL") or _ENV.get("DATABASE_URL", "")
SUPABASE_KEY = _ENV.get("SUPABASE_KEY", "")
DEMO_DEFAULT_PHONE = _ENV.get("DEMO_DEFAULT_PHONE", "")


def _env_float(key: str, default: float) -> float:
    try:
        return float(_ENV.get(key, default))
    except Exception:
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(_ENV.get(key, default))
    except Exception:
        return default


def _env_bool(key: str, default: bool) -> bool:
    value = _ENV.get(key)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


LLM_ENDPOINT = _ENV.get("LLM_ENDPOINT", "http://192.168.1.93:1234/v1")
LLM_MODEL = _ENV.get("LLM_MODEL", "qwen/qwen3-4b")
LLM_TIMEOUT = _env_float("LLM_TIMEOUT", 6.0)
LLM_TEMPERATURE = _env_float("LLM_TEMPERATURE", 0.55)
LLM_MAX_TOKENS = _env_int("LLM_MAX_TOKENS", 60)
ENABLE_WARM_GREETINGS = _env_bool("ENABLE_WARM_GREETINGS", False)
LLM_DEBUG = _env_bool("LLM_DEBUG", False)
# Human caregiver approval/veto windows need enough time for WhatsApp delivery,
# reading, and typing. PharmAgent auto-approval windows are intentionally much
# longer and should be used only for automated pharmacy workflows.
APPROVAL_CONTEXT_EXPIRY_SECONDS = _env_int("APPROVAL_CONTEXT_EXPIRY_SECONDS", 180)
PHARMAGENT_AUTO_APPROVAL_EXPIRY_SECONDS = _env_int("PHARMAGENT_AUTO_APPROVAL_EXPIRY_SECONDS", 172800)
PHARMAGENT_VETO_EXPIRY_HOURS = _env_int("PHARMAGENT_VETO_EXPIRY_HOURS", 48)
PHARMAGENT_VETO_EXPIRY_SECONDS = _env_int("PHARMAGENT_VETO_EXPIRY_SECONDS", 172800)
MEDICATION_CONTEXT_EXPIRY_MINUTES = _env_int("MEDICATION_CONTEXT_EXPIRY_MINUTES", 120)


# ---------------------------------------------------------------------------
# Embedding intent integration config
# ---------------------------------------------------------------------------
# Purpose:
# - EMBEDDING_ENABLED, EMBEDDING_SHADOW_MODE, and EMBEDDING_SHADOW_PCT control
#   safe rollout. In shadow mode, sampled messages are classified and logged for
#   validation, but deterministic routing still controls the user-facing reply.
# - EMBEDDING_CLUSTER_DESIGN_PATH points to the source design document for the
#   CareCircle cluster universe.
# - EMBEDDING_MODEL_NAME selects the multilingual sentence embedding model.
# - EMBEDDING_CONFIDENCE_THRESHOLDS defines minimum similarity by intent cluster.
#   Crisis and irreversible command clusters intentionally use higher thresholds.
# - EMBEDDING_BLOCKLIST keeps hard-gated commands and media/location routing out
#   of embedding fallback so deterministic safety checks remain authoritative.
# - NORMALIZATION_ALIASES, SPLIT_TYPO_REPAIRS, and SMS_SHORTHAND normalize
#   Hinglish, common care-domain words, typos, and short SMS forms before
#   deterministic or embedding classification.
# - CENTROID_* paths point to locally generated embedding centroid artifacts.
# - EMBEDDING_LOAD_RETRIES and HARD_GATE_CACHE_SIZE support resilient startup
#   and bounded in-memory caching for safety-gate decisions.
#
# Future improvements:
# - Move model and artifact paths to .env when multiple environments exist.
# - Version centroid files with the seed corpus hash and model name together.
# - Add locale-specific normalizer packs instead of one global alias map.
# - Track per-cluster precision/recall and tune thresholds from real transcripts.

EMBEDDING_CLUSTER_DESIGN_PATH = "Context/Cluster_design.md"
EMBEDDING_ENABLED = True
EMBEDDING_PRELOAD_ON_STARTUP = _env_bool("EMBEDDING_PRELOAD_ON_STARTUP", False)
EMBEDDING_AUTO_INSTALL_DEPENDENCIES = _env_bool("EMBEDDING_AUTO_INSTALL_DEPENDENCIES", False)
EMBEDDING_SHADOW_MODE = True  # Log embedding decisions but don't use them yet
EMBEDDING_SHADOW_PCT = 30     # 30% of traffic gets shadow-logged
EMBEDDING_TRAFFIC_PCT = EMBEDDING_SHADOW_PCT
EMBEDDING_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDING_CONFIDENCE_THRESHOLDS = {
    # Crisis family - NEVER let low confidence trigger emergency
    "crisis_medical": 0.75,
    "crisis_safety": 0.75,
    "crisis_self_harm": 0.70,
    "crisis_death": 0.75,
    # Medication family
    "medication_due_now": 0.60,
    "medication_report": 0.55,
    "medication_list": 0.60,
    "medication_schedule": 0.60,
    "medication_side_effect": 0.60,
    # Health status
    "symptom_report": 0.55,
    "vital_report": 0.65,
    "lab_report": 0.65,
    "health_status_query": 0.60,
    # Care management
    "new_prescription": 0.60,
    "appointment": 0.60,
    "caregiver_handoff": 0.60,
    "document_upload_confirmation": 0.60,
    # Commands - high bar for irreversible actions
    "approve_command": 0.75,
    "veto_command": 0.75,
    # Conversational
    "greeting_help": 0.55,
    "emotional_checkin": 0.55,
    "caregiver_observation": 0.55,
    # Fallback
    "default": 0.55,
}
EMBEDDING_BLOCKLIST = {
    "approve_command",
    "veto_command",
    "photo_upload",
    "audio_note",
    "pdf_upload",
    "location_update",
    "photo_processing",
    "audio_processing",
    "pdf_processing",
}
NORMALIZATION_ALIASES = {
    "nhi": "nahi",
    "dawa": "medicine",
    "dawai": "medicine",
    "tablet": "medicine",
    "med": "medicine",
    "doc": "doctor",
    "apt": "appointment",
    "rprt": "report",
    "seene": "chest",
    "mein": "in",
    "dard": "pain",
    "behosh": "unconscious",
    "kud": "suicide",
    "kushi": "suicide",
    "mar": "died",
    "gaye": "died",
    "intqaal": "died",
    "li": "taken",
    "kha": "taken",
    "skip": "missed",
    "dil": "heart",
    "daura": "attack",
    "saans": "breathing",
    "chakkar": "dizziness",
    "ulti": "vomiting",
    "bukhar": "fever",
    "kal": "yesterday",
    "parso": "day after tomorrow",
    "abhi": "now",
    "iss waqt": "now",
    "ho raha": "happening",
}
SPLIT_TYPO_REPAIRS = {
    "tab let": "tablet",
    "ambu lance": "ambulance",
    "heart atack": "heart attack",
    "chest painn": "chest pain",
    "medicne": "medicine",
    "doctar": "doctor",
    "appoinment": "appointment",
    "bloo d": "blood",
    "suger": "sugar",
    "prescrip tion": "prescription",
}
SMS_SHORTHAND = {
    "u": "you",
    "plz": "please",
    "r": "are",
    "2": "to",
    "4": "for",
    "gr8": "great",
    "thx": "thanks",
    "msg": "message",
    "txt": "text",
}
CENTROID_PATH = "intent_centroids.npy"
INTENT_ORDER_PATH = "intent_order.json"
CENTROID_SEED_HASH_PATH = "centroid_seed_hash.txt"
EMBEDDING_LOAD_RETRIES = 3
NORMALIZER_DEBUG = False
HARD_GATE_CACHE_SIZE = 300


# ---------------------------------------------------------------------------
# Media ingestion pipeline config
# ---------------------------------------------------------------------------
# Purpose:
# - INGESTION_* controls the local background media parsing worker budget and
#   maximum runtime per image/audio/PDF task.
# - PaddleOCR settings are intentionally CPU-first for simple local demos.
# - Whisper settings default to the medium model on CPU for accuracy without
#   requiring GPU hardware.
# - PDF settings decide when to trust embedded text versus falling back to OCR.
# - LLM extraction settings prefer the local LM Studio Qwen endpoint, with a
#   free-model fallback endpoint reserved for later manual API-key activation.
# - Drug resolution settings support a local formulary cache and optional RxNav
#   lookup when network access is allowed.
# - Dose/lab validation thresholds are physical sanity bounds only; they are not
#   diagnostic rules and should be reviewed by clinicians before production use.
#
# Future improvements:
# - Move parser worker settings to environment variables for deployment tiers.
# - Add per-file-size timeout scaling instead of one global timeout.
# - Version parser confidence calibration by parser_type and document source.
# - Add patient-specific lab baseline checks after enough historical data exists.

# - Media ingestion pipeline -
INGESTION_ENABLED = True
INGESTION_THREAD_POOL_SIZE = 3
INGESTION_TIMEOUT_SECONDS = 300  # 5 minutes max per task

# OCR (PaddleOCR)
PADDLEOCR_LANG = "en"
PADDLEOCR_USE_GPU = False

# Audio transcription (Whisper)
WHISPER_MODEL = "medium"  # 'tiny','small','medium','large'
WHISPER_DEVICE = "cpu"

# PDF extraction
PDF_TEXT_MIN_LENGTH = 50  # fallback to OCR if text shorter than this
PDF_DPI = 200

# LLM extraction fallback (FREE MODELS ONLY)
LLM_EXTRACTION_PRIMARY = "http://192.168.1.93:1234/v1"
LLM_EXTRACTION_PRIMARY_MODEL = "qwen/qwen3-4b"
LLM_EXTRACTION_FALLBACK = "https://openrouter.ai/api/v1/chat/completions"
LLM_EXTRACTION_FALLBACK_MODEL = "google/gemini-flash-1.5"  # FREE model
LLM_EXTRACTION_TIMEOUT = 30
LLM_EXTRACTION_API_KEY = None  # set in environment

# Drug resolution
DRUG_FORMULARY_CACHE_TTL = 3600
RXNAV_API_BASE = "https://rxnav.nlm.nih.gov/REST"
RXNAV_INTERACTION_API_ENABLED = _env_bool("RXNAV_INTERACTION_API_ENABLED", False)

# Dose validation
DOSE_MAX_MULTIPLIER = 3.0

# Lab validation (use exact test_name values from lab_reports table)
LAB_PHYSICAL_LIMITS = {
    "creatinine": (0.1, 50.0),
    "hba1c": (1.0, 25.0),
    "hemoglobin": (3.0, 25.0),
    "glucose": (10.0, 1000.0),  # matches lab_reports.test_name
    "blood_pressure_systolic": (20.0, 400.0),
    "blood_pressure_diastolic": (10.0, 300.0),
    "pulse": (10, 300),
    "spo2": (10, 100),
    "temperature": (25, 45),
}
LAB_VALUE_JUMP_MULTIPLIER = 5.0


# ---------------------------------------------------------------------------
# Pharma Agent config
# ---------------------------------------------------------------------------
PHARMA_AGENT_ENABLED = _env_bool(
    "PHARMA_AGENT_ENABLED",
    _env_bool("PHARMAGENT_ENABLED", True),
)
PHARMA_RULE_CACHE_TTL = _env_int("PHARMA_RULE_CACHE_TTL", 3600)
PHARMA_EXPLANATION_CONFIDENCE_THRESHOLD = _env_float("PHARMA_EXPLANATION_CONFIDENCE_THRESHOLD", 0.7)
PHARMA_CRITICAL_SEVERITIES = {"critical", "high"}
PHARMA_RENAL_CLEARED_DRUGS = {"metformin", "furosemide", "digoxin"}
PHARMA_EGFR_WARNING_THRESHOLD = 30.0
PHARMA_RENAL_CONTEXT_ALERT_ON_MISSING = _env_bool("PHARMA_RENAL_CONTEXT_ALERT_ON_MISSING", True)
PHARMA_RENAL_CONTEXT_LOOKBACK_DAYS = _env_int("PHARMA_RENAL_CONTEXT_LOOKBACK_DAYS", 180)
PHARMA_IDEMPOTENCY_WINDOW_MINUTES = 60
PHARMA_MAX_EXPLANATION_CHARS = 380
PHARMA_EXTERNAL_INTERACTION_LOOKUP_ENABLED = _env_bool("PHARMA_EXTERNAL_INTERACTION_LOOKUP_ENABLED", True)
PHARMA_EXTERNAL_INTERACTION_MAX_PAIRS = _env_int("PHARMA_EXTERNAL_INTERACTION_MAX_PAIRS", 5)
PHARMA_DASHBOARD_ALERT_LIMIT = _env_int("PHARMA_DASHBOARD_ALERT_LIMIT", 12)
PHARMA_DASHBOARD_AUDIT_HOURS = _env_int("PHARMA_DASHBOARD_AUDIT_HOURS", 24)
PHARMA_TASK_POLLER_ENABLED = _env_bool("PHARMA_TASK_POLLER_ENABLED", True)
PHARMA_TASK_POLLER_INTERVAL_SECONDS = _env_float("PHARMA_TASK_POLLER_INTERVAL_SECONDS", 5.0)
PHARMA_TASK_POLLER_BATCH_SIZE = _env_int("PHARMA_TASK_POLLER_BATCH_SIZE", 5)
PHARMA_RESEARCH_ENABLED = _env_bool("PHARMA_RESEARCH_ENABLED", True)
PHARMA_RESEARCH_SHADOW_MODE = _env_bool("PHARMA_RESEARCH_SHADOW_MODE", True)
PHARMA_RESEARCH_MAX_PAIRS_PER_MEDICATION = _env_int("PHARMA_RESEARCH_MAX_PAIRS_PER_MEDICATION", 10)
PHARMA_RESEARCH_REPORT_TTL_HOURS = _env_int("PHARMA_RESEARCH_REPORT_TTL_HOURS", 168)
PHARMA_RESEARCH_MIN_CONFIDENCE = _env_float("PHARMA_RESEARCH_MIN_CONFIDENCE", 0.75)
PHARMA_RESEARCH_PUBMED_ENABLED = _env_bool("PHARMA_RESEARCH_PUBMED_ENABLED", True)
PHARMA_RESEARCH_LLM_PLANNER_ENABLED = _env_bool("PHARMA_RESEARCH_LLM_PLANNER_ENABLED", False)
PHARMA_RESEARCH_LLM_SYNTHESIS_ENABLED = _env_bool("PHARMA_RESEARCH_LLM_SYNTHESIS_ENABLED", False)
PHARMA_RESEARCH_NVIDIA_SAFETY_REQUIRED = _env_bool("PHARMA_RESEARCH_NVIDIA_SAFETY_REQUIRED", False)
PHARMA_RESEARCH_RXNAV_APPROX_ENABLED = _env_bool("PHARMA_RESEARCH_RXNAV_APPROX_ENABLED", False)
PHARMA_RULE_LIVE_VERIFICATION_TTL_HOURS = _env_int("PHARMA_RULE_LIVE_VERIFICATION_TTL_HOURS", 168)
PHARMA_RULE_LIVE_VERIFICATION_LIMIT = _env_int("PHARMA_RULE_LIVE_VERIFICATION_LIMIT", 20)
PHARMA_RULE_PROMOTION_ENABLED = _env_bool("PHARMA_RULE_PROMOTION_ENABLED", True)
PHARMA_RULE_AUTO_APPROVAL_ENABLED = _env_bool("PHARMA_RULE_AUTO_APPROVAL_ENABLED", True)
PHARMA_RULE_AUTO_APPROVAL_SEVERITIES = {"high"}
PHARMA_RULE_HUMAN_REQUIRED_SEVERITIES = {"critical"}
PHARMA_STALE_TASK_REQUEUE_MINUTES = _env_int("PHARMA_STALE_TASK_REQUEUE_MINUTES", 10)

# Public OpenStreetMap/Overpass can timeout under load. Keep live lookup opt-in
# for routine cache refreshes; fallback map links still work without it.
CRISIS_OSM_LOOKUP_ENABLED = _env_bool("CRISIS_OSM_LOOKUP_ENABLED", False)
CRISIS_OSM_LOOKUP_TIMEOUT_SECONDS = _env_float("CRISIS_OSM_LOOKUP_TIMEOUT_SECONDS", 2.0)
CRISIS_OSM_LOOKUP_LOG_COOLDOWN_SECONDS = _env_int("CRISIS_OSM_LOOKUP_LOG_COOLDOWN_SECONDS", 300)


# ---------------------------------------------------------------------------
# Side-effect hints lookup table (moved from handlers.py to avoid circular import)
# ---------------------------------------------------------------------------
SIDE_EFFECT_HINTS = {
    ("amlodipine", "dizziness"): "Amlodipine can cause dizziness, especially when standing up. Sit before standing. ",
    ("amlodipine", "chakkar"): "Amlodipine can cause dizziness. Sit before standing. ",
    ("amlodipine", "swelling"): "Amlodipine can cause ankle swelling. Elevate legs when resting. ",
    ("amlodipine", "sojan"): "Amlodipine can cause ankle swelling. Elevate legs when resting. ",
    ("amlodipine", "headache"): "Amlodipine can cause headache. It usually improves after a few days. ",
    ("metformin", "nausea"): "Metformin can cause nausea or stomach upset. Taking it with food helps. ",
    ("metformin", "vomiting"): "Metformin can cause stomach upset. Taking it with food helps. ",
    ("metformin", "ulti"): "Metformin can cause stomach upset. Taking it with food helps. ",
    ("metformin", "diarrhea"): "Metformin can cause loose stools. Usually improves after a few days. ",
    ("metformin", "loose motion"): "Metformin can cause loose stools. Usually improves after a few days. ",
    ("ramipril", "cough"): "Ramipril can cause a dry cough. Contact your doctor if it persists. ",
    ("ramipril", "dizziness"): "Ramipril can cause dizziness. Stand up slowly. ",
    ("ramipril", "chakkar"): "Ramipril can cause dizziness. Stand up slowly. ",
    ("glimepiride", "dizziness"): "Glimepiride can cause low-sugar symptoms like dizziness. Eat on time. ",
    ("glimepiride", "chakkar"): "Glimepiride can cause low-sugar symptoms like dizziness. Eat on time. ",
    ("glimepiride", "sweating"): "Glimepiride can cause low-sugar symptoms like sweating. Eat on time. ",
    ("glimepiride", "kamzori"): "Glimepiride can cause low-sugar symptoms like weakness. Eat on time. ",
    ("glimepiride", "weak"): "Glimepiride can cause low-sugar symptoms like weakness. Eat on time. ",
}


# ---------------------------------------------------------------------------
# System robustness and guardrail config
# ---------------------------------------------------------------------------
SYSTEM_GUARDRAILS_ENABLED = _env_bool("SYSTEM_GUARDRAILS_ENABLED", True)
INTENT_LOCKING_ENABLED = _env_bool("INTENT_LOCKING_ENABLED", True)
CRISIS_FAST_PATH_ENABLED = _env_bool("CRISIS_FAST_PATH_ENABLED", True)
CRISIS_ALERT_DEDUPE_SECONDS = _env_int("CRISIS_ALERT_DEDUPE_SECONDS", 300)
CRISIS_RUNTIME_BUDGET_MS = _env_int("CRISIS_RUNTIME_BUDGET_MS", 900)
NOTIFICATION_DISPATCH_ENABLED = _env_bool("NOTIFICATION_DISPATCH_ENABLED", True)
NOTIFICATION_AUDIT_ONLY = _env_bool("NOTIFICATION_AUDIT_ONLY", True)
NOTIFICATION_OUTBOX_ENABLED = _env_bool("NOTIFICATION_OUTBOX_ENABLED", True)
APPRISE_ENABLED = _env_bool("APPRISE_ENABLED", False)
APPRISE_URLS = [
    item.strip()
    for item in (_ENV.get("APPRISE_URLS", "") or "").split(",")
    if item.strip()
]
APPRISE_CRITICAL_ONLY = _env_bool("APPRISE_CRITICAL_ONLY", True)
DAILY_SUMMARY_ENABLED = _env_bool("DAILY_SUMMARY_ENABLED", True)
DAILY_SUMMARY_HOUR_LOCAL = _env_int("DAILY_SUMMARY_HOUR_LOCAL", 7)
DAILY_DAY_BRIEF_HOUR_LOCAL = _env_int("DAILY_DAY_BRIEF_HOUR_LOCAL", 10)
DAILY_NIGHT_SUMMARY_HOUR_LOCAL = _env_int("DAILY_NIGHT_SUMMARY_HOUR_LOCAL", 22)
DAILY_SUMMARY_POLLER_INTERVAL_SECONDS = _env_int("DAILY_SUMMARY_POLLER_INTERVAL_SECONDS", 300)
APPOINTMENT_REMINDERS_ENABLED = _env_bool("APPOINTMENT_REMINDERS_ENABLED", True)
APPOINTMENT_REMINDER_WINDOW_HOURS = _env_int("APPOINTMENT_REMINDER_WINDOW_HOURS", 72)
APPOINTMENT_REMINDER_POLLER_INTERVAL_SECONDS = _env_int("APPOINTMENT_REMINDER_POLLER_INTERVAL_SECONDS", 900)
APPOINTMENT_CONFIRM_EXPIRY_HOURS = _env_int("APPOINTMENT_CONFIRM_EXPIRY_HOURS", 72)
PERSONALIZATION_ENABLED = _env_bool("PERSONALIZATION_ENABLED", True)
PERSONALIZATION_MAX_CONTEXT_ITEMS = _env_int("PERSONALIZATION_MAX_CONTEXT_ITEMS", 4)
LLM_POLICY_ENABLED = _env_bool("LLM_POLICY_ENABLED", True)
LLM_FALLBACK_MAX_TOKENS = _env_int("LLM_FALLBACK_MAX_TOKENS", 70)
LLM_EXTRACTION_TOP_P = _env_float("LLM_EXTRACTION_TOP_P", 0.75)
LLM_FALLBACK_TOP_P = _env_float("LLM_FALLBACK_TOP_P", 0.8)
LLM_STRICT_TEMPERATURE = _env_float("LLM_STRICT_TEMPERATURE", 0.0)
DB_OBSERVABILITY_ENABLED = _env_bool("DB_OBSERVABILITY_ENABLED", True)


# ---------------------------------------------------------------------------
# Pharma Agent model routing config
# ---------------------------------------------------------------------------
# Primary model (Qwen3-4B) - thinking mode ON, longer timeout.
PHARMA_PRIMARY_MODEL = {
    "base_url": "http://192.168.1.93:1234/v1",
    "model_id": "qwen/qwen3-4b",
    "max_tokens": 600,
    "temperature": 0,
    "prompt_suffix": "/think",
    "response_format": "json_object",
    "timeout": 60,
}

# Reasoning model (Nemotron) - deep synthesis, long context.
PHARMA_REASONING_MODEL = {
    "base_url": "https://openrouter.ai/api/v1/chat/completions",
    "model_id": "nvidia/nemotron-3-super-120b-a12b:free",
    "max_tokens": 2000,
    "temperature": 0.1,
    "prompt_suffix": "",
    "response_format": None,
    "timeout": 120,
}

# Safety moderation model (NVIDIA API).
PHARMA_SAFETY_MODEL = {
    "base_url": "https://integrate.api.nvidia.com/v1",
    "model_id": "meta/llama-guard-4-12b",
    "max_tokens": 100,
    "temperature": 0,
    "timeout": 10,
}


# ---------------------------------------------------------------------------
# Pharma Agent self-learning config
# ---------------------------------------------------------------------------
PHARMA_SELF_LEARNING_ENABLED = _env_bool("PHARMA_SELF_LEARNING_ENABLED", True)
PHARMA_FEEDBACK_TABLE = "pharmagent_feedback"
PHARMA_MIN_FEEDBACK_COUNT = _env_int("PHARMA_MIN_FEEDBACK_COUNT", 3)
PHARMA_FEEDBACK_CONFIDENCE_THRESHOLD = 0.8

# SIDE_EFFECT_HINTS is intentionally kept as the existing dict above.


# ---------------------------------------------------------------------------
# Document Processing Pipeline Config
# ---------------------------------------------------------------------------
ENABLE_HYBRID_PDF_EXTRACTION = True
PDF_TEXT_DENSITY_THRESHOLD = 50
PDF_OCR_FALLBACK_DPI = 200

# Document Type Routing (Embedding-based)
DOC_TYPE_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DOC_TYPE_CONFIDENCE_THRESHOLD = 0.70
DOC_TYPE_ANCHORS = {
    "prescription": "tablet capsule dose frequency doctor patient Rx 1-0-0 after food before food dawai goli syrup chashma morning night",
    "lab_report": "test result value unit reference range mg/dL g/dL CBC creatinine haemoglobin low high normal report",
    "voice_note": "feeling took missed dizzy pain yesterday morning night dad uncle patient symptom kaisa hai",
    "advice_note": "diet lifestyle walk exercise avoid stress smoking low salt follow-up review parhez",
    "discharge_summary": "discharged admitted condition follow-up appointment diagnosis hospital stay chuthi",
    "referral_letter": "referral specialist consult urgent reason evaluation assessment visheshagya",
    "medical_history": "history of past surgery diabetes hypertension allergy family history patient has known case of purana rog",
    "general_note": "note message update information record miscellaneous",
}

# Validation & Safety
LAB_PHYSICAL_LIMITS = {
    "creatinine": (0.1, 30.0),
    "glucose": (10.0, 800.0),
    "hba1c": (3.0, 20.0),
    "sodium": (100.0, 180.0),
    "potassium": (1.5, 9.0),
    "wbc": (0.1, 100.0),
    "platelets": (5.0, 1500.0),
    "tsh": (0.001, 100.0),
    "cholesterol": (50.0, 700.0),
}
DOSE_MAX_MULTIPLIER = 3.0
MAX_OCR_CONTEXT_TOKENS = 1000

# ---------------------------------------------------------------------------
# Safe Medication Activation Gate
# ---------------------------------------------------------------------------
MEDICATION_STATUS_DRAFT_EXTRACTED = "draft_extracted"
MEDICATION_STATUS_VALIDATION_PENDING = "validation_pending"
MEDICATION_STATUS_SUSPICIOUS = "suspicious"
MEDICATION_STATUS_INTERACTION_PENDING = "interaction_pending"
MEDICATION_STATUS_VETO_REQUIRED = "veto_required"
MEDICATION_STATUS_APPROVED = "approved"
MEDICATION_STATUS_ACTIVE = "active"
MEDICATION_STATUS_REJECTED = "rejected"

MEDICATION_CANDIDATE_STATUSES = {
    MEDICATION_STATUS_DRAFT_EXTRACTED,
    MEDICATION_STATUS_VALIDATION_PENDING,
    MEDICATION_STATUS_SUSPICIOUS,
    MEDICATION_STATUS_INTERACTION_PENDING,
    MEDICATION_STATUS_VETO_REQUIRED,
    MEDICATION_STATUS_APPROVED,
    MEDICATION_STATUS_REJECTED,
}
MEDICATION_ACTIVATION_STATUSES = {MEDICATION_STATUS_ACTIVE}
MEDICATION_VALIDATION_MIN_CONFIDENCE = _env_float("MEDICATION_VALIDATION_MIN_CONFIDENCE", 0.85)
MEDICATION_DRUG_RESOLUTION_MIN_CONFIDENCE = _env_float("MEDICATION_DRUG_RESOLUTION_MIN_CONFIDENCE", 0.80)
MEDICATION_ALLOWED_DOSE_UNITS = {
    "mg",
    "mcg",
    "g",
    "ml",
    "iu",
    "unit",
    "units",
    "%",
    "tablet",
    "capsule",
    "drop",
    "drops",
    "puff",
    "puffs",
    "tsp",
    "teaspoon",
    "tbsp",
    "tablespoon",
    "spoon",
    "sachet",
    "vial",
    "ampoule",
    "amp",
    "application",
    "applications",
    "spray",
    "sprays",
}
MEDICATION_FREQUENCY_ALIASES = {
    "1-0-0": "OD",
    "0-1-0": "OD",
    "0-0-1": "HS",
    "1-0-1": "BD",
    "1-1-0": "BD",
    "0-1-1": "BD",
    "1-1-1": "TDS",
    "1-1-1-1": "QID",
    "od": "OD",
    "once daily": "OD",
    "daily": "OD",
    "bd": "BD",
    "bid": "BD",
    "twice daily": "BD",
    "tds": "TDS",
    "tid": "TDS",
    "three times daily": "TDS",
    "qid": "QID",
    "four times daily": "QID",
    "hs": "HS",
    "night": "HS",
    "bedtime": "HS",
    "sos": "SOS",
    "prn": "SOS",
    "as needed": "SOS",
}
MEDICATION_NOTIFY_ON_REVIEW_NEEDED = _env_bool("MEDICATION_NOTIFY_ON_REVIEW_NEEDED", True)
