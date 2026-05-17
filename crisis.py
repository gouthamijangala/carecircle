import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import config
import db


DEFAULT_HELPLINE = "112"
MAX_CRISIS_MESSAGE_CHARS = 800
CRISIS_PACKET_SCHEMA = "crisis_card_v4"
_last_osm_error_at: datetime | None = None


def _format_time_12h(value: str) -> str:
    try:
        parsed = datetime.strptime(str(value).strip(), "%H:%M")
        return parsed.strftime("%I:%M %p").lstrip("0")
    except Exception:
        return str(value).strip()


def _format_scheduled_times(times) -> str:
    try:
        formatted = [_format_time_12h(time_value) for time_value in list(times or []) if str(time_value).strip()]
        return ", ".join(formatted)
    except Exception:
        return ""


def _format_medication(medication: dict) -> str:
    try:
        drug_name = medication.get("drug_name") or "Unknown medicine"
        dose_amount = medication.get("dose_amount") or ""
        dose_unit = medication.get("dose_unit") or ""
        frequency = medication.get("frequency") or ""
        scheduled = _format_scheduled_times(medication.get("scheduled_times"))
        base = f"{drug_name} {dose_amount}{dose_unit} - {frequency}".strip()
        return f"{base} ({scheduled})" if scheduled else base
    except Exception:
        return "Unknown medicine"


def _parse_reported_at(value: str) -> datetime | None:
    try:
        raw = str(value or "").strip()
        if not raw:
            return None
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed
        return parsed.astimezone(timezone(timedelta(hours=5, minutes=30)))
    except Exception:
        return None


def _format_recent_medication(latest_log: dict | None) -> str | None:
    try:
        if not latest_log:
            return None
        reported_at = _parse_reported_at(latest_log.get("reported_at"))
        if reported_at is None:
            return None
        formatted_time = reported_at.strftime("%I:%M %p").lstrip("0")
        return f"{latest_log.get('drug_name') or 'Medication'} {latest_log.get('event_type') or 'reported'} at {formatted_time}"
    except Exception:
        return None


def _maps_link(latitude, longitude) -> str | None:
    try:
        if latitude is None or longitude is None:
            return None
        return f"https://www.openstreetmap.org/?mlat={latitude}&mlon={longitude}#map=16/{latitude}/{longitude}"
    except Exception:
        return None


def _hospital_search_link(latitude, longitude, query: str = "hospital") -> str | None:
    try:
        if latitude is None or longitude is None:
            return None
        search = str(query or "hospital").strip().replace(" ", "+")
        return f"https://www.openstreetmap.org/search?query={search}&mlat={latitude}&mlon={longitude}#map=14/{latitude}/{longitude}"
    except Exception:
        return None


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return radius_km * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _nearest_hospital_from_osm(latitude, longitude, radius_m: int = 7000) -> dict | None:
    """
    Free/no-key OpenStreetMap data lookup via Overpass.
    Falls back silently because public OSM endpoints may rate-limit or time out.
    """
    try:
        if not getattr(config, "CRISIS_OSM_LOOKUP_ENABLED", False):
            return None
        if latitude is None or longitude is None:
            return None
        lat = float(latitude)
        lon = float(longitude)
        query = f"""
        [out:json][timeout:4];
        (
          node["amenity"="hospital"](around:{radius_m},{lat},{lon});
          way["amenity"="hospital"](around:{radius_m},{lat},{lon});
          relation["amenity"="hospital"](around:{radius_m},{lat},{lon});
        );
        out center tags 10;
        """
        encoded = urllib.parse.urlencode({"data": query}).encode("utf-8")
        request = urllib.request.Request(
            "https://overpass-api.de/api/interpreter",
            data=encoded,
            headers={"User-Agent": "CareCircleDemo/1.0"},
            method="POST",
        )
        timeout = max(0.5, float(getattr(config, "CRISIS_OSM_LOOKUP_TIMEOUT_SECONDS", 2.0)))
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", errors="ignore"))

        best = None
        best_distance = None
        for element in payload.get("elements", []):
            tags = element.get("tags") or {}
            item_lat = element.get("lat") or (element.get("center") or {}).get("lat")
            item_lon = element.get("lon") or (element.get("center") or {}).get("lon")
            if item_lat is None or item_lon is None:
                continue
            distance = _distance_km(lat, lon, float(item_lat), float(item_lon))
            if best_distance is not None and distance >= best_distance:
                continue
            best_distance = distance
            phone = tags.get("phone") or tags.get("contact:phone") or "N/A"
            name = tags.get("name") or "Nearest hospital"
            best = {
                "name": name,
                "phone": phone,
                "maps_link": _maps_link(float(item_lat), float(item_lon)),
                "distance_km": round(distance, 2),
                "source": "openstreetmap_overpass",
            }
        return best
    except Exception as error:
        global _last_osm_error_at
        now = datetime.now(timezone.utc)
        cooldown = max(0, int(getattr(config, "CRISIS_OSM_LOOKUP_LOG_COOLDOWN_SECONDS", 300)))
        if _last_osm_error_at is None or (now - _last_osm_error_at).total_seconds() >= cooldown:
            print(f"OSM hospital lookup failed: {error}")
            _last_osm_error_at = now
        return None


def _empty_contact() -> dict:
    return {"name": "N/A", "phone": "N/A"}


def _empty_hospital() -> dict:
    return {"name": "N/A", "phone": "N/A", "maps_link": None}


def _contacts_from_cache(cache: dict | None) -> tuple[list[dict], dict, dict, str]:
    try:
        if not isinstance(cache, dict):
            return ([], _empty_hospital(), _empty_contact(), DEFAULT_HELPLINE)
        raw_contacts = list(cache.get("caregivers") or cache.get("emergency_contacts") or cache.get("contacts") or [])
        caregivers = []
        doctor = cache.get("doctor") if isinstance(cache.get("doctor"), dict) else _empty_contact()
        hospital = cache.get("hospital") if isinstance(cache.get("hospital"), dict) else _empty_hospital()
        for contact in raw_contacts:
            if not isinstance(contact, dict):
                continue
            role = str(contact.get("role") or "").lower()
            item = {
                "name": contact.get("name") or contact.get("full_name") or "N/A",
                "role": role or "caregiver",
                "phone": contact.get("phone") or "N/A",
            }
            if role in {"primary_caregiver", "secondary_caregiver", "caregiver"}:
                caregivers.append(item)
            elif role == "doctor" and doctor == _empty_contact():
                doctor = {"name": item["name"], "phone": item["phone"]}
            elif role == "hospital" and hospital == _empty_hospital():
                hospital = {"name": item["name"], "phone": item["phone"], "maps_link": contact.get("maps_link")}
        helpline = str(cache.get("government_helpline") or DEFAULT_HELPLINE)
        return (caregivers, hospital, doctor, helpline)
    except Exception:
        return ([], _empty_hospital(), _empty_contact(), DEFAULT_HELPLINE)


def _contacts_from_care_team(patient_id: str) -> tuple[list[dict], dict, dict]:
    try:
        contacts = db.get_care_team_contacts(patient_id)
        caregivers = []
        doctor = _empty_contact()
        hospital = _empty_hospital()
        for contact in contacts:
            role = contact.get("role")
            item = {
                "name": contact.get("name") or "N/A",
                "role": role or "caregiver",
                "phone": contact.get("phone") or "N/A",
            }
            if role in {"primary_caregiver", "secondary_caregiver"}:
                caregivers.append(item)
            elif role == "doctor":
                doctor = {"name": item["name"], "phone": item["phone"]}
            elif role == "hospital":
                hospital = {"name": item["name"], "phone": item["phone"], "maps_link": None}
        return (caregivers, hospital, doctor)
    except Exception:
        return ([], _empty_hospital(), _empty_contact())


def build_crisis_card(patient_id: str, patient_name: str) -> dict:
    """
    Assemble the full Crisis Card from the live database.
    """
    try:
        active_meds = db.get_active_medications_schedule(patient_id)
        latest_log = db.get_latest_medication_log(patient_id)
        location = db.get_patient_location(patient_id)
        hospital_preference = db.get_patient_hospital_preference(patient_id)
        cache = db.get_crisis_cache(patient_id)

        medications = [_format_medication(medication) for medication in active_meds]
        recent_medication = _format_recent_medication(latest_log)

        cache_caregivers, cache_hospital, cache_doctor, helpline = _contacts_from_cache(cache)
        team_caregivers, team_hospital, team_doctor = _contacts_from_care_team(patient_id)
        caregivers = cache_caregivers or team_caregivers
        hospital = cache_hospital if cache_hospital != _empty_hospital() else team_hospital
        doctor = cache_doctor if cache_doctor != _empty_contact() else team_doctor

        nearest_hospital = None
        if location:
            nearest_hospital = _nearest_hospital_from_osm(location["latitude"], location["longitude"])
            location = {
                "latitude": location["latitude"],
                "longitude": location["longitude"],
                "maps_link": _maps_link(location["latitude"], location["longitude"]),
            }
        else:
            location = {"latitude": None, "longitude": None, "maps_link": None}

        if not isinstance(hospital, dict):
            hospital = _empty_hospital()
        if not isinstance(doctor, dict):
            doctor = _empty_contact()
        hospital.setdefault("maps_link", None)
        if nearest_hospital:
            hospital = nearest_hospital
        if hospital.get("name") in {None, "", "N/A"}:
            if nearest_hospital:
                hospital = nearest_hospital
            elif hospital_preference:
                hospital["name"] = hospital_preference
            else:
                hospital["name"] = "Nearest hospital search"
        if nearest_hospital and hospital.get("name") == "Nearest hospital search":
            hospital = nearest_hospital
        if location and not hospital.get("maps_link"):
            hospital["maps_link"] = _hospital_search_link(
                location.get("latitude"),
                location.get("longitude"),
                hospital.get("name") if hospital.get("name") != "Nearest hospital search" else "hospital",
            )

        card = {
            "schema_version": CRISIS_PACKET_SCHEMA,
            "patient_name": patient_name,
            "medications": medications,
            "recent_medication": recent_medication,
            "hospital": hospital,
            "nearest_hospital": nearest_hospital,
            "government_helpline": helpline or DEFAULT_HELPLINE,
            "doctor": doctor,
            "caregivers": caregivers,
            "location": location,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        card["quality"] = score_crisis_card_quality(card)
        return card
    except Exception:
        card = {
            "patient_name": patient_name,
            "medications": [],
            "recent_medication": None,
            "schema_version": CRISIS_PACKET_SCHEMA,
            "hospital": _empty_hospital(),
            "nearest_hospital": None,
            "government_helpline": DEFAULT_HELPLINE,
            "doctor": _empty_contact(),
            "caregivers": [],
            "location": {"latitude": None, "longitude": None, "maps_link": None},
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        card["quality"] = score_crisis_card_quality(card)
        return card


def score_crisis_card_quality(card: dict) -> dict:
    """
    Score emergency-card completeness so missing operational data is visible.
    This is not a medical score; it is a data-quality/readiness score.
    """
    try:
        issues = []
        score = 100

        if not (card.get("caregivers") or []):
            issues.append("missing_caregiver_contacts")
            score -= 25
        if not (card.get("doctor") or {}).get("phone") or (card.get("doctor") or {}).get("phone") == "N/A":
            issues.append("missing_doctor_phone")
            score -= 15
        hospital = card.get("hospital") if isinstance(card.get("hospital"), dict) else {}
        if not hospital.get("maps_link"):
            issues.append("missing_hospital_map")
            score -= 15
        if not hospital.get("phone") or hospital.get("phone") == "N/A":
            issues.append("missing_hospital_phone")
            score -= 10
        location = card.get("location") if isinstance(card.get("location"), dict) else {}
        if not location.get("maps_link"):
            issues.append("missing_patient_location")
            score -= 20
        if not (card.get("medications") or []):
            issues.append("missing_active_medications")
            score -= 10
        if not card.get("recent_medication") or card.get("recent_medication") == "No recent data":
            issues.append("missing_recent_medication_log")
            score -= 5

        score = max(0, min(100, score))
        if score >= 85:
            status = "ready"
        elif score >= 60:
            status = "usable_needs_review"
        else:
            status = "incomplete"

        return {
            "score": score,
            "status": status,
            "issues": issues,
        }
    except Exception:
        return {"score": 0, "status": "unknown", "issues": ["quality_score_error"]}


def _truncate_word(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    shortened = text[: max_chars - 1].rstrip()
    if " " in shortened:
        shortened = shortened.rsplit(" ", 1)[0]
    return shortened.rstrip(" ,.;:") + "..."


def format_crisis_card(card: dict) -> str:
    """
    Format a WhatsApp-safe Crisis Card with a clear action hierarchy.
    """
    try:
        patient_name = card.get("patient_name") or "Patient"
        medications = list(card.get("medications") or [])
        recent = card.get("recent_medication") or "No recent data"
        hospital = card.get("hospital") if isinstance(card.get("hospital"), dict) else {}
        doctor = card.get("doctor") if isinstance(card.get("doctor"), dict) else {}
        caregivers = list(card.get("caregivers") or [])
        location = card.get("location") if isinstance(card.get("location"), dict) else {}

        quality = card.get("quality") if isinstance(card.get("quality"), dict) else {}
        hospital_name = hospital.get("name") or "Nearest hospital"
        hospital_phone = hospital.get("phone") or "N/A"
        hospital_map = hospital.get("maps_link") or "N/A"
        doctor_name = doctor.get("name") or "N/A"
        doctor_phone = doctor.get("phone") or "N/A"

        contact_lines = [
            "Emergency contacts:",
            f"- Hospital: {hospital_name}",
            f"- Hospital phone: {hospital_phone}",
            f"- Hospital map: {hospital_map}",
            f"- Helpline: {card.get('government_helpline') or DEFAULT_HELPLINE}",
            f"- Doctor: {doctor_name} ({doctor_phone})",
            "- Caregivers:",
        ]
        if caregivers:
            for caregiver in caregivers:
                contact_lines.append(
                    f"  * {caregiver.get('name') or 'N/A'} ({caregiver.get('role') or 'caregiver'}): {caregiver.get('phone') or 'N/A'}"
                )
        else:
            contact_lines.append("  * N/A")
        contact_lines.append(f"- Patient location: {location.get('maps_link') or 'N/A'}")
        contact_section = "\n".join(contact_lines)

        med_lines = ["Current medicines:"]
        if medications:
            med_lines.extend(f"- {medication}" for medication in medications)
        else:
            med_lines.append("- No active medications recorded")

        header = f"EMERGENCY CARD - {patient_name}"
        action_lines = "\n".join(
            [
                "Immediate actions:",
                "1. Call emergency services or go to the nearest hospital.",
                "2. Keep the patient seated/lying safely.",
                "3. Share this card with the doctor or ambulance team.",
            ]
        )
        recent_line = f"Recent: {recent}"
        quality_line = ""
        if quality:
            quality_line = f"Readiness: {quality.get('status', 'unknown')} ({quality.get('score', 0)}/100)"
        body = "\n".join(
            part
            for part in [header, action_lines, "\n".join(med_lines), recent_line, contact_section, quality_line]
            if part
        )

        if len(body) <= MAX_CRISIS_MESSAGE_CHARS:
            return body

        fixed_tail = "\n".join([recent_line, contact_section])
        available = MAX_CRISIS_MESSAGE_CHARS - len(header) - len(fixed_tail) - 2
        compact_meds = _truncate_word("\n".join(med_lines), max(40, available))
        body = "\n".join([header, compact_meds, fixed_tail])
        return _truncate_word(body, MAX_CRISIS_MESSAGE_CHARS)
    except Exception:
        return "EMERGENCY MODE - Patient\nMedications:\n   - No active medications recorded\nRecent: No recent data\nHospital: N/A - N/A (maps: N/A)\nGovernment Helpline: 112\nDoctor: N/A - N/A\nCaregivers:\n   - N/A\nPatient Location: N/A"


def _is_complete_crisis_packet(packet: dict) -> bool:
    required_keys = {
        "patient_name",
        "medications",
        "recent_medication",
        "hospital",
        "government_helpline",
        "doctor",
        "caregivers",
        "location",
        "generated_at",
        "schema_version",
    }
    return (
        isinstance(packet, dict)
        and required_keys.issubset(packet.keys())
        and packet.get("schema_version") == CRISIS_PACKET_SCHEMA
    )


def get_emergency_packet(patient_id: str) -> dict:
    """
    Return a fresh cached emergency packet or build and cache a new one.
    """
    try:
        cached = db.get_crisis_cache(patient_id)
        if _is_complete_crisis_packet(cached):
            if isinstance(cached, dict) and "quality" not in cached:
                cached["quality"] = score_crisis_card_quality(cached)
            return cached

        patient_name = db.get_patient_name(patient_id) or "Patient"
        packet = build_crisis_card(patient_id, patient_name)
        db.upsert_crisis_cache(patient_id, packet)
        return packet
    except Exception:
        return build_crisis_card(patient_id, "Patient")


# ENHANCEMENT IDEA: Add "Next Scheduled Dose" by parsing each medication's
# scheduled_times, selecting the first dose after datetime.now(), and showing:
# "⏰ Next Dose: Metformin at 8:00 PM". Keep this deterministic and skip meds
# without scheduled_times rather than guessing from frequency in emergency mode.
#
# SAFETY NOTE: If scheduled_times is empty, this module omits the parentheses
# instead of inventing a schedule. Emergency packets should prefer incomplete
# but truthful data over a confident guess.
#
# SAFETY NOTE: If latest medication log is "missed", still show it. In a crisis,
# "Amlodipine missed at 10:00 PM" is clinically relevant and should not be
# hidden behind a taken-only filter.
