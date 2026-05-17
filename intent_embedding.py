import difflib
import hashlib
import json
from pathlib import Path

import config
from normalizer import normalize


EMBEDDING_AVAILABLE = False
_model = None
_hard_gate_cache = {}  # LRU cache for recent messages
_centroids = None
_intent_order = None
_seed_hash = None
UNCLEAR_INTENT = "unclear"


# Research notes from audit_log:
# - Confirmed repeated crisis triggers: "chest pain abhi", "heart attack",
#   "emergency", and "ambulance".
# - Older rows showed likely missed/edge crisis wording: "heart fail",
#   "heart pain", "fell from a building", and "i will be killed by meera".
# - False-positive risk: avoid broad standalone words like "help" and phrases
#   like "heart break"; these belong to greeting/emotional support unless paired
#   with a stronger danger signal.
#
# Centroid seed coverage notes:
# - The seed map follows Context/Cluster_design.md: crisis, medication, health
#   status, care management, commands, and greeting/help.
# - `unclear` is represented as a final neutral zero-vector centroid. This keeps
#   intent_order.json aligned with the fallback cluster without training the
#   model to force-match vague text.
# - Thresholds live in config.py so crisis/command clusters can stay stricter
#   than conversational and medication-report clusters.


TIER1_CRISIS_MEDICAL = [
    "chest pain",
    "heart attack",
    "heart failure",
    "liver failure",
    "dil ka daura",
    "emergency",
    "ambulance",
    "saans nahi",
    "seene mein dard",
    "dad collapsed",
    "uncle behosh ho gaya",
    "not breathing",
    "breathing stuck",
    "breathing stopping",
    "dam ghut raha hai",
    "saansein atak gayi",
    "patient collapsed",
    "rajesh behosh hai",
    "heart attack aa gaya",
    "fell from a building",
    "fell from building",
]
TIER1_CRISIS_MEDICAL_FALSE_POSITIVES = [
    "heart fail",
]

TIER2_CRISIS_SAFETY = [
    "someone attacked me",
    "koi maar raha hai",
    "rape",
    "mujhe maar raha hai",
    "caregiver ne maara",
    "koi ghar mein ghus aaya",
    "i will be killed",
    "someone came to kill me",
    "kill me",
    "sexual assault",
    "someone raped",
    "someone raped my",
    "koi peeche pada hai",
    "someone following me",
    "koi marne aaya",
    "threat to life",
]

TIER3_CRISIS_SELF_HARM = [
    "kud kushi",
    "building se kuda",
    "mai building se kud",
    "mai build pe sey kudh liya",
    "i want to die",
    "main nahi rehna chahta",
    "i do not want my life",
    "i dont want my life",
    "i don't want my life",
    "i do not want to live",
    "i dont want to live",
    "dad ne kuch kha liya",
    "i can't take this anymore",
    "i cant take this anymore",
    "suicide",
    "khud ko hurt",
    "khudkushi",
    "i jumped from building",
    "jumped from building",
    "kudh liya",
    "maine zeher kha liya",
    "poison kha liya",
    "sab khatam karna chahta",
    "bahut thak gaya hoon sab se",
]

TIER4_CRISIS_DEATH = [
    "passed away",
    "died",
    "mar gaye",
    "intqaal ho gaya",
    "dad died",
    "funeral",
    "no more",
    "patient expired",
    "i am dead",
    "patient died",
    "patient no more",
    "rajesh nahi rahe",
    "body",
    "antim sanskar",
]

DEATH_FALSE_POSITIVE_CONTEXT = [
    "kutta",
    "kutha",
    "dog",
    "cat",
    "pet",
    "mera dog",
    "meri cat",
]

DEATH_PERSON_CONTEXT = [
    "dad",
    "papa",
    "patient",
    "rajesh",
    "uncle",
    "aunty",
    "mother",
    "father",
    "caregiver",
    "meera",
    "rani",
    "i am",
    "main",
    "mai",
]

TIER3_EMOTIONAL_CHECKIN = [
    # English
    "i am sad",
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
    # Hindi-romanized
    "mai udas hoon",
    "tension ho rahi hai",
    "dil toot gaya",
    "pareshan hoon",
    "mann nahi lag raha",
    "mai thak gaya hoon",
    "akela feel kar raha hoon",
    "kuch acha nahi lag raha",
    "dil udas hai",
    "bechaini ho rahi hai",
    # Hinglish mixed
    "feeling sad hoon",
    "tension bahut hai",
    "dil tut gaya hai",
    "ajeeb sa lag raha hai",
    "kuch acha nahi lag raha",
    "mood off hai",
    "depressed feel kar raha hoon",
    "stress mein hoon",
    "lonely feel ho raha hai",
    "miss kar raha hoon",
]

TIER4_CAREGIVER_OBSERVATION = [
    # English
    "uncle had dizziness this morning",
    "patient did not eat breakfast",
    "dad slept whole day",
    "he was walking fine today",
    "patient looks weak today",
    "he refused medicine today",
    "dad was confused in the morning",
    "patient had fever last night",
    "he was breathing heavily",
    "uncle fell in the bathroom",
    # Hindi-romanized
    "uncle ko subah chakkar aaye",
    "patient ne breakfast nahi khaya",
    "dad poora din soye rahe",
    "wo thak gaye hain aaj",
    "patient kamzor lag rahe hain",
    "unhone dawai mana kar di",
    "dad subah confused the",
    "patient ko raat bukhar tha",
    "wo saans phool raha tha",
    "uncle bathroom mein gir gaye",
    # Hinglish mixed
    "uncle ko aaj subah chakkar aa raha tha",
    "dad ne khana nahi khaya",
    "patient weak lag rahe hain aaj",
    "wo dawai lena nahi chahte",
    "uncle ka BP high tha subah",
    "dad thoda confused the morning mein",
    "patient ne walk nahi ki aaj",
    "wo zyada soye hain aaj kal",
    "uncle ko fever aa gaya raat ko",
    "dad ka behaviour alag tha aaj",
]


def load_model():
    """
    Load the configured SentenceTransformer model for future embedding routing.
    Never raises; updates EMBEDDING_AVAILABLE and _model.
    """
    global EMBEDDING_AVAILABLE, _model

    retries = max(1, int(getattr(config, "EMBEDDING_LOAD_RETRIES", 3)))
    last_error = None

    for _ in range(retries):
        try:
            from sentence_transformers import SentenceTransformer

            _model = SentenceTransformer(config.EMBEDDING_MODEL_NAME)
            EMBEDDING_AVAILABLE = True
            verify_centroid_freshness(regenerate=True)
            return _model
        except Exception as error:
            last_error = error
            _model = None
            EMBEDDING_AVAILABLE = False

    if last_error is not None:
        print(f"Embedding model load failed: {last_error}")
    return None


def _cache_get(key: str):
    if key not in _hard_gate_cache:
        return None
    value = _hard_gate_cache.pop(key)
    _hard_gate_cache[key] = value
    return value


def _cache_set(key: str, value: str | None) -> None:
    try:
        _hard_gate_cache[key] = value
        max_size = max(1, int(getattr(config, "HARD_GATE_CACHE_SIZE", 300)))
        while len(_hard_gate_cache) > max_size:
            oldest_key = next(iter(_hard_gate_cache))
            _hard_gate_cache.pop(oldest_key, None)
    except Exception:
        pass


def _contains_phrase(message: str, normalized_message: str, phrases: list[str]) -> bool:
    for phrase in phrases:
        phrase_lower = phrase.lower()
        normalized_phrase = normalize(phrase_lower)
        if phrase_lower in message or (normalized_phrase and normalized_phrase in normalized_message):
            return True
    return False


def _compact_typo_match(message: str, normalized_message: str, phrases: list[str], threshold: float = 0.86) -> bool:
    """
    Catch short collapsed crisis typos like "chestoain" -> "chest pain".
    Restricted to crisis hard-gate phrases to avoid broad fuzzy false positives.
    """
    try:
        text = "".join(ch for ch in f"{message} {normalized_message}" if ch.isalnum() or ch.isspace())
        tokens = [token for token in text.split() if len(token) >= 5]
        compact_text = "".join(text.split())
        candidates = tokens + ([compact_text] if compact_text else [])
        for phrase in phrases:
            compact_phrase = "".join(normalize(phrase).split()) or "".join(str(phrase).lower().split())
            if len(compact_phrase) < 7:
                continue
            for candidate in candidates:
                if abs(len(candidate) - len(compact_phrase)) > 3:
                    continue
                if difflib.SequenceMatcher(None, candidate, compact_phrase).ratio() >= threshold:
                    return True
        return False
    except Exception:
        return False


def _is_death_false_positive(message: str, normalized_message: str) -> bool:
    try:
        has_pet_context = any(term in message or term in normalized_message for term in DEATH_FALSE_POSITIVE_CONTEXT)
        has_person_context = any(term in message or term in normalized_message for term in DEATH_PERSON_CONTEXT)
        return has_pet_context and not has_person_context
    except Exception:
        return False


def _hard_safety_gate(message: str) -> str | None:
    """
    Return a crisis cluster from deterministic hard gates, or None.
    This runs before embeddings so safety-critical phrases do not depend on
    model availability or similarity thresholds.
    """
    try:
        if not isinstance(message, str):
            return None

        lowered = message.lower().strip()
        if not lowered:
            return None

        cached = _cache_get(lowered)
        if cached is not None or lowered in _hard_gate_cache:
            return cached

        normalized = normalize(lowered)
        if normalized in TIER1_CRISIS_MEDICAL_FALSE_POSITIVES or lowered in TIER1_CRISIS_MEDICAL_FALSE_POSITIVES:
            _cache_set(lowered, None)
            return None

        checks = [
            ("crisis_medical", TIER1_CRISIS_MEDICAL),
            ("crisis_safety", TIER2_CRISIS_SAFETY),
            ("crisis_self_harm", TIER3_CRISIS_SELF_HARM),
            ("crisis_death", TIER4_CRISIS_DEATH),
        ]
        for intent_name, phrases in checks:
            if _contains_phrase(lowered, normalized, phrases) or (
                intent_name.startswith("crisis_") and _compact_typo_match(lowered, normalized, phrases)
            ):
                if intent_name == "crisis_death" and _is_death_false_positive(lowered, normalized):
                    continue
                _cache_set(lowered, intent_name)
                return intent_name

        _cache_set(lowered, None)
        return None
    except Exception:
        return None


def _seed_sentences() -> dict[str, list[str]]:
    return {
        "crisis_medical": [
            "chest pain", "heart attack", "dil ka daura", "emergency",
            "severe chest pain", "ambulance call karo", "not breathing",
            "saans nahi aa rahi", "seene mein dard ho raha hai",
            "dad collapsed", "uncle behosh ho gaya", "chest pain abhi",
            "dad ko chest pain", "Rajesh ko daura aaya", "call ambulance",
            "hospital le jao", "ambulance", "kuch theek nahi lag raha",
            "bahut bura haal hai", "unconscious", "heart failure",
            "heart pain", "breathing stuck", "dam ghut raha hai",
            "patient collapsed", "rajesh behosh hai",
            "dad ko chest pain ho raha hai", "saans problem abhi hai",
            "uncle ka BP bahut low aur behosh", "heart attack jaisa lag raha hai",
            "ambulance jaldi bhejo please",
	        "patient having chest pain right now",
           "severe pain in chest abhi",
            "heart attack symptoms aa rahe hain",
            "breathing problem ho rahi hai",
            "ambulance bulana hai jaldi",
            "dad ka heart problem ho gaya",
            "uncle ko heart attack aa gaya",
            "chest mein unbearable pain ho rahi hai",
            "saans lene mein mushkil ho rahi hai",
            "not able to breathe properly",
            "heart beat irregular hai",
            "severe breathing difficulty abhi",
            "patient unconscious ho gaya",
            "emergency situation hai hospital jana padega",
            "heart pain zyada ho gaya",
            "breathlessness badh gaya hai",
            "cardiac emergency alert",
            "BP suddenly dropped aur unconscious",
            "severe dizziness ke saath chest pain",
            "stroke symptoms dikh rahe hain"
        ],
        "crisis_safety": [
            "someone attacked me", "koi maar raha hai", "threat to life",
            "physical assault", "someone trying to harm me",
            "mujhe maar raha hai", "koi hamla kar raha",
            "someone is hurting dad", "caregiver ne maara",
            "some one raped my rani", "rape", "koi ghar mein ghus aaya",
            "someone following me", "koi peeche pada hai",
            "someone came to kill me", "i will be killed",
            "koi dad ko maar raha hai", "someone ghar mein ghus gaya",
            "caregiver ne uncle ko hit kiya", "mujhe threat mil raha hai",
            "koi hamla kar raha hai mere upar",
            "someone trying to hurt me abhi",
            "physical danger mein hoon",
            "dad ko koi threaten kar raha hai",
            "mujhe koi mara ya maara",
            "burglar ghar mein aaya hai",
            "intruder inside house",
            "koi mujhe harm karne ke liye aaya",
            "unsafe situation ghar mein",
            "caregiver ne dad ko hurt kiya",
            "koi dangerous situation hai",
            "someone pointing weapon at me",
            "rape ki attempt hui hai",
            "sexual assault abhi ho gaya",
            "koi forcefully ghar mein ghus gaya",
            "threatening calls aa rahe hain",
            "blackmailing ho raha hai",
            "dad ke saath koi violence hui hai",
            "abuse happening right now",
            "life in danger someone trying to kill"
        ],
        "crisis_self_harm": [
            "kud kushi", "building se kuda", "i hurt myself",
            "i want to die", "suicide", "kill myself",
            "main nahi rehna chahta", "khatam kar lena chahta hoon",
            "maine kuch kha liya", "mai building se kuda",
            "dad ne kuch kha liya", "I can't take this anymore",
            "mai build pe sey kudh liya", "i jumped from building",
            "maine zeher kha liya", "bahut thak gaya hoon sab se",
            "i dont want my life", "main life nahi chahata",
            "sleeping pills kha liya", "building se jump karne ka mann hai",
            "i want to end my life",
            "khatam kar lena apna sab",
            "mai apne aap ko harm karunga",
            "suicide karne ka soch raha hoon",
            "building se niche jump karne wala hoon",
            "pill overdose le liya hai",
            "zeher pi liya hai abhi",
            "cutting kar raha hoon apne aap ko",
            "no point in living anymore",
            "main marega ya maregi",
            "kuch bhi mehnat nahi karna ab",
            "i can't do this anymore",
            "better if i wasn't born",
            "life se haar gaya hoon",
            "sab kuch adjust nahi ho raha",
            "main nahi chahta jab leb",
            "i wish i was dead",
            "apni life khatam karna chahta hoon",
            "harm karunga apne aap ko",
            "no reason to live now"
        ],
        "crisis_death": [
            "passed away", "died", "no more", "mar gaye",
            "intqaal ho gaya", "woh nahi rahe", "swargart ho gaye",
            "dad died", "papa nahi rahe", "funeral", "i am dead",
            "patient expired", "rajesh nahi rahe", "antim sanskar",
            "papa passed away ho gaye", "dad no more hai",
            "uncle ka intqaal ho gaya", "patient mar gaye today",
            "dad passed away last night",
            "ammi passed away last night",
            "matha died this morning",
            "ammi expired this morning",
            "ammi declared dead by hospital this morning",
            "Aunt breathing stopped completely this morning",
            "ammi death ho gaya this morning",
            "ammi has died this morning",
            "ammi no more signals from dad this morning",
            "mom heart stopped working this morning",
            "patient died this morning",
            "woh mar gaye hospital mein",
            "uncle is no more",
            "aunt is no more",
            "Mommy mar gayi", "mommy nahi rahe",
            "sister is no more",
            "brother is no more",
            "father is no more",
            "mother is no more",
            "aunt is no more",
            "uncle is no more",
            "sister in law is no more",
            "brother in law is no more",
            "father in law is no more",
            "mother is no more",
            "saas mar gayi",
            "saas nahi rahe",
            "saas is no more",
            "saas is no more",
            "bahu mar gayi",
            "bahu nahi rahe",
            "bahu is no more",
            "bahu is no more",
            "bhabhi is dead",
            "funeral arrangements karni hain",
            "last rites perform karni hain",
            "antim sanskar kal hain",
            "dad left us today",
            "swargat ho gaye papa",
            "swargat ho gaye ammi",
            "no longer with us",
            "passed away peacefully",
            "expired this afternoon",
            "declared dead by hospital",
            "breathing stopped completely",
            "death ho gaya",
            "patient has died",
            "no more signals from dad",
            "heart stopped working",
            "final moments aaye",
            "life ended for patient"

        ],
        "medication_due_now": [
            "what should i take now", "abhi kya lena hai",
            "medication schedule now", "current time medicine",
            "dawai kab leni hai abhi", "which pill right now",
            "kya lena hai", "which tablet now", "morning medicines",
            "dinner ke baad kya", "medicines for this time",
            "what meds now", "abhi ki dawai", "right now dose",
            "what medicine now i want to take", "what medicine for afternoon",
            "present medicines to be taken", "which medicine is due now",
            "abhi kaunsi dawa leni hai", "morning ki medicine due hai kya",
            "raat ki tablet abhi leni hai kya", "lunch ke baad wali dawai kaunsi",
            "abhi kya liya", "abhi kya lena hai", "is time kya liya",
            "current time pe kaunsi dawai", "abhi wali medicine kaunsi",
            "subah ki medicine abhi leni hai kya", "night tablet due hai kya",
            "abhi dose ka time hai kya", "kaunsi dawai right now leni hai",
            "dinner ke baad wali medicine abhi chahiye", "current dose batao",
            "iss waqt kaunsi tablet leni hai", "morning dose abhi due hai",
            "abhi kaunsi medicine leni hai",
            "what is due right now",
            "current time ki medicine batao",
            "dawai ka time ho gaya",
            "which medicine should i take now",
            "right now medication chahiye",
            "present dose kya hai",
            "abhi tablet lena hai kya",
            "medicine schedule check karna hai abhi",
            "what is the current dose i should take",
            "morning ki dawa abhi leni hai",
            "dinner ke baad medicine abhi",
            "medication time check karo",
            "which pill for now",
            "what medicine is scheduled for this moment",
            "current prescription check karna hai",
            "abhi kya dawa lena chahiye",
            "right now meds to take",
            "what tablets to take right now",
            "dose check karna hai abhi",
            "upcoming medicine kya hai",
            "next medicine due kya hai"
        ],
        "medication_report": [
            "took it", "given", "de di", "le li", "kha li",
            "nhi li", "skip kar di", "bhool gaya", "missed dose",
            "nahi li kyunki soya tha", "subah wali nahi li",
            "saari medicines le li", "no medications today",
            "done", "haan", "yes",
            "maine subah ki dawai le li", "papa ne le li",
            "missed my pill", "forgot to take", "no medications all today",
            "no medicines all today", "took night meds", "nahi khai",
            "bhool gya", "Morning ki dawa le li", "raat wali tablet nahi li",
            "afternoon meds skip ho gaya", "papa ne medicine kha li",
            "subah ki pill done hai", "morning ki dawa le li",
            "subah ki medicine nahi li", "night tablet skip kar di",
            "dawai kha li hai", "metformin le liya",
            "amlodipine nahi li aaj", "aaj koi dawai nahi li",
            "evening dose ho gaya", "bp tablet le liya",
            "maine medicine le li abhi",
            "took the tablet just now",
            "dawa le li hai sahi se",
            "nahi li aaj ki dawa",
            "skip kar diya morning ki medicine",
            "missed the evening dose",
            "bhool gaye tablet lena",
            "kha li medicine abhi",
            "pill liya abhi",
            "nahi li medicine kyunki不舒服 tha",
            "le li morning ki dawa",
            "dad ne medicine kha li",
            "took metformin with food",
            "papa ne amlodipine le li",
            "tablet nahi li sahi time pe",
            "took all medicines today",
            "kuch medicines nahi li",
            "evening dose complete kiya",
            "no meds taken today",
            "skip kar diya medicine",
            "missed morning meds",
            "forgot to take bp tablet"
        ],
        "medication_list": [
            "what meds am i on", "all medicines", "dawai ki list",
            "kitni dawaiyan hai", "how many medicines",
            "dad ki saari medicines kya hain",
            "which medicines to tell the doctor",
            "active medicines list", "medication names",
            "list of drugs", "current prescriptions", "current medication list",
            "show current tablets", "what medicines am i taking",
            "meri current dawa list batao", "dad ka medicine list dikhao",
            "abhi kaunsi tablets chal rahi hain", "active dawai ka naam kya hai",
            "dad ki active medicines batao", "current tablet list dikhao",
            "kaunsi dawai chal rahi hai", "meri dawa list kya hai",
            "doctor ko batane wali medicine list", "all current tablets batao",
            "patient ka medication list chahiye", "active prescription ka naam",
            "dad ki saari medicines batao",
            "ammi ki saari medicines batao",
            "matha ki saari medicines batao",
            "sister ki saari medicines batao",
            "brother ki saari medicines batao",
            "father in law ki saari medicines batao",
            "mother in law ki saari medicines batao",
            "saas ki saari medicines batao",
            "bahu ki saari medicines batao",
            "bhabhi ki saari medicines batao",
            "Ammi aur daddy ki saari medicines batao",
            "list of all current medications",
            "what pills is dad currently taking",
            "medicines on going kya hain",
            "active prescriptions list chahiye",
            "all tablets currently in use",
            "medication inventory batao",
            "what is dad taking for his conditions",
            "current medicines kya hain",
            "all drugs prescribed to patient",
            "show me all medicines dad is on",
            "medications summary chahiye",
            "what tablets are running now",
            "active medication list dikhao",
            "current prescription details",
            "all drugs dad is consuming",
            "medicine inventory for dad",
            "complete medication profile",
            "what all pills does rajesh take",
            "medicines currently active for patient"
        ],
        "medication_schedule": [
            "when to take amlodipine", "what time is the evening dose",
            "medicine ka time kya hai", "full day schedule",
            "kitni baar leni hai", "how many times a day",
            "schedule for metformin", "timing of my pills",
            "kab leni hai", "dose schedule", "medicine routine",
            "what medicine for afternoon", "afternoon medicine schedule",
            "schedule", "subah shaam ki dawa timing", "metformin kab leni hai after food",
            "raat ko kaunsi tablet leni hai", "medicine routine kya hai dad ka",
            "subah wali dawa ka time kya hai", "night tablet kab leni hai",
            "breakfast ke pehle ya baad medicine", "evening dose ka timing",
            "full day dawai schedule batao", "metformin kitni baar leni hai",
            "dose routine dad ka kya hai", "lunch ke baad medicine timing",
            "medicine timing schedule batao",
            "when should dad take metformin",
            "schedule of all medicines today",
            "dad and mom ki dawa schedule",
            "saas ki gol schedule batao",
            "bahu ki gol schedule batao",
            "bhabhi ki gol schedule batao",
            "Ammi aur daddy ki gol schedule batao",
            "meri pathni ki gol schedule batao",
            "kab dawa leni hai maa",
            "kitni baar leni hai",
            "kab leni hai pitha",
            "dose timing for all tablets",
            "medicine routine details",
            "how many times a day medicine",
            "breakfast ke baad kya medicine leni hai",
            "lunch ke baad dawa kab leni hai",
            "evening dose ka time kya hai",
            "night medicine kab lena hai",
            "morning medicines kitni baar",
            "medication schedule for the day",
            "timings of all tabs for dad",
            "when to take amlodipine morning ya evening",
            "schedule for bp and sugar medicines",
            "medicine routine ka full plan",
            "how to take these medicines",
            "dosage schedule with times",
            "when is each medicine supposed to be taken",
            "medicine timing chart chahiye",
            "full day medication routine"
        ],
        "medication_side_effect": [
            "metformin ke side effects kya hain",
            "amlodipine lene ke baad chakkar aa raha hai",
            "kya yeh tablet ki wajah se ho raha hai",
            "side effects of ramipril", "dawai se allergy",
            "medicine causing headache", "side effect list",
            "amlodipine se chakkar aa raha hai", "metformin ke baad loose motion",
            "ramipril lene ke baad cough hai", "tablet ki wajah se headache ho raha hai",
            "amlodipine lene ke baad headache aa raha hai",
            "metformin se stomach problem ho rahi hai",
            "side effects of current medicines",
            "dawai se weakness aa rahi hai",
            "ramipril lene ke baad cough hai",
            "tablet ki wajah se dizziness ho raha hai",
            "medicine causing nausea",
            "allergic reaction dikh raha hai",
            "dawa se rash aa gaya",
            "tablet se body me swelling",
            "medicine side effects check karna hai",
            "dizziness after taking medicine",
            "metformin causing loose motion",
            "bp tablet making me dizzy",
            "any side effects from these meds",
            "what side effects to expect from metformin",
            "arm pain after taking medicine",
            "eye swelling from tablet",
            "drowsiness because of medicine",
            "skin reaction to new tablet"
        ],
        "symptom_report": [
            "headache", "dizziness", "fever", "chakkar aa raha hai",
            "body pain", "nausea", "feeling weak", "cough",
            "sar dard", "halka bukhar hai", "sometimes chest discomfort",
            "thoda sa dard", "mild headache", "chronic pain",
            "pair mein sojan", "thakaan", "bukhar hai",
            "aaj halka fever hai", "body pain ho raha hai",
            "dad ko chakkar aa rahe hain", "khansi aur weakness hai",
            "aaj body pain hai", "halka headache ho raha hai",
            "stomach mein nausea feel ho raha hai", "pair mein swelling hai",
            "patient weak feel kar raha hai", "bukhar thoda sa hai",
            "khansi zyada ho gayi hai", "thakaan bahut lag rahi hai",
            "aaj headache ho raha hai",
            "body pain abhi",
            "feeling dizzy since morning",
            "fever aa gaya aaj",
            "cough zyada ho gaya",
            "nausea ho raha hai",
            "stomach pain ho rahi hai",
            "weakness feel kar raha hoon",
            "throat pain hai",
            "sar dard abhi",
            "chakkar aa rahe hain",
            "legs pain ho rahi hain",
            "back pain hai aaj",
            "chest discomfort ho raha hai",
            "breathing heavy lag raha hai",
            "palpitations ho rahe hain",
            "swelling in feet aa gaya",
            "appetite kam ho gaya hai",
            "sleep problems ho rahi hain",
            "anxiety symptoms dikh rahe hain",
            "body fatigue ho raha hai",
            "mild fever aaj",
            "throwing up ho raha hai"
        ],
        "vital_report": [
            "130/85", "bp 140 over 90", "blood pressure aaya",
            "fasting sugar 180", "random glucose 210 mg/dl",
            "pulse 95", "heartbeat fast", "spo2 94%",
            "oxygen level low", "fever 101", "temperature 38.5",
            "aaj bp zyaada tha", "sugar check kiya 200 tha",
            "bp reading", "pulse ox", "temp",
            "BP high hai 150/90", "sugar 180 aaya fasting",
            "oxygen 93 percent hai", "pulse 110 ho gaya",
            "aaj BP 140/90 reading hai", "fasting sugar high hai 190",
            "pulse 105 bpm aa raha hai", "spo2 94 dikha raha hai",
            "temperature 101 fever hai", "oxygen level 92 percent",
            "random sugar 210 mg/dl aaya", "blood pressure thoda high hai",
            "bp reading aaj 145/95 hai",
            "blood pressure 130 over 85",
            "pulse rate 100 hai aaj",
            "heartbeat 95 per minute",
            "oxygen level 94 percent",
            "spo2 reading low hai 91",
            "temperature 101 degree fever",
            "blood sugar fasting 180",
            "post meal sugar 210",
            "random glucose 195 mg/dl",
            "hba1c value 8.2",
            "vital signs check karo aaj",
            "bp normal nahi hai aaj",
            "sugar level high aa rahi hai",
            "heart rate fast hai abhi",
            "oxygen saturation low hai",
            "fever temperature 38.5",
            "bp monitor reading 150/100",
            "blood pressure high aaj",
            "sugar reading 200 fasting",
            "vitals reported today",
            "latest bp reading from machine"
        ],
        "lab_report": [
            "hba1c 7.8", "creatinine 1.2 mg/dl", "lipid profile aaya",
            "lft normal hai", "report mili", "test results aaye",
            "lab se result aaya", "hba1c high hai",
            "thyroid out of range", "blood test normal",
            "lipid panel", "liver function", "kft report",
            "creatinine report high hai", "HbA1c result aaya 7.8",
            "blood test ka report normal hai", "latest lab report upload hua",
            "dad ka creatinine report aaya", "hba1c high hai report mein",
            "kft lab result mila hai", "cbc report normal bol raha hai",
            "liver test ka result aaya", "thyroid report out of range hai",
            "blood sugar lab report high", "latest creatinine value batao",
            "lab report aaya hai check karo",
            "hba1c 7.8 aaya results mein",
            "creatinine level high hai 1.5",
            "lipid profile report aaya",
            "thyroid tsh out of range",
            "lft test results normal",
            "kft report creatinine 1.3",
            "blood test result aaya",
            "cbc normal hai sab values",
            "urine test report mila",
            "liver function test normal",
            "kidney function report check karo",
            "HbA1c latest value 8 point 2",
            "cholesterol report high hai",
            "triglycerides level elevated",
            "vitamin d deficiency report",
            "lab results from yesterday",
            "test report received from lab",
            "pathology results uploaded",
            "latest blood work results"
        ],
        "health_status_query": [
            "how is dad", "how is the patient", "Rajesh ki tabiyat kaisi hai",
            "aaj dad kaisa hai", "health update today", "koi nayi update hai kya",
            "any new alerts", "tell me about dad's health", "briefing chahiye",
            "health status update", "patient health summary",
            "how is dad today", "how is dad doing", "dad health update",
            "dad ki health kaisi hai today", "aaj ka health update do",
            "patient ka haal batao", "sab theek hai kya dad ke saath",
            "dad ka aaj status kya hai", "health summary chahiye",
            "koi alert hai kya patient ke liye", "aaj rajesh ka haal batao",
            "patient ki tabiyat update do", "care summary today batao",
            "dad ke vitals aur meds ka status", "sab normal hai kya aaj",
            "dad ki health update do aaj",
            "how is dad feeling today",
            "patient status batao",
            "rajesh ki tabiyat kaisi hai aaj",
            "health summary chahiye",
            "dad ka aaj ka health status",
            "is everything normal with dad",
            "any new health alerts",
            "patient health summary dikhao",
            "how is dad doing right now",
            "koi new update hai kya",
            "dad ka current haal kaisa hai",
            "health briefing chahiye",
            "update on dad's condition",
            "tabiyat ki status batao",
            "is dad okay today",
            "health status check karna hai",
            "dad ke saawal kya hai aaj",
            "patient condition report",
            "overall health summary"
        ],
        "new_prescription": [
            "doctor ne naya tablet diya", "new medicine started",
            "dosage badh gayi", "doctor changed the dose",
            "ek medicine band kar di", "stopped metformin",
            "clinic se aaye", "prescription mili",
            "new prescription from cardiologist",
            "started on amlodipine", "doctor ne new medicine start ki",
            "cardiologist ne dose change kiya", "nayi tablet add hui hai",
            "old dawai band kar di doctor ne","doctor ne naya prescription diya",
            "new medicine added today",
            "dose badhaya cardiologist ne",
            "medicine frequency badli hai",
            "new tablet add kiya doctor ne",
            "old medicine band kar diya",
            "prescription update hua hai",
            "medicine change hua hai new",
            "doctor ne dosage adjust kiya",
            "new medicine start hui hai",
            "added one more tablet daily",
            "reduced metformin dose",
            "changed amlodipine timing",
            "new prescription from specialist",
            "updated medications list",
            "doctor visit se naya prescription",
            "medicine modification done",
            "dose change by doctor today",
            "new drug added to list",
            "prescription changed by cardiologist"
        ],
        "appointment": [
            "next appointment kab hai", "when is the follow-up",
            "doctor ke paas kab jaana hai", "cardiology checkup",
            "appointment lena hai", "kab available hai doctor",
            "10 din baad appointment hai", "next visit",
            "doctor visit", "checkup schedule", "follow up",
            "followup", "appmnt", "schedule appointment",
            "kal doctor appointment hai kya", "follow up kab jaana hai",
            "checkup ka time batao", "doctor visit schedule karna hai",
            "next doctor visit kab hai", "follow-up appointment book karna hai",
            "cardiology ka checkup kab hai", "appointment ka reminder chahiye",
            "doctor ke paas kal jaana hai kya", "upcoming checkup batao",
            "appointment reschedule karna hai", "clinic visit ka time kya hai",
            "doctor appointment kab hai next",
            "appointment schedule kya hai",
            "when is next doctor visit",
            "follow up kab jaana hai",
            "doctor ke paas appointment lena hai",
            "next visit schedule kya hai",
            "checkup kab hai dad ka",
            "cardiology appointment reminder",
            "appointment date confirm karo",
            "doctor visit schedule batao",
            "next appointment date kya hai",
            "when to visit cardiologist",
            "scheduled checkup kab hai",
            "appointment reschedule karna hai",
            "clinic visit date confirm",
            "doctor consultation timing",
            "upcoming appointment details",
            "when is the next follow up",
            "schedule new appointment",
            "book appointment for dad",
            "appointment time kya hai"
        ],
        "caregiver_handoff": [
            "i am leaving now, rani will take over",
            "ab meera dekh rahi hai", "caregiver change",
            "someone else is watching dad today",
            "main nahi hoon aaj", "i won't be available",
            "rani sambhal legi", "shift change",
            "duty handover", "i have arrived, taking over",
            "handover kar raha hoon", "main aa gaya hoon",
            "taking over now", "rani ab dad ko dekhegi",
            "main late aaunga caregiver change kar do", "shift handover ho gaya",
            "aaj meera available nahi hai", "rani will take over aaj",
            "main aaj leave par hoon", "backup caregiver aa raha hai",
            "shift change ho gaya hai", "main late aaungi today",
            "caregiver duty rani ko de di", "meera unavailable hai aaj",
            "handoff complete rani sambhalegi", "main ghar pahunch gaya taking over",
            "meera will take over from now",
            "rani is taking over caregiving",
            "i am going now rani will handle",
            "shift change happening now",
            "handoff to new caregiver done",
            "another caregiver taking over today",
            "i am leaving rajesh in your care",
            "main ja raha hoon aap sab sambhalo",
            "meera is now responsible for dad",
            "rani takes over from here",
            "caregiver change happening today",
            "duty transferred to meera",
            "shift handover complete",
            "i am done for today you take over",
            "another person is taking care now",
            "responsibility transferred to rani",
            "meera is on duty now",
            "care given to rani from here",
            "handoff successful meera has taken over",
            "new caregiver in charge now"
        ],
        "caregiver_observation": TIER4_CAREGIVER_OBSERVATION + [
            "uncle had dizziness this morning and did not eat breakfast",
            "dad was weak today but took all medicines",
            "papa seemed confused at night and skipped dinner",
            "caregiver said uncle had chakkar after lunch",
            "rajesh did not eat breakfast but took morning tablets",
            "patient looked tired and slept most of the afternoon",
            "uncle had fever in the evening and missed dinner",
            "papa ate lunch but did not take the afternoon medicine",
            "he was dizzy this morning and walked slowly",
            "dad seemed okay after taking all medicines",
            "uncle complained of weakness but finished breakfast",
            "papa ne breakfast nahi khaya but dawai le li",
            "uncle ko chakkar aaye aur lunch nahi khaya",
            "patient was restless at night and did not sleep",
            "caregiver update uncle was weak but alert",
            "rani said dad did not eat and looked tired",
            "meera reported papa took all medicines but felt dizzy",
            "uncle ne breakfast skip kiya but meds le li",
            "dad thoda weak the aur lunch nahi khaya",
            "caregiver bol raha hai papa ko chakkar tha",
            "patient ne dinner khaya but raat ki tablet miss ki",
            "uncle had chest discomfort after lunch",
            "patient refused food today morning",
            "dad seemed tired whole day",
            "he was confused in the afternoon",
            "uncle complained of headache",
            "patient did not sleep well night",
            "dad's appetite was low today",
            "he seemed weak after medicine",
            "uncle had dizziness after standing",
            "patient's mood was low all day",
            "he walked slowly today morning",
            "dad seemed better after breakfast",
            "uncle's energy was low afternoon",
            "patient had cough all day",
            "he seemed restless evening time",
            "dad ate well today lunch",
            "uncle's vitals seemed stable",
            "patient was alert morning time",
            "he seemed comfortable after rest",
            "dad's condition was stable today",
            "uncle seemed normal today",
            "patient was active morning time",
            "he had no complaints today"
        ],
        "document_upload_confirmation": [
            "sending the prescription", "photo bhej raha hoon",
            "uploading report", "yeh lab report hai",
            "this is the new prescription", "sent the image",
            "photo bhej di", "uploaded photo",
            "did you receive the photo", "report mili kya",
            "sent prescription photo", "upload kar diya",
            "prescription photo bhej diya", "report PDF upload kar raha hoon",
            "lab report ka photo sent", "doctor note ki image bheji",
            "prescription image bhej raha hoon", "report ka pdf upload kiya",
            "photo attach kar diya hai", "doctor prescription sent hai",
            "lab report photo bheji hai", "xray image upload kar raha hoon",
            "file bhej di please check", "yeh discharge summary pdf hai",
            "sending prescription photo now",
            "uploaded lab report check karo",
            "prescription image bhej diya hai",
            "report photo attach kar diya",
            "document upload kar diya hai",
            "check the prescription photo sent",
            "report pdf uploaded for you",
            "photo bhej raha hoon prescription ka",
            "uploaded xray results",
            "discharge summary image sent",
            "doctor note photo bheja hai",
            "lab results uploaded now",
            "medical document sent please verify",
            "prescription copy attached",
            "report image received kya",
            "uploaded today's prescription photo",
            "file bhej di please check",
            "medical document sent",
            "report photo check karo",
            "document attached for reference"
        ],
        "approve_command": [
            "approve", "yes approve", "confirm it",
            "i agree", "go ahead", "proceed",
            "haan karo", "theek hai but i want to approve",
            "yes approve kar do", "theek hai proceed karo",
            "confirm this please", "approve karna hai",
            "yes approve it",
            "go ahead with the plan",
            "i approve this action",
            "please proceed with it",
            "confirm the update",
            "agree and approve",
            "thumbs up for this",
            "approved from my side",
            "ok proceed kar do",
            "haan karo sab theek hai",
            "confirm karo is update ko",
            "i am okay with this",
            "approve the changes",
            "let it happen",
            "go for it",
            "approved please execute",
            "confirm this update",
            "go ahead with the schedule",
            "execute the plan approved",
            "this is fine proceed"
        ],
        "veto_command": [
            "veto", "reject", "deny", "nahi karna",
            "cancel it", "do not proceed", "band karo",
            "ina mana", "disapprove",
            "veto kar do", "nahi approve karna",
            "reject this update", "is action ko cancel karo",
            "no do not approve",
            "veto this action",
            "cancel it please",
            "do not proceed",
            "nahi karna yeh sab",
            "reject this update",
            "disapprove this plan",
            "band karo isko",
            "undo this action",
            "please stop this",
            "cancel the scheduled",
            "do not execute",
            "not approved from me",
            "veto the changes",
            "reject and cancel",
            "do not allow this",
            "stop the process",
            "disallow this action",
            "please don't do this",
            "abort this plan"
        ],
        "emotional_checkin": TIER3_EMOTIONAL_CHECKIN + [
            "i am sad",
            "feeling sad",
            "feeling tensed",
            "i feel anxious",
            "i am worried",
            "i am upset",
            "heart break",
            "heartbroken",
            "mood off hai",
            "mann kharab hai",
            "bahut tension hai",
            "pareshan hoon",
            "ghabrahat ho rahi hai",
            "lonely feel kar raha hoon",
            "i feel lonely",
            "i am stressed",
            "stress ho raha hai",
            "i need emotional support",
            "dil toot gaya",
            "rona aa raha hai",
            "i feel low",
            "aaj mood kharab hai",
            "i am feeling bahut anxious", "dil heavy lag raha hai",
            "today mood off hai", "bahut tension feel ho raha hai",
            "feeling sad today",
            "tension ho raha hai bahut",
            "pareshan hoon kuch kamzor",
            "dil heavy lag raha hai",
            "kuch udas feel ho raha hoon",
            "not feeling okay emotionally",
            "overwhelmed feel kar raha hoon",
            "mentally exhausted hoon",
            "stress ho raha hai kaam se",
            "anxious lag raha hai kuch",
            "lonely feel kar raha hoon aaj",
            "mood off hai bahut",
            "i feel worried about dad",
            "emotionally drained hoon",
            "sad and tired today",
            "feeling low and hopeless",
            "mann nahi lag raha kuch bhi",
            "dil turant nahi hai sahi",
            "kuch emotional support chahiye",
            "not feeling motivated today",
            "feeling emotionally weak",
            "kuch emotional breakdown ho raha hai"
        ],
        "greeting_help": [
            "hi", "hello", "namaste", "haan bolo",
            "kya kar sakte ho", "what can you do", "help",
            "main meera bol rahi hoon", "good morning",
            "hey", "how are you", "heloo", "hello there",
            "mujhe help chahiye", "samjha nahi", "kaise use karein",
            "hello carecircle help karo", "namaste kya kar sakte ho",
            "hi mujhe menu chahiye", "good morning status batao",
            "namaste how can you help",
            "hello what can you do for me",
            "hi i need some help",
            "hey there can you assist me",
            "kya kar sakte ho mere liye",
            "hello carecircle assistant",
            "hi mujhe guidance chahiye",
            "can you tell me what you do",
            "hey help karo please",
            "namaste mujhe info chahiye",
            "hello need assistance",
            "can i ask you something",
            "what are your capabilities",
            "kaise help kar sakte ho",
            "hi there need some info",
            "hello i want to know more",
            "how do i use this system",
            "what commands can i give",
            "help menu dikhao please",
            "quick help needed",
            "need to understand how this works"
        ]
    }


def _compute_seed_hash(seed_sentences: dict[str, list[str]]) -> str:
    payload = {
        "model": getattr(config, "EMBEDDING_MODEL_NAME", ""),
        "seeds": seed_sentences,
    }
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _project_path(relative_path: str) -> Path:
    return Path(__file__).resolve().parent / relative_path


def _canonical_intent_order(seed_sentences: dict[str, list[str]]) -> list[str]:
    try:
        order_path = _project_path(config.INTENT_ORDER_PATH)
        if order_path.exists():
            metadata = json.loads(order_path.read_text(encoding="utf-8"))
            configured_order = metadata.get("intent_order") if isinstance(metadata, dict) else metadata
            if isinstance(configured_order, list):
                ordered = [
                    intent_name
                    for intent_name in configured_order
                    if intent_name in seed_sentences and intent_name != UNCLEAR_INTENT
                ]
                remaining = [intent_name for intent_name in seed_sentences if intent_name not in ordered]
                return ordered + remaining
    except Exception:
        pass
    return list(seed_sentences.keys())


def _encode_texts(texts: list[str]):
    return _model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)


def _with_unclear_centroid(centroids, order: list[str]):
    """Append a neutral zero-vector centroid for the explicit fallback cluster."""
    import numpy as np

    clean_order = list(order or [])
    if UNCLEAR_INTENT in clean_order:
        return centroids, clean_order
    if centroids is None or int(getattr(centroids, "ndim", 0)) != 2 or int(centroids.shape[0]) == 0:
        return centroids, clean_order
    zero_row = np.zeros((1, int(centroids.shape[1])), dtype=centroids.dtype)
    return np.vstack([centroids, zero_row]), clean_order + [UNCLEAR_INTENT]


def _current_seed_hash() -> str:
    try:
        return _compute_seed_hash(_seed_sentences())
    except Exception:
        return ""


def _stored_seed_hash() -> str:
    try:
        hash_path = _project_path(config.CENTROID_SEED_HASH_PATH)
        order_path = _project_path(config.INTENT_ORDER_PATH)

        if order_path.exists():
            loaded = json.loads(order_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and loaded.get("seed_hash"):
                return str(loaded.get("seed_hash") or "").strip()

        if hash_path.exists():
            return hash_path.read_text(encoding="utf-8").strip()
        return ""
    except Exception:
        return ""


def verify_centroid_freshness(regenerate: bool = True) -> bool:
    """
    Compare the current seed-corpus hash against stored centroid metadata.
    If stale and regenerate=True, rebuild centroid artifacts immediately.
    """
    try:
        current_hash = _current_seed_hash()
        stored_hash = _stored_seed_hash()
        if current_hash and stored_hash == current_hash:
            return True

        print(
            "WARNING: Embedding centroid seed hash mismatch; "
            f"stored={stored_hash or 'missing'} current={current_hash or 'unknown'}"
        )
        if regenerate:
            generated, order, generated_hash = _generate_centroids(save_to_disk=True)
            fresh = generated is not None and bool(order) and generated_hash == current_hash
            if fresh:
                print("INFO: Embedding centroids regenerated from current seed corpus.")
            return fresh
        return False
    except Exception as error:
        print(f"WARNING: Embedding centroid freshness check failed: {error}")
        return False


def _generate_centroids(save_to_disk=False):
    """
    Build one centroid per explicit CareCircle cluster.
    Returns (centroids, intent_order, seed_hash).
    """
    global _centroids, _intent_order, _seed_hash
    try:
        if _model is None:
            return None, [], ""

        import numpy as np

        seed_sentences = _seed_sentences()
        seed_hash = _compute_seed_hash(seed_sentences)
        order = _canonical_intent_order(seed_sentences)
        centroids = []

        for intent_name in order:
            normalized_seeds = [normalize(sentence) or sentence.lower() for sentence in seed_sentences[intent_name]]
            embeddings = _encode_texts(normalized_seeds)
            centroid = np.mean(embeddings, axis=0)
            norm = np.linalg.norm(centroid)
            if norm > 0:
                centroid = centroid / norm
            centroids.append(centroid)

        centroids_array = np.vstack(centroids)
        centroids_array, order = _with_unclear_centroid(centroids_array, order)
        _centroids = centroids_array
        _intent_order = order
        _seed_hash = seed_hash

        if save_to_disk:
            np.save(_project_path(config.CENTROID_PATH), centroids_array)
            metadata = {
                "intent_order": order,
                "seed_hash": seed_hash,
                "model": getattr(config, "EMBEDDING_MODEL_NAME", ""),
            }
            _project_path(config.INTENT_ORDER_PATH).write_text(
                json.dumps(metadata, indent=2),
                encoding="utf-8",
            )
            _project_path(config.CENTROID_SEED_HASH_PATH).write_text(seed_hash, encoding="utf-8")

        return centroids_array, order, seed_hash
    except Exception as error:
        print(f"Centroid generation failed: {error}")
        return None, [], ""


def _load_centroids():
    """
    Load cached centroids when their seed hash matches; otherwise regenerate.
    Returns (centroids, intent_order).
    """
    global _centroids, _intent_order, _seed_hash
    try:
        if _centroids is not None and _intent_order is not None:
            return _centroids, _intent_order

        import numpy as np

        seed_sentences = _seed_sentences()
        current_hash = _compute_seed_hash(seed_sentences)
        centroid_path = _project_path(config.CENTROID_PATH)
        order_path = _project_path(config.INTENT_ORDER_PATH)
        hash_path = _project_path(config.CENTROID_SEED_HASH_PATH)

        disk_hash = ""
        if hash_path.exists():
            disk_hash = hash_path.read_text(encoding="utf-8").strip()

        metadata = {}
        if order_path.exists():
            loaded = json.loads(order_path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                metadata = {"intent_order": loaded, "seed_hash": disk_hash}
            elif isinstance(loaded, dict):
                metadata = loaded

        metadata_hash = str(metadata.get("seed_hash") or disk_hash)
        order = list(metadata.get("intent_order") or [])

        if centroid_path.exists() and order and metadata_hash == current_hash:
            centroids = np.load(centroid_path)
            if len(order) == int(centroids.shape[0]):
                centroids, order = _with_unclear_centroid(centroids, order)
                _centroids = centroids
                _intent_order = order
                _seed_hash = current_hash
                return _centroids, _intent_order
            if (
                UNCLEAR_INTENT not in order
                and len(order) + 1 == int(centroids.shape[0])
            ):
                order = order + [UNCLEAR_INTENT]
                _centroids = centroids
                _intent_order = order
                _seed_hash = current_hash
                return _centroids, _intent_order

        generated, generated_order, generated_hash = _generate_centroids(save_to_disk=True)
        _seed_hash = generated_hash
        return generated, generated_order
    except Exception as error:
        print(f"Centroid load failed: {error}")
        return None, []


def _cosine_scores(vector, centroids):
    import numpy as np

    vector_norm = np.linalg.norm(vector)
    if vector_norm > 0:
        vector = vector / vector_norm
    return np.dot(centroids, vector)


def _token_overlap_score(message: str, seed: str) -> float:
    try:
        message_tokens = set(message.split())
        seed_tokens = set(seed.split())
        if not message_tokens or not seed_tokens:
            return 0.0
        overlap = len(message_tokens & seed_tokens)
        precision = overlap / len(message_tokens)
        recall = overlap / len(seed_tokens)
        if precision + recall == 0:
            return 0.0
        return (2 * precision * recall) / (precision + recall)
    except Exception:
        return 0.0


def _lexical_seed_match(normalized_message: str) -> tuple[str, float]:
    """
    Built-in fallback for clear seed-like messages when embeddings are offline.
    This is intentionally conservative and still applies config thresholds.
    """
    try:
        best_intent = "unknown"
        best_score = 0.0
        for intent_name, seeds in _seed_sentences().items():
            if intent_name.startswith("crisis_"):
                continue
            if intent_name in getattr(config, "EMBEDDING_BLOCKLIST", set()):
                continue
            if intent_name == "crisis_death" and _is_death_false_positive(normalized_message, normalized_message):
                continue
            for seed in seeds:
                normalized_seed = normalize(seed) or seed.lower()
                if not normalized_seed:
                    continue
                if normalized_message == normalized_seed:
                    score = 0.96
                elif normalized_seed in normalized_message or normalized_message in normalized_seed:
                    score = 0.88
                else:
                    overlap = _token_overlap_score(normalized_message, normalized_seed)
                    ratio = difflib.SequenceMatcher(None, normalized_message, normalized_seed).ratio()
                    score = max(overlap, ratio * 0.82)

                if score > best_score:
                    best_intent = intent_name
                    best_score = score

        thresholds = getattr(config, "EMBEDDING_CONFIDENCE_THRESHOLDS", {})
        threshold = float(thresholds.get(best_intent, thresholds.get("default", 0.50)))
        if best_intent == "unknown" or best_score < threshold:
            return "unknown", round(float(best_score), 4)
        return best_intent, round(float(best_score), 4)
    except Exception:
        return "unknown", 0.0


# APPROVED ENHANCEMENTS (commented-out for future activation):
# 1. Phonetic matching for Hinglish: Use jellyfish.metaphone() to match "dard"/"dardh".
# 2. Aho-Corasick automaton for hard gate: Build once at startup for O(n) crisis scanning.
# 3. Centroid auto-refresh: Weekly job to recompute centroids from confirmed interactions.
# 4. Per-user personalisation: Store user-specific alias expansions in profiles.preferences jsonb.
# 5. Fallback to Krutrim Vyakyarth model: If MiniLM confidence < 0.5, try Vyakyarth.
# 6. Async embedding pre-computation: Pre-embed common phrases at idle time.
# 7. Audit-based seed expansion: Monthly job to add high-confidence misclassifications to seed bank.


def classify_intent_embedding(message: str) -> tuple[str, float]:
    """
    Classify with embedding centroid similarity only.
    Crisis routing is owned by intent.is_valid_emergency() in router.py/handlers.py.
    Returns ("unknown", score) when the best score is below threshold.
    """
    try:
        try:
            import intent as deterministic_intent

            is_emergency, _ = deterministic_intent.is_valid_emergency(message)
            if is_emergency or deterministic_intent.classify_intent(message) == "crisis":
                return "unknown", 0.0
        except Exception:
            pass

        normalized = normalize(message)
        if not normalized:
            return "unknown", 0.0

        if not EMBEDDING_AVAILABLE or _model is None:
            return _lexical_seed_match(normalized)

        centroids, order = _load_centroids()
        if centroids is None or not order:
            return "unknown", 0.0

        vector = _encode_texts([normalized])[0]
        scores = _cosine_scores(vector, centroids)
        best_index = int(scores.argmax())
        best_intent = order[best_index]
        confidence = float(scores[best_index])

        if best_intent.startswith("crisis_"):
            return "unknown", confidence

        if best_intent in getattr(config, "EMBEDDING_BLOCKLIST", set()):
            return "unknown", confidence

        thresholds = getattr(config, "EMBEDDING_CONFIDENCE_THRESHOLDS", {})
        threshold = float(thresholds.get(best_intent, thresholds.get("default", 0.50)))
        if confidence < threshold:
            return "unknown", confidence

        return best_intent, confidence
    except Exception:
        return "unknown", 0.0
