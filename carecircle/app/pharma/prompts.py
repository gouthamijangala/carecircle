"""
Prompt templates for structured medical document extraction.

These prompts preserve the current CareCircle JSON contracts:
- prescriptions use top-level "medications"
- lab reports use top-level "lab_values"
- voice/general notes use top-level "events"

Additional document-specific metadata is allowed, but the canonical arrays above
must remain stable because ingestion.py and llm_gateway.py map them to DB writes.
"""

from __future__ import annotations


PRESCRIPTION_PROMPT = """Extract ONLY medication orders from the source text.
Return JSON ONLY. Do not add markdown, explanations, or extra keys.

Canonical JSON structure:
{
  "type": "prescription",
  "medications": [
    {
      "drug_name_raw": "exact drug name as written",
      "drug_name_canonical": null,
      "dose_amount": null,
      "dose_unit": null,
      "frequency": null,
      "instructions": null,
      "advice": null,
      "prescribed_by": null,
      "start_date": null,
      "date": null
    }
  ],
  "doctor_name": null,
  "patient_name": null,
  "date": null,
  "start_date": null
}

Rules:
- Extract every medication line, not only the first.
- Medication lines include tablets, capsules, injections, syrups, suspensions, drops, creams, ointments, gels, sprays, inhalers, nebulization solutions, sachets, vials, ampoules, and any other medicine form.
- Use drug_name_raw exactly as written in the text. Never invent a drug name.
- Handle Indian brand names, Hinglish directions, and common prescription abbreviations.
- Preserve combination brands such as Telma-AM, Ecosprin-AV, or Met-XL as written in drug_name_raw.
- dose_amount must be a number, not a string. If unclear, use null.
- Convert frequency: 1-0-0=OD, 0-1-0=afternoon, 0-0-1=HS, 1-0-1=BD, 1-1-1=TDS.
- Keep instructions such as after food, before food, morning, night, SOS.
- Put medication-specific doctor advice such as gargle, apply locally, shake well, dilute, take with steam, complete course, or avoid alcohol in advice.
- The prescription date is the medication start_date. Copy it into top-level start_date and into each medication start_date when visible.
- prescribed_by should be the doctor's specialty or department if visible (for example cardiologist, dentist, dermatologist), not the doctor's personal name.
- If no medication is present, return {"type":"prescription","medications":[],"doctor_name":null,"patient_name":null,"date":null,"start_date":null}.

Text:
{raw_text}
/no_think"""


LAB_REPORT_PROMPT = """Extract ONLY lab test results from the source text.
Return JSON ONLY. Do not add markdown, explanations, or extra keys.

Canonical JSON structure:
{
  "type": "lab_report",
  "lab_values": [
    {
      "test_name": "normalized_test_name",
      "test_value": null,
      "unit": null,
      "reference_range_low": null,
      "reference_range_high": null,
      "flag": null
    }
  ],
  "patient_name": null,
  "report_date": null,
  "lab_name": null
}

Rules:
- Extract every visible test result, not only the first.
- test_name must be lowercase with spaces replaced by underscores.
- Preserve kidney markers such as creatinine, eGFR, urea, and blood urea nitrogen.
- Preserve decimals exactly. test_value must be a number, never a string.
- Parse examples like "11.2 g/dL", "13.0-17.0", "H", "L".
- If a result has two numbers such as BP 140/90, create separate systolic and diastolic entries.
- CRITICAL: each numeric value must appear in the source text. If it does not, use null.
- If no test result is present, return {"type":"lab_report","lab_values":[],"patient_name":null,"report_date":null,"lab_name":null}.

Text:
{raw_text}
/no_think"""


VOICE_NOTE_PROMPT = """Extract caregiver voice-note events from the transcript.
Return JSON ONLY. Do not add markdown, explanations, or extra keys.

Canonical JSON structure:
{
  "type": "general_note",
  "events": [
    {
      "event_type": "taken|missed|symptom|diet|sleep|activity|mood|other",
      "medication_name": null,
      "time_of_day": null,
      "description": null,
      "details": null
    }
  ],
  "caregiver_notes": null
}

Rules:
- Handle Hinglish and code-mixed text.
- Extract medication taken/missed events separately from symptoms.
- If a field is unclear, use null. Never guess.

Text:
{raw_text}
/no_think"""


ADVICE_NOTE_PROMPT = """Extract lifestyle, diet, warning, and follow-up advice.
Return JSON ONLY. Do not add markdown, explanations, or extra keys.

Canonical JSON structure:
{
  "type": "general_note",
  "events": [
    {
      "event_type": "diet|activity|follow_up|warning|other",
      "category": null,
      "instruction": null,
      "duration": null,
      "frequency": null,
      "description": null
    }
  ],
  "follow_up_date": null
}

Rules:
- Categories must be diet, exercise, warning, follow_up, or other.
- Keep instructions short and literal to the source text.
- If unclear, use null.

Text:
{raw_text}
/no_think"""


DISCHARGE_PROMPT = """Extract discharge summary data.
Return JSON ONLY. Do not add markdown, explanations, or extra keys.

Canonical JSON structure:
{
  "type": "general_note",
  "events": [
    {
      "event_type": "discharge",
      "diagnosis": null,
      "discharge_medications": [],
      "follow_up_instructions": null,
      "next_appointment_date": null,
      "description": null
    }
  ],
  "diagnosis": null,
  "discharge_medications": [],
  "follow_up_instructions": null,
  "next_appointment_date": null
}

Rules:
- Extract diagnosis, discharge medications, follow-up instructions, and appointment date.
- If medication rows are detailed with dose/frequency, preserve them as strings in discharge_medications.
- If unclear, use null or an empty list.

Text:
{raw_text}
/no_think"""


REFERRAL_PROMPT = """Extract referral letter data.
Return JSON ONLY. Do not add markdown, explanations, or extra keys.

Canonical JSON structure:
{
  "type": "general_note",
  "events": [
    {
      "event_type": "referral",
      "specialist_name": null,
      "reason_for_referral": null,
      "urgency": null,
      "referring_doctor": null,
      "description": null
    }
  ],
  "specialist_name": null,
  "reason_for_referral": null,
  "urgency": null,
  "referring_doctor": null
}

Rules:
- urgency must be routine, urgent, emergency, or null.
- Preserve the referral reason exactly when possible.
- If unclear, use null.

Text:
{raw_text}
/no_think"""


MEDICAL_HISTORY_PROMPT = """Extract medical history data.
Return JSON ONLY. Do not add markdown, explanations, or extra keys.

Canonical JSON structure:
{
  "type": "medical_history",
  "past_conditions": [],
  "surgeries": [],
  "allergies": [],
  "family_history": []
}

Rules:
- Handle Hinglish and code-mixed medical history.
- Include only history clearly stated in the text.
- Use empty lists when nothing is found. If one item is unclear, use null for that item.
- Never invent conditions, allergies, surgeries, or family history.

Text:
{raw_text}
/no_think"""


GENERAL_NOTE_PROMPT = """Extract general medical note metadata.
Return JSON ONLY. Do not add markdown, explanations, or extra keys.

Canonical JSON structure:
{
  "type": "general_note",
  "events": [
    {
      "event_type": "symptom|diet|sleep|activity|mood|other",
      "description": null,
      "time_of_day": null
    }
  ],
  "note_type": null,
  "key_points": [],
  "action_required": false
}

Rules:
- key_points must be short strings copied or tightly paraphrased from the source.
- action_required is true only when the note clearly asks for follow-up, review, or urgent action.
- If unclear, use null for strings and false for action_required.

Text:
{raw_text}
/no_think"""


def get_prompt_for_type(doc_type: str) -> str:
    return {
        "prescription": PRESCRIPTION_PROMPT,
        "lab_report": LAB_REPORT_PROMPT,
        "voice_note": VOICE_NOTE_PROMPT,
        "advice_note": ADVICE_NOTE_PROMPT,
        "discharge_summary": DISCHARGE_PROMPT,
        "referral_letter": REFERRAL_PROMPT,
        "medical_history": MEDICAL_HISTORY_PROMPT,
        "general_note": GENERAL_NOTE_PROMPT,
    }.get(doc_type, GENERAL_NOTE_PROMPT)

