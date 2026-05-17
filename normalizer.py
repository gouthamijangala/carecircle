import re
import unicodedata

import config


# Audit research notes:
# Recent audit_log entries mainly showed repeated emergency triggers such as
# "heart attack", "emergency", "ambulance", and "chest pain abhi". The project
# config also highlights Hinglish medication replies ("nhi li"), crisis terms
# ("seene mein dard"), SMS shorthand, and predictable split typos.
#
# Implemented audit/research patterns:
# - Crisis variants: saans/sans/saansein, dardh, heart atack.
# - Medication replies: le liya, nahi khai, bhool gya, what med ... to take.
# - Appointment/planning: appmnt, followup, check up.
# - Conversational typos: heloo -> hello, ot -> to.


LOCAL_PHRASE_REPAIRS = {
    "heart atack": "heart attack",
    "hart attack": "heart attack",
    "heartattacke": "heart attack",
    "heartattack": "heart attack",
    "chestoain": "chest pain",
    "chestpain": "chest pain",
    "chest painn": "chest pain",
    "chest paon": "chest pain",
    "chest pan": "chest pain",
    "heart fail": "heart failure",
    "heart pain": "heart pain",
    "ambu lance": "ambulance",
    "call ambu": "call ambulance",
    "saansein atak gayi": "breathing stuck",
    "saanse atak gayi": "breathing stuck",
    "saans ruk rahi": "breathing stopping",
    "saans nahi aa rahi": "breathing not coming",
    "mai build pe sey kudh liya": "i jumped from building",
    "building se kuda": "jumped from building",
    "fell from building": "fell from building",
    "fell from a building": "fell from a building",
    "some one raped": "someone raped",
    "koi ghar me ghus aaya": "koi ghar mein ghus aaya",
    "le liya": "le li",
    "nahi khai": "nahi li",
    "nhi khai": "nahi li",
    "nai li": "nahi li",
    "bhool gya": "bhool gaya",
    "bhul gaya": "bhool gaya",
    "skip kar diya": "skip kar di",
    "no medication all today": "no medications today",
    "no medicines all today": "no medications today",
    "what med now": "what medicine now",
    "what med to afternoon": "what medicine for afternoon",
    "what med now i want ot take": "what medicine now i want to take",
    "what meds now i should take": "what medicines now i should take",
    "present tablet": "present medicine",
    "present tablets": "present medicines",
    "appmnt": "appointment",
    "followup": "follow up",
    "check up": "checkup",
    "heloo": "hello",
    "helloo": "hello",
    "who r u": "who are you",
    "who are u": "who are you",
    "who you are": "who are you",
    "i dont want my life": "i do not want my life",
    "i don't want my life": "i do not want my life",
}


LOCAL_TOKEN_REPAIRS = {
    "saansein": "saans",
    "saanse": "saans",
    "sans": "saans",
    "saan": "saans",
    "dardh": "dard",
    "drd": "dard",
    "doctar": "doctor",
    "docotr": "doctor",
    "medicne": "medicine",
    "medicin": "medicine",
    "meds": "medicine",
    "tabs": "medicine",
    "tablt": "tablet",
    "appoinment": "appointment",
    "appointmnt": "appointment",
    "appt": "appointment",
    "rprt": "report",
    "suger": "sugar",
    "bukharh": "bukhar",
    "chakar": "chakkar",
    "chkr": "chakkar",
    "udas": "sad",
    "tensed": "anxious",
    "ot": "to",
}


NEGATED_MEDICATION_TOKENS = {"li", "khai", "kha", "taken"}


def _collapse_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _clean_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    normalized = normalized.lower()
    normalized = re.sub(r"(.)\1{2,}", r"\1\1", normalized)
    normalized = re.sub(r"[^\w\s]", " ", normalized, flags=re.UNICODE)
    return _collapse_spaces(normalized)


def _apply_phrase_replacements(text: str, replacements: dict[str, str]) -> str:
    result = text
    for source, target in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        source_text = str(source).lower().strip()
        target_text = str(target).lower().strip()
        if not source_text:
            continue
        pattern = r"(?<!\w)" + re.escape(source_text) + r"(?!\w)"
        result = re.sub(pattern, target_text, result)
    return _collapse_spaces(result)


def _expand_tokens(text: str) -> str:
    tokens = text.split()
    expanded: list[str] = []
    aliases = getattr(config, "NORMALIZATION_ALIASES", {})
    shorthand = getattr(config, "SMS_SHORTHAND", {})

    for token in tokens:
        replacement = shorthand.get(token, token)
        replacement = LOCAL_TOKEN_REPAIRS.get(replacement, replacement)
        previous = expanded[-1] if expanded else ""

        # Keep negated medication replies readable for deterministic medication-report logic.
        if replacement in NEGATED_MEDICATION_TOKENS and previous in {"nahi", "nai", "no", "not"}:
            expanded.append(replacement)
            continue

        expanded.append(aliases.get(replacement, replacement))

    return _collapse_spaces(" ".join(expanded))


def normalize(text: str) -> str:
    """
    Clean and normalize incoming WhatsApp text.
    - Lowercase, strip extra whitespace, remove punctuation noise
    - Expand Hinglish aliases using config.NORMALIZATION_ALIASES
    - Repair common split typos using config.SPLIT_TYPO_REPAIRS
    - Expand SMS shorthand using config.SMS_SHORTHAND
    - Edge cases: empty string, only spaces, numbers only -> return empty string
    - Never raise; on error return original text unchanged.
    """
    original_text = text
    try:
        if not isinstance(text, str):
            return original_text

        cleaned = _clean_text(text)
        if not cleaned or not re.search(r"[a-zA-Z]", cleaned):
            return ""

        cleaned = _apply_phrase_replacements(cleaned, LOCAL_PHRASE_REPAIRS)

        multi_word_aliases = {
            key: value
            for key, value in getattr(config, "NORMALIZATION_ALIASES", {}).items()
            if " " in str(key).strip()
        }
        cleaned = _apply_phrase_replacements(cleaned, multi_word_aliases)
        cleaned = _expand_tokens(cleaned)
        cleaned = _apply_phrase_replacements(cleaned, getattr(config, "SPLIT_TYPO_REPAIRS", {}))
        cleaned = _apply_phrase_replacements(cleaned, LOCAL_PHRASE_REPAIRS)

        if not cleaned or not re.search(r"[a-zA-Z]", cleaned):
            return ""
        return cleaned
    except Exception:
        return original_text


# APPROVED ENHANCEMENTS (commented-out for future activation):
# 1. Phonetic matching for Hinglish: Use jellyfish.metaphone() to match "dard"/"dardh".
# 2. Aho-Corasick automaton for hard gate: Build once at startup for O(n) crisis scanning.
# 3. Centroid auto-refresh: Weekly job to recompute centroids from confirmed interactions.
# 4. Per-user personalisation: Store user-specific alias expansions in profiles.preferences jsonb.
# 5. Fallback to Krutrim Vyakyarth model: If MiniLM confidence < 0.5, try Vyakyarth.
# 6. Async embedding pre-computation: Pre-embed common phrases at idle time.
# 7. Audit-based seed expansion: Monthly job to add high-confidence misclassifications to seed bank.
