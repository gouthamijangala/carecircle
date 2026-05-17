import difflib
import re
import unicodedata
from datetime import datetime, timedelta


DEBUG_INTENT = False   # Set to True to see which intent matched


NORMALIZATION_TOKEN_MAP = {
    "nhi": "nahi",
    "nai": "nahi",
    "nahin": "nahi",
    "sey": "se",
    "seyy": "se",
    "pe": "par",
    "mein": "me",
    "main": "mai",
    "kudh": "kood",
    "kud": "kood",
    "koodh": "kood",
    "khud": "kood",
    "li": "liya",
    "liye": "liya",
    "meds": "medications",
    "medicines": "medicine",
    "med": "medicine",
    "dawai": "medicine",
    "dava": "medicine",
    "dawa": "medicine",
    "tablet": "tablet",
    "tablets": "tablets",
    "tab": "tablet",
    "tabs": "tablets",
    "pills": "tablets",
    "attak": "attack",
    "atak": "attack",
    "emergncy": "emergency",
    "emerjency": "emergency",
    "ambulanse": "ambulance",
    "ambulans": "ambulance",
    "collaps": "collapse",
    "behos": "behosh",
    "rap": "rape",
    "raped": "rape",
    "killed": "kill",
    "marna": "maar",
    "mara": "maar",
    "maara": "maar",
}

NORMALIZATION_PHRASES = {
    "some one": "someone",
    "heartattacke": "heart attack",
    "heartattack": "heart attack",
    "heart attak": "heart attack",
    "hert attack": "heart attack",
    "hart attack": "heart attack",
    "chestoain": "chest pain",
    "chestpain": "chest pain",
    "chest painn": "chest pain",
    "chest dard": "chest pain",
    "seene mein dard": "seene me dard",
    "sine me dard": "seene me dard",
    "sans nahi": "saans nahi",
    "saanse nahi": "saans nahi",
    "saansein atak gayi": "saans atak gayi",
    "livr failure": "liver failure",
    "liver fail": "liver failure",
    "kidny failure": "kidney failure",
    "kidney fail": "kidney failure",
    "no medication": "no medications",
    "no medicine": "no medications",
    "no medicines": "no medications",
    "not taken": "not taken",
    "nahi li": "nahi liya",
    "nhi li": "nahi liya",
    "appoinment": "appointment",
    "appointmnt": "appointment",
    "followup": "follow up",
    "present tablet": "present tablets",
    "build par se kood": "bridge par se kood",
    "building par se kood": "building par se kood",
    "i dont want my life": "i do not want my life",
    "i don't want my life": "i do not want my life",
}

FUZZY_NORMALIZATION_TERMS = [
    "ambulance",
    "emergency",
    "appointment",
    "medication",
    "medicine",
    "tablets",
    "skipped",
    "missed",
    "taken",
    "approve",
    "reject",
    "veto",
    "collapse",
    "unconscious",
]


CRISIS_KEYWORDS = [
    "chest pain",
    "heart attack",
    "dard",
    "dardh",
    "dardd",
    "saans",
    "saan",
    "sans",
    "saas",
    "emergency",
    "unconscious",
    "collapse",
    "behosh",
    "ambulance bulao",
    "not breathing",
    "dil ka dora",
    "seene mein dard",
    "seene me dard",
    "gir gaya",
]

PRESENT_MARKERS = [
    "abhi",
    "right now",
    "ho raha hai",
    "ho rha hai",
    "ho rahe hai",
    "ho rahi hai",
    "just now",
    "currently",
    "aaj",
    "iss waqt",
    "is waqt",
    "this moment",
    "abhi abhi",
]

PAST_MARKERS = [
    "tha",
    "thi",
    "the",
    "had",
    "was",
    "were",
    "kal",
    "parso",
    "last week",
    "last month",
    "yesterday",
    "pehle",
    "hua tha",
    "ho gaya tha",
]

EXPLICIT_EMERGENCY_PHRASES = [
    "chest pain",
    "emergency",
    "ambulance",
    "severe breathing problem",
    "breathing issue",
    "severe pain",
    "ambulance bulao",
    "unconscious",
    "collapse",
    "behosh",
    "saans nahi",
    "sans nahi",
    "saan nahi",
    "not breathing",
    "gir gaya",
    "behosh ho gaya",
    "dil ka dora",
    "heart attack aa gaya",
]

YES_WORDS = ["yes", "haan", "ha", "li", "le li", "kha liya", "taken", "done", "ho gaya", "👍", "✓"]
NO_WORDS = ["no", "nahi", "nai", "missed", "bhool gaya", "skip", "skipped", "nahi liya", "👎"]
DEVIATION_KEYWORDS = [
    "before food",
    "pehle",
    "galti se",
    "wrong time",
    "alag waqt",
    "subah",
    "raat ko",
    "late",
    "early",
]

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic")
AUDIO_EXTENSIONS = (".ogg", ".mp3", ".m4a", ".wav", ".aac", ".opus")
PDF_EXTENSIONS = (".pdf",)

KNOWN_DRUGS = [
    "metformin",
    "amlodipine",
    "glimepiride",
    "ramipril",
    "aspirin",
    "atorvastatin",
    "telmisartan",
    "losartan",
    "insulin",
    "pantoprazole",
    "paracetamol",
]

FOOD_WORDS = [
    "khana",
    "breakfast",
    "lunch",
    "dinner",
    "diet",
    "bhookha",
    "fasting",
]

# Re-declared here to avoid broad substring matches from older demo constants.
YES_WORDS = ["yes", "haan", "ha", "li", "le li", "kha liya", "taken", "took it", "done", "ho gaya", "👍", "✓"]
NO_WORDS = ["no", "nahi", "nai", "nhi", "missed", "bhool gaya", "skip", "skipped", "nahi liya", "nhi li", "not taken", "👎"]
APPROVAL_CONTEXT_TYPES = {
    "veto_window",
    "interaction_alert",
    "new_med",
    "approval_window",
    "pending_approval",
}
APPROVE_COMMANDS = ["approve", "approve this", "yes approve", "confirm", "theek hai", "proceed"]
REJECT_COMMANDS = ["deny", "reject", "veto", "veto this", "cancel this", "nahi chahiye", "nai chahiye"]
MEDICATION_ACTION_REPLIES = YES_WORDS + NO_WORDS + ["took", "skipped", "missed"]
GREETING_PATTERNS = {
    "hi",
    "hello",
    "namaste",
    "help",
    "what can you do",
    "help chahiye",
    "who are you",
    "who r u",
    "who are u",
    "what are you",
    "carecircle kya hai",
}

MEDICATION_MISSED_PATTERNS = [
    "no medications",
    "no medication",
    "no medicine",
    "no tablets",
    "missed all",
    "skipped all",
    "nahi liya",
    "medicine nahi liya",
    "medications nahi liya",
    "medications skipped",
]

MEDICATION_TAKEN_PATTERNS = [
    "medicine liya",
    "medications liya",
    "tablet liya",
    "tablets liya",
    "took medicine",
    "took medications",
    "taken medicine",
    "taken medications",
]

ABUSE_KEYWORDS = [
    "fuck",
    "fucking",
    "shit",
    "bullshit",
    "asshole",
    "bitch",
    "bastard",
    "shut up",
    "idiot",
    "stupid",
    "madarchod",
    "bhenchod",
    "behenchod",
    "chutiya",
    "chutiye",
    "gandu",
    "harami",
    "kamina",
    "kamine",
]


def _fuzzy_match(keyword: str, text: str, threshold: int = 85) -> bool:
    """Return True if fuzzywuzzy partial_ratio >= threshold, or exact match."""
    try:
        from fuzzywuzzy import fuzz

        return keyword in text or fuzz.partial_ratio(keyword, text) >= threshold
    except ImportError:
        return keyword in text
    except Exception:
        return keyword in text


def normalize_message(message: str) -> str:
    """
    Deterministic text normalization for routing.
    Keeps the transformation transparent and conservative.
    """
    try:
        text = unicodedata.normalize("NFKC", str(message or "")).lower()
        text = text.replace("\u2019", "'").replace("\u2018", "'")
        text = re.sub(r"[^a-z0-9+%/:\s]", " ", text)
        text = re.sub(r"(.)\1{2,}", r"\1\1", text)
        text = re.sub(r"\s+", " ", text).strip()

        for source, target in NORMALIZATION_PHRASES.items():
            text = re.sub(rf"(?<![a-z0-9]){re.escape(source)}(?![a-z0-9])", target, text)

        tokens = []
        for token in text.split():
            normalized = NORMALIZATION_TOKEN_MAP.get(token, token)
            if normalized == token and len(token) >= 5:
                match = difflib.get_close_matches(token, FUZZY_NORMALIZATION_TERMS, n=1, cutoff=0.88)
                if match:
                    normalized = match[0]
            tokens.append(normalized)

        normalized_text = " ".join(tokens)
        for source, target in NORMALIZATION_PHRASES.items():
            normalized_text = re.sub(
                rf"(?<![a-z0-9]){re.escape(source)}(?![a-z0-9])",
                target,
                normalized_text,
            )
        return re.sub(r"\s+", " ", normalized_text).strip()
    except Exception:
        return str(message or "").lower().strip()


def _debug(intent_name: str, message: str) -> None:
    if DEBUG_INTENT:
        print(f"INTENT_DEBUG {intent_name} <- raw={message[:60]!r} normalized={normalize_message(message)[:80]!r}")


def _return(intent_name: str, message: str) -> str:
    _debug(intent_name, message)
    return intent_name


def _contains_any(text: str, keywords: list[str]) -> bool:
    return any(keyword.lower() in text for keyword in keywords)


def _contains_phrase_or_word(text: str, phrases: list[str]) -> bool:
    lowered = f" {text.lower().strip()} "
    for phrase in sorted(phrases, key=len, reverse=True):
        normalized = phrase.lower().strip()
        if " " in normalized:
            if normalized in lowered:
                return True
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", lowered):
            return True
    return False


def _is_approval_command_shape(text: str) -> str | None:
    lowered = text.lower().strip()
    if lowered in APPROVE_COMMANDS or lowered.startswith("approve "):
        return "approve_command"
    if lowered in REJECT_COMMANDS or lowered.startswith("veto ") or lowered.startswith("reject "):
        return "veto_command"
    if lowered.startswith("yes approve"):
        return "approve_command"
    return None


def _parse_context_time(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _approval_context_state(context: dict) -> str:
    try:
        if not context:
            return "missing"
        context_type = context.get("type")
        if context_type not in APPROVAL_CONTEXT_TYPES:
            return "missing"
        if context.get("active") is False or context.get("status") in {"done", "failed", "expired", "inactive"}:
            return "expired"

        expires_at = _parse_context_time(context.get("expires_at"))
        if expires_at is None:
            created_at = _parse_context_time(context.get("created_at") or context.get("asked_at"))
            if created_at is not None:
                try:
                    import config

                    expiry_seconds = getattr(config, "APPROVAL_CONTEXT_EXPIRY_SECONDS", 180)
                    expires_at = created_at + timedelta(seconds=expiry_seconds)
                except Exception:
                    expires_at = created_at + timedelta(seconds=180)

        if expires_at is not None and datetime.now(expires_at.tzinfo) > expires_at:
            return "expired"
        return "active"
    except Exception:
        return "missing"


def _is_medication_context(context: dict) -> bool:
    return (context or {}).get("type") in {"medication_confirmation", "medication_prompt", "scheduled_medication"}


CAREGIVER_OBSERVATION_SUBJECTS = [
    "dad",
    "papa",
    "uncle",
    "patient",
    "rajesh",
    "he ",
    "his ",
    "him ",
    "woh",
    "unhone",
    "papa ne",
    "uncle ne",
    "caregiver said",
]

CAREGIVER_OBSERVATION_SIGNALS = [
    "had",
    "was",
    "seemed",
    "looked",
    "did not",
    "didn't",
    "nahi",
    "took",
    "missed",
    "skipped",
    "ate",
    "eat",
    "breakfast",
    "lunch",
    "dinner",
    "dizziness",
    "dizzy",
    "chakkar",
    "weak",
    "confused",
    "fever",
    "bukhar",
    "pain",
    "dard",
    "medicines",
    "medicine",
    "tablets",
]
CAREGIVER_OBSERVATION_KEYWORDS = [
    "uncle had",
    "patient had",
    "dad had",
    "he had",
    "she had",
    "uncle ko",
    "patient ko",
    "dad ko",
    "uncle ne",
    "patient ne",
    "dad ne",
    "wo theek nahi lag rahe",
    "wo kamzor lag rahe",
    "wo confused hain",
    "wo zyada soye",
    "wo chal nahi pa rahe",
]
CAREGIVER_OBSERVATION_VERBS = [
    "dizziness",
    "fever",
    "weak",
    "confused",
    "fall",
    "gir",
    "soye",
    "sleep",
    "khana",
    "eat",
    "dawai",
    "medicine",
    "chakkar",
    "bukhar",
    "kamzori",
    "behosh",
    "walk",
    "chal",
]


def _is_caregiver_observation(text: str) -> bool:
    lowered = f" {text.lower().strip()} "
    if lowered.startswith(("what ", "which ", "when ", "how ", "kab ", "kaunsi ", "kya ")):
        return False
    if "?" in text:
        return False
    if _contains_any(lowered, CAREGIVER_OBSERVATION_KEYWORDS) and _contains_any(lowered, CAREGIVER_OBSERVATION_VERBS):
        return True
    has_subject = any(subject in lowered for subject in CAREGIVER_OBSERVATION_SUBJECTS)
    signal_count = sum(1 for signal in CAREGIVER_OBSERVATION_SIGNALS if signal in lowered)
    has_narrative_connector = any(
        connector in lowered
        for connector in [" and ", " but ", ",", " this morning", " today", " aaj ", " after ", " before ", " at night", " in the evening"]
    )
    return has_subject and signal_count >= 2 and has_narrative_connector


def _is_medication_action_reply(text: str) -> bool:
    lowered = text.lower().strip()
    if lowered.startswith(("what ", "which ", "when ", "how ", "kab ", "kaunsi ", "kya ")):
        return False
    symptom_terms = [
        "pain",
        "dard",
        "chest",
        "heart",
        "saans",
        "breath",
        "fever",
        "bukhar",
        "chakkar",
        "dizzy",
        "vomit",
        "ulti",
    ]
    medication_terms = ["medicine", "medication", "tablet", "pill", "dawai", "dose", "meds"]
    generic_no_words = ["no", "nahi", "nai", "nhi"]
    has_generic_no = _contains_phrase_or_word(lowered, generic_no_words)
    has_specific_medication_action = (
        lowered in MEDICATION_ACTION_REPLIES
        or _contains_any(lowered, MEDICATION_MISSED_PATTERNS)
        or _contains_any(lowered, MEDICATION_TAKEN_PATTERNS)
    )
    if has_generic_no and not has_specific_medication_action and not _contains_phrase_or_word(lowered, medication_terms):
        return False
    if (
        lowered not in MEDICATION_ACTION_REPLIES
        and _contains_phrase_or_word(lowered, symptom_terms)
        and not _contains_phrase_or_word(lowered, medication_terms)
    ):
        return False
    return (
        _contains_phrase_or_word(text, MEDICATION_ACTION_REPLIES)
        or _contains_any(lowered, MEDICATION_MISSED_PATTERNS)
        or _contains_any(lowered, MEDICATION_TAKEN_PATTERNS)
    )


def _is_greeting(text: str) -> bool:
    lowered = text.lower().strip()
    return lowered in GREETING_PATTERNS


def _contains_abuse(text: str) -> bool:
    padded = f" {text.lower()} "
    for keyword in ABUSE_KEYWORDS:
        if " " in keyword:
            if keyword in padded:
                return True
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", padded):
            return True
    return False


def _has_yes(text: str) -> bool:
    return _contains_phrase_or_word(text, YES_WORDS)


def _has_no(text: str) -> bool:
    return _contains_phrase_or_word(text, NO_WORDS)


def _context_value(context: dict, names: list[str]) -> str:
    for name in names:
        value = context.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _media_intent_from_filename(context: dict) -> str | None:
    filename = _context_value(context, ["filename", "file_name", "name", "attachment_name"])
    lowered = filename.lower()

    if lowered.endswith(IMAGE_EXTENSIONS):
        return "photo_upload"
    if lowered.endswith(AUDIO_EXTENSIONS):
        return "audio_note"
    if lowered.endswith(PDF_EXTENSIONS):
        return "pdf_upload"
    return None


def _has_negation_near_keyword(text: str, keywords: list[str], negations: list[str], window: int = 4) -> bool:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    if not tokens:
        return False

    for keyword in keywords:
        keyword_tokens = re.findall(r"[a-z0-9]+", keyword.lower())
        if not keyword_tokens:
            continue
        size = len(keyword_tokens)
        for index in range(0, len(tokens) - size + 1):
            if tokens[index:index + size] == keyword_tokens:
                start = max(0, index - window)
                end = min(len(tokens), index + size + window)
                if any(token in negations for token in tokens[start:end]):
                    return True
    return False


def is_valid_emergency(message: str) -> tuple[bool, str]:
    """
    Deterministic crisis detection.
    Returns (is_emergency, reason).
    """
    try:
        msg = normalize_message(message)
        if _is_approval_command_shape(msg) is not None:
            return (False, "command_word")

        non_human_death_context = ["phone died", "battery died", "laptop died", "app died"]
        if _contains_any(msg, non_human_death_context):
            return (False, "no_crisis_detected")

        explicit_emergency = [
            "ambulance",
            "help me",
            "bachao",
            "emergency",
            "call 112",
            "call ambulance",
            "urgent help",
            "jaldi help",
        ]
        negations = ["nahi", "not", "no", "without"]
        non_emergency_pain = [
            "knee pain",
            "headache",
            "sir dard",
            "pair dard",
            "back pain",
            "shoulder pain",
            "tooth pain",
        ]
        medical_critical_keywords = [
            "chest pain",
            "heart attack",
            "stroke",
            "brain stroke",
            "paralysis",
            "liver failure",
            "kidney failure",
            "organ failure",
            "unconscious",
            "collapse",
            "collapsed",
            "behosh",
            "not breathing",
            "cannot breathe",
            "breathing problem",
            "severe breathing problem",
            "saans nahi",
            "saans atak gayi",
            "dam ghut raha",
            "saans",
            "sans",
            "saan",
            "seene mein dard",
            "seene me dard",
            "seizure",
            "fit aa raha",
            "bleeding heavily",
            "heavy bleeding",
            "blood loss",
        ]
        death_phrases = [
            "died",
            "dead",
            "death",
            "passed away",
            "mar gaya",
            "maar gaya",
            "marr gaya",
            "gujar gaya",
            "death ho gaya",
        ]
        self_harm_phrases = [
            "kill myself",
            "suicide",
            "self harm",
            "marna chahta",
            "marna chahti",
            "jaan dena",
            "zehar",
            "poison",
            "i do not want my life",
            "dont want my life",
            "don't want my life",
            "bridge par se kood",
            "building par se kood",
            "chhat se kood",
            "train ke samne",
        ]
        violence_phrases = [
            "someone came to kill me",
            "someone kill me",
            "kill me",
            "kill my",
            "maar denge",
            "maar diya",
            "attack kar diya",
            "assault",
            "beaten",
            "pitai",
            "gun",
            "knife",
            "chaku",
            "threat",
            "danger",
        ]
        assault_phrases = [
            "rape",
            "sexual assault",
            "molest",
            "molested",
            "raped my",
            "rape kiya",
        ]

        if _contains_any(msg, explicit_emergency):
            return (True, "explicit_emergency")

        if _has_negation_near_keyword(msg, medical_critical_keywords, negations):
            return (False, "negated")

        if _contains_any(msg, non_emergency_pain):
            return (False, "pain_non_emergency")

        if _contains_any(msg, death_phrases):
            return (True, "death_report")

        if _contains_any(msg, assault_phrases):
            return (True, "sexual_assault")

        if _contains_any(msg, violence_phrases):
            return (True, "violence_threat")

        if _contains_any(msg, self_harm_phrases) or (
            "kood" in msg and _contains_any(msg, ["bridge", "building", "chhat", "roof"])
        ):
            return (True, "self_harm_risk")

        has_crisis_keyword = _contains_any(msg, medical_critical_keywords) or any(
            _fuzzy_match(keyword, msg, threshold=88) for keyword in medical_critical_keywords
        )
        has_present_marker = _contains_phrase_or_word(msg, PRESENT_MARKERS)
        has_past_marker = _contains_phrase_or_word(msg, PAST_MARKERS)

        if has_crisis_keyword and has_present_marker:
            return (True, "crisis_present")

        if has_crisis_keyword and has_past_marker:
            return (False, "crisis_historical")

        high_risk_short_phrases = [
            "heart attack",
            "stroke",
            "liver failure",
            "kidney failure",
            "organ failure",
            "unconscious",
            "collapse",
            "collapsed",
            "behosh",
            "not breathing",
            "cannot breathe",
            "chest pain",
        ]
        if _contains_any(msg, high_risk_short_phrases):
            return (True, "medical_emergency")

        return (False, "no_crisis_detected")
    except Exception:
        return (False, "detection_error")


def _is_crisis(message: str) -> bool:
    is_emergency, reason = is_valid_emergency(message)
    if is_emergency:
        return True
    if reason in {"crisis_historical", "negated", "pain_non_emergency", "detection_error"}:
        return False

    # The public helper stays strict, but routing is fail-safe for short,
    # high-risk phrases that users often send without temporal markers.
    fail_safe_short_phrases = [
        "chest pain",
        "heart attack",
        "unconscious",
        "collapse",
        "behosh",
        "not breathing",
        "severe breathing problem",
    ]
    return _contains_any(normalize_message(message), fail_safe_short_phrases)


# FUTURE HINGLISH EMERGENCY PHRASES TO REVIEW BEFORE ENABLING:
# "saansein atak gayi", "saans phool rahi hai", "dam ghut raha hai",
# "hawa nahi aa rahi", "jaan ja rahi hai", "bahut tez seene ka dard",
# "patient gir gaye", "hosh nahi hai", "pulse nahi mil raha",
# "body thandi pad rahi hai"


def _has_date_or_time(text: str) -> bool:
    day_words = [
        "kal",
        "parso",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
        "somwar",
        "mangalwar",
        "budhwar",
        "guruvar",
        "shukrawar",
        "shanivar",
        "ravivar",
        "tarikh",
        "aaj",
        "tomorrow",
        "today",
        "jan",
        "january",
        "feb",
        "february",
        "mar",
        "march",
        "apr",
        "april",
        "may",
        "jun",
        "june",
        "jul",
        "july",
        "aug",
        "august",
        "sep",
        "sept",
        "september",
        "oct",
        "october",
        "nov",
        "november",
        "dec",
        "december",
    ]
    time_pattern = r"\b\d{1,2}(:\d{2})?\s*(am|pm|baje)?\b"
    ordinal_date_pattern = r"\b\d{1,2}(st|nd|rd|th)?\s+(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december)\b"
    return (
        _contains_any(text, day_words)
        or re.search(time_pattern, text) is not None
        or re.search(ordinal_date_pattern, text) is not None
    )


def _looks_like_appointment_add_statement(text: str) -> bool:
    """
    Detect appointment creation statements without a perfect command phrase.
    Example: "cardiology appointment on 30th may 2026" should ask for time,
    not query existing appointments.
    """
    lowered = normalize_message(text)
    if not _contains_phrase_or_word(lowered, ["appointment", "checkup", "follow up", "follow-up", "visit"]):
        return False
    if not _has_date_or_time(lowered):
        return False
    query_markers = [
        "when",
        "kab",
        "show",
        "list",
        "any",
        "what",
        "does",
        "is there",
        "hai kya",
        "bhejo",
        "date bhejo",
    ]
    if "?" in text or _contains_any(lowered, query_markers):
        return False
    creation_markers = [
        "create",
        "add",
        "book",
        "schedule",
        "fix",
        "karna",
        "karo",
        "le jaana",
        "bulaya",
        "on",
        "ko",
    ]
    specialty_markers = [
        "cardiology",
        "cardiologist",
        "nephrologist",
        "kidney specialist",
        "endocrinologist",
        "neurologist",
        "psychiatrist",
        "dentist",
        "doctor",
        "dr",
    ]
    return _contains_any(lowered, creation_markers) or _contains_any(lowered, specialty_markers)


def _is_vital_report(text: str) -> bool:
    lab_panel_markers = [
        "hba1c",
        "cholesterol",
        "creatinine",
        "lft",
        "kft",
        "cbc",
        "tsh",
        "lab report",
        "blood test",
    ]
    if _contains_any(text, lab_panel_markers):
        return False

    bp_pattern = r"\b(bp|blood pressure)?\s*[:=-]?\s*\d{2,3}/\d{2,3}\b"
    glucose_pattern = r"\b(sugar|glucose|rbs|fbs)\s*[:=-]?\s*\d{2,3}(\.\d+)?\s*(mg/dl)?\b"
    mgdl_pattern = r"\b\d{2,3}(\.\d+)?\s*mg/dl\b"
    pulse_pattern = r"\b(pulse|heart rate|hr)?\s*[:=-]?\s*\d{2,3}\s*bpm\b"
    oxygen_pattern = r"\b(spo2|oxygen)\s*[:=-]?\s*\d{2,3}\s*%?\b"
    temperature_pattern = r"\b(temperature|temp|bukhar|fever)\b.*\d{2,3}(\.\d+)?"
    reverse_temperature_pattern = r"\b\d{2,3}(\.\d+)?\s*(f|c|°f|°c)?\b.*\b(temperature|temp|bukhar|fever)\b"

    if re.search(bp_pattern, text):
        return True
    if re.search(glucose_pattern, text) or re.search(mgdl_pattern, text):
        return True
    if re.search(pulse_pattern, text):
        return True
    if re.search(oxygen_pattern, text):
        return True
    if re.search(temperature_pattern, text) or re.search(reverse_temperature_pattern, text):
        return True

    keyword_only = ["bp", "pulse", "sugar", "oxygen"]
    if _contains_any(text, keyword_only) and ("reading" in text or re.search(r"\d", text)):
        return True
    if "reading" in text and _contains_any(text, keyword_only):
        return True

    return False


def _has_fuzzy_drug_reference(text: str, threshold: int = 86) -> bool:
    if not text.strip():
        return False

    try:
        from fuzzywuzzy import fuzz

        words = re.findall(r"[a-zA-Z]{4,}", text)
        candidates = set(words)
        candidates.update(" ".join(words[index:index + 2]) for index in range(max(len(words) - 1, 0)))

        for drug in KNOWN_DRUGS:
            if drug in text:
                return True
            if any(fuzz.partial_ratio(drug, candidate.lower()) >= threshold for candidate in candidates):
                return True
    except ImportError:
        words = re.findall(r"[a-zA-Z]{4,}", text)
        for drug in KNOWN_DRUGS:
            if drug in text:
                return True
            if any(difflib.SequenceMatcher(None, drug, word.lower()).ratio() >= threshold / 100 for word in words):
                return True
    except Exception:
        return any(drug in text for drug in KNOWN_DRUGS)

    return False


def _is_medication_query(text: str, medication_query_keywords: list[str]) -> bool:
    if _contains_any(text, ["show current tablets", "list my tablets", "current medication list"]):
        return False
    if _contains_any(text, medication_query_keywords):
        return True

    query_cues = [
        "dose",
        "dosage",
        "kab",
        "kaise",
        "lena",
        "leni",
        "kitna",
        "kitni",
        "how",
        "when",
        "before food",
        "after food",
        "khane",
        "pehle",
        "baad",
        "?",
    ]
    return _has_fuzzy_drug_reference(text) and _contains_any(text, query_cues)


def _is_caregiver_update(text: str, caregiver_verbs: list[str]) -> bool:
    if not _contains_any(text, caregiver_verbs):
        return False

    collision_words = FOOD_WORDS + [
        "neend",
        "sleep",
        "mood",
        "walk",
        "exercise",
        "dawai",
        "medicine",
        "appointment",
        "doctor",
        "hospital",
    ]
    return not _contains_any(text, collision_words)


def _split_compound_clauses(message: str) -> list[str]:
    try:
        text = str(message or "").strip()
        normalized = normalize_message(text)
        pieces = re.split(r"\s*(?:,|;|\band\b|\balso\b|\baur\b|\bplus\b)\s*", text, flags=re.IGNORECASE)
        candidate_clauses = [piece.strip(" .!?") for piece in pieces if piece.strip(" .!?")]
        if len(normalized.split()) <= 10 and len(candidate_clauses) < 3:
            return []
        raw_lower = f" {text.lower()} "
        if not (
            any(marker in normalized for marker in [" and ", " also ", " aur ", " plus "])
            or "," in text
            or ";" in text
            or any(marker in raw_lower for marker in [" and ", " also ", " aur ", " plus "])
        ):
            return []
        clauses = [piece for piece in candidate_clauses if len(piece.split()) >= 2]
        return clauses[:5]
    except Exception:
        return []


def _classify_single_clause(message: str, pending_context: dict | None = None) -> str:
    return classify_intent(message, pending_context)


def _secondary_intents_for_compound(
    message: str,
    pending_context: dict | None,
    primary_intent: str,
) -> list[dict]:
    try:
        clauses = _split_compound_clauses(message)
        if len(clauses) < 2:
            return []
        secondary: list[dict] = []
        seen = {primary_intent}
        for clause in clauses:
            intent_name = _classify_single_clause(clause, pending_context)
            if intent_name in {"unknown", "greeting"} or intent_name in seen:
                continue
            confidence, reason = _confidence_for_intent(intent_name, clause, pending_context)
            secondary.append(
                {
                    "intent": intent_name,
                    "confidence": round(max(0.0, min(1.0, float(confidence))), 2),
                    "reason": reason,
                    "clause": clause,
                    "normalized_text": normalize_message(clause),
                }
            )
            seen.add(intent_name)
        return secondary
    except Exception:
        return []


def classify_intent(message: str, pending_context: dict | None = None) -> str:
    try:
        if message is None:
            message = ""

        text = str(message)
        msg_lower = normalize_message(text)
        stripped = text.strip()
        context = pending_context or {}
        context_type = context.get("type")
        media_type = context.get("media_type")

        if isinstance(media_type, str) and media_type.startswith("image/"):
            return _return("photo_upload", text)

        if isinstance(media_type, str) and media_type.startswith("audio/"):
            return _return("audio_note", text)

        if media_type == "application/pdf":
            return _return("pdf_upload", text)

        filename_intent = _media_intent_from_filename(context)
        if filename_intent is not None:
            return _return(filename_intent, text)

        if context.get("latitude") is not None or "maps.google" in msg_lower or "geo:" in msg_lower:
            return _return("location_update", text)

        if _is_crisis(text):
            return _return("crisis", text)

        approval_intent = _is_approval_command_shape(text)
        if approval_intent is not None:
            approval_state = _approval_context_state(context)
            if approval_state == "active":
                return _return(approval_intent, text)
            if approval_state == "expired":
                return _return("approval_context_expired", text)
            return _return("approval_context_missing", text)

        if _contains_abuse(msg_lower):
            return _return("abusive_language", text)

        if context_type == "appointment_draft" and _has_date_or_time(msg_lower):
            return _return("appointment_add", text)

        if context_type == "appointment_confirm" and _has_yes(msg_lower):
            return _return("appointment_confirmed", text)

        if context_type == "appointment_confirm" and _has_no(msg_lower):
            return _return("appointment_declined", text)

        if (
            _is_medication_context(context)
            and _has_yes(msg_lower)
            and not _has_no(msg_lower)
            and _contains_any(msg_lower, DEVIATION_KEYWORDS)
        ):
            return _return("medication_taken_incorrect", text)

        if _is_medication_context(context) and _has_yes(msg_lower) and not _has_no(msg_lower):
            return _return("medication_taken_confirm", text)

        if _is_medication_context(context) and _has_no(msg_lower):
            return _return("medication_missed_confirm", text)

        if _is_caregiver_observation(msg_lower):
            return _return("caregiver_observation", text)

        if _is_medication_action_reply(msg_lower):
            return _return("medication_report", text)

        if context_type == "clarification_question":
            return _return("clarification_response", text)

        if context_type == "refill_confirm" and _has_yes(msg_lower):
            return _return("refill_confirmed", text)

        if context_type == "refill_confirm" and _has_no(msg_lower):
            return _return("refill_declined", text)

        if _is_vital_report(msg_lower):
            return _return("vital_report", text)

        lab_keywords = [
            "lab report",
            "blood test",
            "hba1c",
            "cholesterol",
            "creatinine",
            "lft",
            "kft",
            "cbc",
            "tsh",
            "report aaya",
            "results",
        ]
        if _contains_any(msg_lower, lab_keywords) and re.search(r"\d", msg_lower):
            return _return("lab_report", text)

        new_prescription_keywords = [
            "prescribed",
            "doctor ne diya",
            "naya tablet",
            "nai dawai",
            "dosage changed",
            "dose badh gaya",
            "started on",
            "band kar di",
            "discontinued",
            "medicine changed",
        ]
        if _contains_any(msg_lower, new_prescription_keywords):
            return _return("new_prescription", text)

        discharge_keywords = [
            "discharge",
            "hospital se aaye",
            "discharged",
            "admitted",
            "admit hua",
            "released from hospital",
            "discharge summary",
        ]
        if _contains_any(msg_lower, discharge_keywords):
            return _return("discharge_summary", text)

        appointment_add_keywords = [
            "create appointment",
            "create cardiology appointment",
            "create doctor appointment",
            "create checkup",
            "book appointment",
            "book cardiology appointment",
            "book doctor appointment",
            "book karo",
            "book kar",
            "add appointment",
            "please add doctor visit",
            "add doctor visit",
            "schedule appointment",
            "schedule checkup",
            "schedule cardiology",
            "schedule tele consult",
            "schedule tele-consult",
            "appointment fix",
            "appointment fix karna",
            "appointment book karo",
            "appointment book",
            "appointment liya",
            "appointment fix kiya",
            "booking ki",
            "schedule kiya",
            "le jaana hai",
            "bulaya hai",
        ]
        if (_contains_any(msg_lower, appointment_add_keywords) and _has_date_or_time(msg_lower)) or _looks_like_appointment_add_statement(text):
            return _return("appointment_add", text)

        appointment_query_keywords = [
            "appointment",
            "next appointment",
            "next visit",
            "doctor kab",
            "appointment kab hai",
            "schedule",
            "kabka appointment",
            "kab jaana hai",
            "when is the appointment",
            "doctor visit",
            "follow-up",
            "follow up",
            "checkup",
            "next checkup",
            "doctor ke paas",
            "doctor ka appointment",
            "doctor appointment",
            "appointments this week",
            "all upcoming doctor visits",
            "appointment ki date",
            "upcoming checkup",
        ]
        if _contains_any(msg_lower, appointment_query_keywords):
            return _return("appointment_query", text)

        medication_query_keywords = [
            "kab lena hai",
            "kitni dose",
            "dose kya hai",
            "kab tak lena hai",
            "kitne din",
            "instructions",
            "how to take",
            "khana ke pehle ya baad",
            "before or after food",
            "what should i take now",
            "what medicine now",
            "current tablet",
            "which tablet now",
            "present tablet",
            "present tablets",
            "medicines due now",
            "medicine due now",
            "which medicine is due now",
            "what medicine at this time",
            "what is my present tablets to be taken",
            "tablets to be taken",
            "take now",
            "due now",
        ]
        if _is_medication_query(msg_lower, medication_query_keywords):
            return _return("medication_query", text)

        refill_request_keywords = [
            "dawai khatam",
            "medicine khatam ho gayi",
            "stock low",
            "refill",
            "khatam hone wali hai",
            "order karna hai",
            "pharmacy",
            "chemist",
            "nayi dawai lani hai",
        ]
        if _contains_any(msg_lower, refill_request_keywords):
            return _return("refill_request", text)

        status_query_keywords = [
            "meds",
            "medication",
            "dawai",
            "kya le raha",
            "what is dad on",
            "what medicines am i on",
            "what medicines am i taking",
            "what medicine am i on",
            "what medicine am i taking",
            "what meds are active",
            "list",
            "list my tablets",
            "medicine list",
            "tablet list",
            "currently on",
            "active medicines",
            "active medication",
            "current medication list",
            "show current tablets",
            "dawai batao",
            "kaunsi dawai",
        ]
        if _contains_any(msg_lower, status_query_keywords):
            return _return("status_query", text)

        doctor_visit_keywords = [
            "doctor se mile",
            "doctor visit hua",
            "clinic gaye",
            "hospital gaye",
            "appointment hua",
            "doctor bole",
            "doctor ne kaha",
            "checkup hua",
            "cardiologist se mile",
        ]
        if _contains_any(msg_lower, doctor_visit_keywords) and not _contains_any(msg_lower, new_prescription_keywords):
            return _return("doctor_visit_update", text)

        symptom_keywords = [
            "dard",
            "dardh",
            "dardd",
            "pain",
            "headache",
            "head ache",
            "sir dard",
            "sar dard",
            "body ache",
            "dizzy",
            "chakkar",
            "chakar",
            "nausea",
            "ulti",
            "vomiting",
            "swelling",
            "sojan",
            "weak",
            "kamzori",
            "thakaan",
            "tired",
            "breathless",
            "saans",
            "saan",
            "sans",
            "saas",
            "constipation",
            "diarrhea",
            "loose motion",
            "rash",
            "khujli",
            "fever",
            "bukhar",
            "chest pain",
            "seene mein dard",
            "seene me dard",
        ]
        crisis_like_symptoms = [
            "dard",
            "dardh",
            "dardd",
            "pain",
            "chest pain",
            "seene mein dard",
            "seene me dard",
            "saans",
            "saan",
            "sans",
            "saas",
            "breathless",
        ]
        has_symptom = _contains_any(msg_lower, symptom_keywords)
        has_present = _contains_any(msg_lower, PRESENT_MARKERS)
        has_crisis_like_symptom = _contains_any(msg_lower, crisis_like_symptoms)
        if has_symptom and not (has_crisis_like_symptom and has_present):
            return _return("symptom_report", text)

        diet_keywords = [
            "khana khaya",
            "breakfast",
            "lunch",
            "dinner",
            "kha liya",
            "nahi khaya",
            "diet",
            "bhookha",
            "fasting",
            "fast kar raha",
            "khana skip kiya",
        ]
        if _contains_any(msg_lower, diet_keywords):
            return _return("diet_report", text)

        sleep_keywords = [
            "neend",
            "soya",
            "sleeping",
            "neend nahi aayi",
            "raat ko jaaga",
            "uthh gaye",
            "insomnia",
            "raat bhar",
        ]
        if _contains_any(msg_lower, sleep_keywords):
            return _return("sleep_report", text)

        emotional_checkin_keywords = [
            "feeling tensed",
            "feeling anxious",
            "heart break",
            "i am worried",
            "feeling low today",
            "not feeling good mentally",
            "feeling stressed",
            "i am upset",
            "feeling lonely",
            "i miss her",
            "i miss him",
            "feeling empty",
            "not in a good mood",
            "feeling down",
            "mai udas hoon",
            "tension ho rahi hai",
            "dil toot gaya",
            "pareshan hoon",
            "mann nahi lag raha",
            "akela feel kar raha hoon",
            "bechaini ho rahi hai",
            "mood off hai",
            "depressed feel kar raha hoon",
            "stress mein hoon",
        ]
        if _contains_any(msg_lower, emotional_checkin_keywords):
            return _return("emotional_checkin", text)

        mood_keywords = [
            "mood",
            "khush",
            "upset",
            "sad",
            "roya",
            "dara hua",
            "anxious",
            "confused",
            "irritated",
            "gussa",
            "depressed",
            "pareshan",
            "ghabra raha",
        ]
        if _contains_any(msg_lower, mood_keywords):
            return _return("mood_report", text)

        exercise_keywords = [
            "walk",
            "exercise",
            "chala",
            "gaya bahar",
            "workout",
            "stairs",
            "chalte hain",
            "physical activity",
        ]
        if _contains_any(msg_lower, exercise_keywords):
            return _return("exercise_report", text)

        caregiver_schedule_keywords = [
            "nahi aa paunga",
            "nahi aa paungi",
            "chutti",
            "leave",
            "absent",
            "late aaunga",
            "replace",
            "backup",
            "koi aur aayega",
            "available nahi",
        ]
        if _contains_any(msg_lower, caregiver_schedule_keywords):
            return _return("caregiver_schedule_update", text)

        caregiver_observation_keywords = [
            "uncle had",
            "patient had",
            "dad had",
            "he had",
            "she had",
            "uncle ko",
            "patient ko",
            "dad ko",
            "uncle ne",
            "patient ne",
            "dad ne",
            "wo theek nahi lag rahe",
            "wo kamzor lag rahe",
            "wo confused hain",
            "wo zyada soye",
            "wo chal nahi pa rahe",
        ]
        caregiver_observation_verbs = [
            "dizziness",
            "fever",
            "weak",
            "confused",
            "fall",
            "gir",
            "soye",
            "sleep",
            "khana",
            "eat",
            "dawai",
            "medicine",
            "chakkar",
            "bukhar",
            "kamzori",
            "behosh",
            "walk",
            "chal",
        ]
        if _contains_any(msg_lower, caregiver_observation_keywords) and _contains_any(msg_lower, caregiver_observation_verbs):
            return _return("caregiver_observation", text)

        family_member_query_keywords = [
            "how is dad",
            "kaisa hai",
            "kya hua",
            "update do",
            "sab theek",
            "dad kaise hain",
            "patient kaisa hai",
            "health update",
            "any change",
        ]
        if _contains_any(msg_lower, family_member_query_keywords) and len(stripped.split()) <= 8:
            return _return("family_member_query", text)

        caregiver_verbs = ["gaye", "aaye", "kiya", "hua", "bataya"]
        if _is_caregiver_update(msg_lower, caregiver_verbs):
            return _return("caregiver_update", text)

        if _is_greeting(msg_lower):
            return _return("greeting", text)

        return _return("unknown", text)
    except Exception:
        return "unknown"


def _confidence_for_intent(intent_name: str, message: str, pending_context: dict | None = None) -> tuple[float, str]:
    try:
        normalized = normalize_message(message)
        context = pending_context or {}
        if intent_name == "crisis":
            is_valid, reason = is_valid_emergency(message)
            return (1.0 if is_valid else 0.92, f"safety_gate:{reason}")
        if intent_name in {"approve_command", "veto_command"}:
            return (0.95, "command_gate:active_context")
        if intent_name in {"approval_context_missing", "approval_context_expired"}:
            return (0.9, f"command_gate:{intent_name}")
        if intent_name in {
            "medication_taken_incorrect",
            "medication_taken_confirm",
            "medication_missed_confirm",
        }:
            return (0.93 if _is_medication_context(context) else 0.8, "medication_context:pending_prompt")
        if intent_name == "medication_report":
            return (0.86 if _is_medication_action_reply(normalized) else 0.72, "medication_action:normalized_match")
        if intent_name == "caregiver_observation":
            return (0.88, "caregiver_observation:third_person_narrative")
        if intent_name == "medication_query":
            return (0.86, "medication_query:weighted_phrase")
        if intent_name == "status_query":
            return (0.86, "status_query:weighted_phrase")
        if intent_name == "emotional_checkin":
            return (0.78, "emotional_checkin:keyword_match")
        if intent_name == "greeting":
            return (0.9, "greeting:narrow_pattern")
        if intent_name in {"appointment_query", "appointment_add"}:
            return (0.84, "appointment:phrase_match")
        if intent_name in {"photo_upload", "audio_note", "pdf_upload", "location_update"}:
            return (0.95, "media_or_location:context")
        if intent_name in {"vital_report", "lab_report"}:
            return (0.92, "structured_medical_data:regex")
        if intent_name == "abusive_language":
            return (0.9, "abuse:keyword_boundary")
        if intent_name == "unknown":
            return (0.35, "fallback:no_rule_matched")
        return (0.78, f"deterministic:{intent_name}")
    except Exception:
        return (0.0, "classifier:confidence_error")


def classify_intent_with_confidence(message: str, pending_context: dict | None = None) -> dict:
    try:
        intent_name = classify_intent(message, pending_context)
        confidence, reason = _confidence_for_intent(intent_name, message, pending_context)
        secondary_intents = _secondary_intents_for_compound(message, pending_context, intent_name)
        return {
            "intent": intent_name,
            "confidence": round(max(0.0, min(1.0, float(confidence))), 2),
            "reason": reason,
            "normalized_text": normalize_message(message),
            "secondary_intents": secondary_intents,
        }
    except Exception:
        return {
            "intent": "unknown",
            "confidence": 0.0,
            "reason": "classifier:error",
            "normalized_text": normalize_message(message),
            "secondary_intents": [],
        }
