import hashlib
import json
from datetime import date, datetime, timedelta

import config


DEFAULT_DRUG_INTERACTION_RULES = [
    ("warfarin", "aspirin", "bleeding_risk", "critical", "Warfarin with aspirin can greatly increase bleeding risk. Contact the doctor before combining."),
    ("warfarin", "clopidogrel", "bleeding_risk", "critical", "Warfarin with clopidogrel can greatly increase bleeding risk. Urgent doctor review is needed."),
    ("warfarin", "ibuprofen", "bleeding_risk", "critical", "Warfarin with ibuprofen can increase serious bleeding risk. Avoid unless a doctor specifically approves."),
    ("warfarin", "naproxen", "bleeding_risk", "critical", "Warfarin with naproxen can increase serious bleeding risk. Avoid unless a doctor specifically approves."),
    ("warfarin", "amiodarone", "warfarin_level_increase", "critical", "Amiodarone can raise warfarin effect and INR. Doctor monitoring is needed."),
    ("warfarin", "metronidazole", "warfarin_level_increase", "critical", "Metronidazole can raise warfarin effect and bleeding risk. Doctor monitoring is needed."),
    ("warfarin", "fluconazole", "warfarin_level_increase", "critical", "Fluconazole can raise warfarin effect and bleeding risk. Doctor monitoring is needed."),
    ("digoxin", "clarithromycin", "digoxin_toxicity", "critical", "Clarithromycin can raise digoxin levels and toxicity risk. Doctor review is needed."),
    ("digoxin", "amiodarone", "digoxin_toxicity", "critical", "Amiodarone can raise digoxin levels and toxicity risk. Doctor review is needed."),
    ("sildenafil", "nitroglycerin", "hypotension", "critical", "Sildenafil with nitroglycerin can cause dangerous blood pressure drops. Do not combine without emergency medical guidance."),
    ("aspirin", "clopidogrel", "bleeding_risk", "high", "Aspirin with clopidogrel increases bleeding risk. Confirm this combination and monitor for bleeding."),
    ("aspirin", "ibuprofen", "bleeding_risk", "high", "Aspirin with ibuprofen can increase bleeding risk and reduce aspirin benefit. Ask the doctor or pharmacist."),
    ("aspirin", "naproxen", "bleeding_risk", "high", "Aspirin with naproxen can increase bleeding risk. Ask the doctor or pharmacist."),
    ("clopidogrel", "omeprazole", "reduced_antiplatelet_effect", "high", "Omeprazole may reduce clopidogrel effect. Ask the doctor if pantoprazole is preferred."),
    ("metoprolol", "ivabradine", "bradycardia", "high", "Metoprolol with ivabradine can slow heart rate too much. Monitor pulse and contact the doctor."),
    ("atenolol", "ivabradine", "bradycardia", "high", "Atenolol with ivabradine can slow heart rate too much. Monitor pulse and contact the doctor."),
    ("verapamil", "ivabradine", "bradycardia", "critical", "Verapamil with ivabradine can cause unsafe heart-rate slowing. Doctor review is needed."),
    ("diltiazem", "ivabradine", "bradycardia", "critical", "Diltiazem with ivabradine can cause unsafe heart-rate slowing. Doctor review is needed."),
    ("ramipril", "spironolactone", "hyperkalemia", "high", "Ramipril with spironolactone can raise potassium. Doctor monitoring of potassium and kidney function is needed."),
    ("telmisartan", "spironolactone", "hyperkalemia", "high", "Telmisartan with spironolactone can raise potassium. Doctor monitoring of potassium and kidney function is needed."),
    ("losartan", "spironolactone", "hyperkalemia", "high", "Losartan with spironolactone can raise potassium. Doctor monitoring of potassium and kidney function is needed."),
    ("enalapril", "spironolactone", "hyperkalemia", "high", "Enalapril with spironolactone can raise potassium. Doctor monitoring of potassium and kidney function is needed."),
    ("ramipril", "potassium chloride", "hyperkalemia", "high", "Ramipril with potassium supplements can raise potassium. Doctor monitoring is needed."),
    ("telmisartan", "potassium chloride", "hyperkalemia", "high", "Telmisartan with potassium supplements can raise potassium. Doctor monitoring is needed."),
    ("metformin", "contrast dye", "renal_lactic_acidosis_risk", "high", "Metformin around iodinated contrast needs kidney-function guidance from the doctor."),
    ("metformin", "furosemide", "renal_dehydration_risk", "medium", "Metformin with furosemide needs hydration and kidney-function awareness, especially in older adults."),
    ("furosemide", "digoxin", "electrolyte_digoxin_risk", "high", "Furosemide can lower potassium and increase digoxin toxicity risk. Monitor electrolytes and symptoms."),
    ("digoxin", "verapamil", "digoxin_toxicity", "high", "Verapamil can raise digoxin levels. Doctor monitoring may be needed."),
    ("digoxin", "diltiazem", "digoxin_toxicity", "high", "Diltiazem can raise digoxin levels. Doctor monitoring may be needed."),
    ("atorvastatin", "clarithromycin", "statin_toxicity", "high", "Clarithromycin can raise atorvastatin levels and muscle injury risk. Ask the doctor."),
    ("simvastatin", "clarithromycin", "statin_toxicity", "critical", "Clarithromycin with simvastatin can cause serious muscle injury. Doctor review is needed."),
    ("rosuvastatin", "cyclosporine", "statin_toxicity", "critical", "Cyclosporine can greatly raise rosuvastatin exposure. Doctor review is needed."),
    ("amlodipine", "simvastatin", "statin_exposure", "medium", "Amlodipine can increase simvastatin exposure. Confirm the dose with the doctor."),
    ("amlodipine", "clarithromycin", "hypotension", "high", "Clarithromycin can raise amlodipine levels and cause low blood pressure. Monitor dizziness and contact the doctor."),
    ("telmisartan", "ibuprofen", "kidney_risk", "medium", "Telmisartan with ibuprofen can affect kidney function, especially with dehydration or older age."),
    ("telmisartan", "naproxen", "kidney_risk", "medium", "Telmisartan with naproxen can affect kidney function, especially with dehydration or older age."),
    ("aspirin", "telmisartan", "kidney_bp_risk", "medium", "Aspirin with telmisartan can affect kidney function or blood pressure in some patients. Monitor as advised."),
    ("clopidogrel", "pantoprazole", "monitoring", "low", "Pantoprazole is commonly used with clopidogrel, but confirm long-term need with the doctor."),
    ("rosuvastatin", "clopidogrel", "monitoring", "low", "Rosuvastatin with clopidogrel is commonly used in heart care; monitor for unusual muscle pain or bleeding."),
    ("atorvastatin", "clopidogrel", "monitoring", "low", "Atorvastatin with clopidogrel is commonly used in heart care; monitor for unusual muscle pain or bleeding."),
]


DEFAULT_RENAL_DOSING_RULES = [
    ("metformin", 60.0, 45.0, 30.0, "high", "Metformin needs kidney-function review before or during use."),
    ("furosemide", 60.0, 45.0, 30.0, "medium", "Furosemide needs hydration, electrolyte, and kidney-function monitoring."),
    ("digoxin", 60.0, 45.0, 30.0, "high", "Digoxin needs kidney-function and toxicity monitoring."),
    ("gabapentin", 60.0, 45.0, 30.0, "high", "Gabapentin often needs renal dose adjustment."),
    ("pregabalin", 60.0, 45.0, 30.0, "high", "Pregabalin often needs renal dose adjustment."),
    ("acyclovir", 60.0, 45.0, 30.0, "high", "Acyclovir needs renal dosing review and hydration monitoring."),
    ("valacyclovir", 60.0, 45.0, 30.0, "high", "Valacyclovir needs renal dosing review."),
    ("vancomycin", 60.0, 45.0, 30.0, "high", "Vancomycin needs renal function and drug-level monitoring."),
    ("rivaroxaban", 60.0, 45.0, 30.0, "high", "Rivaroxaban dosing depends on kidney function and bleeding risk."),
    ("apixaban", 60.0, 45.0, 30.0, "medium", "Apixaban may need renal and age/weight review."),
    ("dabigatran", 60.0, 45.0, 30.0, "high", "Dabigatran is kidney-cleared and needs renal review."),
    ("lithium", 60.0, 45.0, 30.0, "critical", "Lithium can become toxic when kidney function is reduced."),
    ("methotrexate", 60.0, 45.0, 30.0, "critical", "Methotrexate needs renal safety review because toxicity can be serious."),
    ("nitrofurantoin", 60.0, 45.0, 30.0, "medium", "Nitrofurantoin may be ineffective or unsafe with reduced kidney function."),
    ("spironolactone", 60.0, 45.0, 30.0, "high", "Spironolactone can raise potassium, especially with kidney impairment."),
    ("ramipril", 60.0, 45.0, 30.0, "medium", "Ramipril needs kidney and potassium monitoring."),
    ("telmisartan", 60.0, 45.0, 30.0, "medium", "Telmisartan needs kidney and potassium monitoring."),
    ("losartan", 60.0, 45.0, 30.0, "medium", "Losartan needs kidney and potassium monitoring."),
    ("ibuprofen", 60.0, 45.0, 30.0, "high", "Ibuprofen can worsen kidney function in vulnerable patients."),
    ("naproxen", 60.0, 45.0, 30.0, "high", "Naproxen can worsen kidney function in vulnerable patients."),
]


def _connect():
    import psycopg2
    from config import SUPABASE_KEY, SUPABASE_URL

    _ = SUPABASE_KEY
    return psycopg2.connect(SUPABASE_URL)


def get_profile_by_phone(phone: str) -> dict | None:
    try:
        connection = _connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT p.id, p.full_name, ct.role, ct.patient_id,
                           patient_profile.full_name
                    FROM profiles p
                    LEFT JOIN care_team ct ON p.id = ct.profile_id
                    LEFT JOIN patients patient_record ON ct.patient_id = patient_record.id
                    LEFT JOIN profiles patient_profile ON patient_record.profile_id = patient_profile.id
                    WHERE p.phone = %s
                    LIMIT 1;
                    """,
                    (phone,),
                )
                row = cursor.fetchone()
        finally:
            connection.close()

        if row is None:
            return None

        return {
            "id": row[0],
            "full_name": row[1],
            "role": row[2],
            "patient_id": row[3],
            "phone": phone,
            "patient_name": row[4] or row[1],
        }
    except Exception as error:
        print(error)
        return None


def get_active_medications_schedule(patient_id: str) -> list[dict]:
    try:
        connection = _connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id::text, drug_name, dose_amount::text, dose_unit,
                           frequency, scheduled_times, instructions, advice,
                           prescribed_by, source_type, recorded_at::text, start_date::text
                    FROM medications
                    WHERE patient_id = %s
                      AND status = 'active'
                    ORDER BY drug_name;
                    """,
                    (patient_id,),
                )
                rows = cursor.fetchall()
        finally:
            connection.close()

        return [
            {
                "id": row[0],
                "drug_name": row[1],
                "dose_amount": row[2] or "",
                "dose_unit": row[3] or "",
                "frequency": row[4] or "",
                "scheduled_times": list(row[5] or []),
                "instructions": row[6] or "",
                "advice": row[7] or "",
                "prescribed_by": row[8] or "",
                "source_type": row[9] or "",
                "recorded_at": row[10],
                "start_date": row[11],
            }
            for row in rows
        ]
    except Exception as error:
        print(error)
        return []


def get_active_medications(patient_id: str) -> list[dict]:
    return get_active_medications_schedule(patient_id)


def get_recent_medications(patient_id: str, limit: int = 5) -> list[dict]:
    """
    Fetch active medications ordered by most recently recorded.
    """
    try:
        connection = _connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT drug_name, dose_amount::text, dose_unit, frequency, scheduled_times
                    FROM medications
                    WHERE patient_id = %s
                      AND status = 'active'
                    ORDER BY recorded_at DESC
                    LIMIT %s;
                    """,
                    (patient_id, limit),
                )
                rows = cursor.fetchall()
        finally:
            connection.close()

        return [
            {
                "drug_name": row[0],
                "dose_amount": row[1] or "",
                "dose_unit": row[2] or "",
                "frequency": row[3] or "",
                "scheduled_times": list(row[4] or []),
            }
            for row in rows
        ]
    except Exception as error:
        print(error)
        return []


def get_medication_log_for_date(patient_id: str, target_date: date) -> list[dict]:
    try:
        connection = _connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT medication_id::text, event_type, reported_at::text, raw_text
                    FROM medication_log
                    WHERE patient_id = %s
                      AND event_date = %s
                    ORDER BY reported_at DESC;
                    """,
                    (patient_id, target_date),
                )
                rows = cursor.fetchall()
        finally:
            connection.close()

        return [
            {
                "medication_id": row[0],
                "event_type": row[1],
                "reported_at": row[2],
                "raw_text": row[3] or "",
            }
            for row in rows
        ]
    except Exception as error:
        print(error)
        return []


def get_recent_vitals(patient_id: str, limit: int = 3) -> list[dict]:
    connection = None
    try:
        vital_names = (
            "blood_pressure_systolic",
            "blood_pressure_diastolic",
            "pulse",
            "spo2",
            "temperature",
            "glucose",
        )
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT test_name, test_value::float, unit, report_date::text,
                       reference_range_low::float, reference_range_high::float,
                       created_at::text
                FROM lab_reports
                WHERE patient_id = %s
                  AND LOWER(test_name) = ANY(%s)
                ORDER BY report_date DESC, created_at DESC
                LIMIT %s;
                """,
                (patient_id, list(vital_names), int(limit or 3)),
            )
            rows = cursor.fetchall()
        vitals = []
        for row in rows:
            value = row[1]
            low = row[4]
            high = row[5]
            severity = "normal"
            if value is not None and ((low is not None and value < low) or (high is not None and value > high)):
                severity = "critical" if row[0] in {"spo2", "blood_pressure_systolic"} else "advisory"
            vitals.append(
                {
                    "test_name": row[0],
                    "value": value,
                    "unit": row[2] or "",
                    "report_date": row[3],
                    "reference_range_low": low,
                    "reference_range_high": high,
                    "created_at": row[6],
                    "severity": severity,
                }
            )
        return vitals
    except Exception as error:
        print(error)
        return []
    finally:
        if connection is not None:
            connection.close()


def get_recent_labs(patient_id: str, limit: int = 3) -> list[dict]:
    try:
        connection = _connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT test_name, test_value::float, unit, report_date::text,
                           reference_range_low::float, reference_range_high::float,
                           confidence
                    FROM lab_reports
                    WHERE patient_id = %s
                    ORDER BY report_date DESC, created_at DESC
                    LIMIT %s;
                    """,
                    (patient_id, limit),
                )
                rows = cursor.fetchall()
        finally:
            connection.close()

        labs = []
        for row in rows:
            value = row[1]
            low = row[4]
            high = row[5]
            severity = "normal"
            if value is not None and ((low is not None and value < low) or (high is not None and value > high)):
                severity = "advisory"
            labs.append(
                {
                    "test_name": row[0],
                    "value": value,
                    "unit": row[2] or "",
                    "report_date": row[3],
                    "reference_range_low": low,
                    "reference_range_high": high,
                    "confidence": row[6] if row[6] is not None else 0.8,
                    "severity": severity,
                }
            )
        return labs
    except Exception as error:
        print(error)
        return []


def get_open_alerts(patient_id: str) -> list[dict]:
    try:
        connection = _connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id::text, type, severity, message_template,
                           data_payload, created_at::text
                    FROM alerts
                    WHERE patient_id = %s
                      AND status = 'open'
                    ORDER BY created_at DESC;
                    """,
                    (patient_id,),
                )
                rows = cursor.fetchall()
        finally:
            connection.close()

        return [
            {
                "id": row[0],
                "type": row[1],
                "severity": row[2],
                "message": row[3] or "",
                "data_payload": row[4] or {},
                "created_at": row[5],
            }
            for row in rows
        ]
    except Exception as error:
        print(error)
        return []


def get_active_patient_ids() -> list[str]:
    """
    Return patients with active care data. Used by schedulers.
    """
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT patient_id::text
                FROM (
                    SELECT patient_id FROM medications WHERE status = 'active'
                    UNION
                    SELECT patient_id FROM alerts WHERE status = 'open'
                    UNION
                    SELECT patient_id FROM care_team WHERE is_active = TRUE
                ) source
                WHERE patient_id IS NOT NULL
                ORDER BY patient_id::text;
                """
            )
            return [row[0] for row in cursor.fetchall()]
    except Exception as error:
        print(error)
        return []
    finally:
        if connection is not None:
            connection.close()


def _table_exists(cursor, table_name: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = %s
        LIMIT 1;
        """,
        (table_name,),
    )
    return cursor.fetchone() is not None


def ensure_care_coordination_tables() -> bool:
    """
    Create optional coordination tables used by automatic caregiver briefs.
    Safe to run repeatedly; existing installations with their own tables can
    continue using them as long as column names match these helpers.
    """
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS care_appointments (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    patient_id UUID REFERENCES patients(id) ON DELETE CASCADE,
                    appointment_type TEXT NOT NULL DEFAULT 'doctor',
                    title TEXT,
                    appointment_at TIMESTAMPTZ NOT NULL,
                    location TEXT,
                    provider_name TEXT,
                    department TEXT,
                    notes TEXT,
                    status TEXT NOT NULL DEFAULT 'scheduled',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_care_appointments_patient_time
                ON care_appointments (patient_id, appointment_at)
                WHERE status IN ('scheduled', 'confirmed');
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS caregiver_visits (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    patient_id UUID REFERENCES patients(id) ON DELETE CASCADE,
                    caregiver_profile_id UUID REFERENCES profiles(id),
                    caregiver_name TEXT,
                    visit_at TIMESTAMPTZ NOT NULL,
                    purpose TEXT,
                    brief TEXT,
                    status TEXT NOT NULL DEFAULT 'planned',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_caregiver_visits_patient_time
                ON caregiver_visits (patient_id, visit_at)
                WHERE status IN ('planned', 'scheduled', 'completed');
                """
            )
        connection.commit()
        return True
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(f"ensure_care_coordination_tables failed: {error}")
        return False
    finally:
        if connection is not None:
            connection.close()


def ensure_appointment_workflow_schema() -> bool:
    """
    Ensure appointment tables and appointment confirmation contexts are usable.
    This is additive: it preserves existing rows and only broadens pending_tasks
    task_type support when an older CHECK constraint is present.
    """
    connection = None
    try:
        ensure_care_coordination_tables()
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT conname
                FROM pg_constraint
                WHERE conrelid = 'public.pending_tasks'::regclass
                  AND contype = 'c'
                  AND pg_get_constraintdef(oid) ILIKE '%task_type%';
                """
            )
            for row in cursor.fetchall():
                constraint_name = str(row[0] or "")
                if constraint_name.replace("_", "").isalnum():
                    cursor.execute(f"ALTER TABLE public.pending_tasks DROP CONSTRAINT IF EXISTS {constraint_name};")
            cursor.execute(
                """
                ALTER TABLE public.pending_tasks
                ADD CONSTRAINT pending_tasks_task_type_check
                CHECK (task_type IN (
                    'ocr_prescription',
                    'transcribe_audio',
                    'parse_pdf',
                    'llm_reasoning',
                    'pharm_research',
                    'medication_confirmation',
                    'veto_window',
                    'approval_window',
                    'pending_approval',
                    'interaction_alert',
                    'new_med',
                    'appointment_confirm',
                    'appointment_draft'
                ));
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pending_tasks_phone_appointment
                ON public.pending_tasks (from_phone, created_at DESC)
                WHERE task_type = 'appointment_confirm' AND status = 'queued';
                """
            )
        connection.commit()
        return True
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(f"ensure_appointment_workflow_schema failed: {error}")
        return False
    finally:
        if connection is not None:
            connection.close()


def get_upcoming_appointments(patient_id: str, days: int = 14, limit: int = 5) -> list[dict]:
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            if not _table_exists(cursor, "care_appointments"):
                return []
            cursor.execute(
                """
                SELECT id::text, appointment_type, title, appointment_at::text,
                       location, provider_name, department, notes, status
                FROM care_appointments
                WHERE patient_id = %s
                  AND appointment_at >= NOW()
                  AND appointment_at < NOW() + (%s || ' days')::interval
                  AND status IN ('scheduled', 'confirmed')
                ORDER BY appointment_at ASC
                LIMIT %s;
                """,
                (_uuid_or_none(patient_id), int(days or 14), int(limit or 5)),
            )
            rows = cursor.fetchall()
        return [
            {
                "id": row[0],
                "appointment_type": row[1],
                "title": row[2] or row[1] or "Appointment",
                "appointment_at": row[3],
                "location": row[4] or "",
                "provider_name": row[5] or "",
                "department": row[6] or "",
                "notes": row[7] or "",
                "status": row[8] or "",
            }
            for row in rows
        ]
    except Exception as error:
        print(error)
        return []
    finally:
        if connection is not None:
            connection.close()


def get_care_appointments(
    patient_id: str,
    start_at: datetime | str | None = None,
    end_at: datetime | str | None = None,
    statuses: list[str] | tuple[str, ...] | None = None,
    limit: int = 10,
    direction: str = "asc",
) -> list[dict]:
    """
    Fetch care appointments for flexible query handlers.
    Direction controls ordering only; date bounds decide future vs history.
    """
    connection = None
    try:
        connection = _connect()
        safe_limit = max(1, min(int(limit or 10), 50))
        order = "DESC" if str(direction or "").lower() == "desc" else "ASC"
        status_values = list(statuses or ["scheduled", "confirmed", "tentative", "completed", "cancelled", "declined"])
        with connection.cursor() as cursor:
            if not _table_exists(cursor, "care_appointments"):
                return []
            cursor.execute(
                f"""
                SELECT id::text, appointment_type, title, appointment_at::text,
                       location, provider_name, department, notes, status, created_at::text
                FROM care_appointments
                WHERE patient_id = %s
                  AND (%s::timestamptz IS NULL OR appointment_at >= %s::timestamptz)
                  AND (%s::timestamptz IS NULL OR appointment_at < %s::timestamptz)
                  AND status = ANY(%s)
                ORDER BY appointment_at {order}
                LIMIT %s;
                """,
                (
                    _uuid_or_none(patient_id),
                    start_at,
                    start_at,
                    end_at,
                    end_at,
                    status_values,
                    safe_limit,
                ),
            )
            rows = cursor.fetchall()
        return [
            {
                "id": row[0],
                "appointment_type": row[1],
                "title": row[2] or row[1] or "Appointment",
                "appointment_at": row[3],
                "location": row[4] or "",
                "provider_name": row[5] or "",
                "department": row[6] or "",
                "notes": row[7] or "",
                "status": row[8] or "",
                "created_at": row[9],
            }
            for row in rows
        ]
    except Exception as error:
        print(error)
        return []
    finally:
        if connection is not None:
            connection.close()


def create_care_appointment(
    patient_id: str,
    appointment_type: str,
    title: str | None,
    appointment_at: datetime | str,
    location: str | None = None,
    provider_name: str | None = None,
    department: str | None = None,
    notes: str | None = None,
    status: str = "scheduled",
) -> str | None:
    connection = None
    try:
        ensure_care_coordination_tables()
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO care_appointments (
                    patient_id, appointment_type, title, appointment_at,
                    location, provider_name, department, notes, status
                )
                VALUES (%s, %s, %s, %s::timestamptz, %s, %s, %s, %s, %s)
                RETURNING id::text;
                """,
                (
                    _uuid_or_none(patient_id),
                    appointment_type or "doctor",
                    title,
                    appointment_at,
                    location,
                    provider_name,
                    department,
                    notes,
                    status or "scheduled",
                ),
            )
            row = cursor.fetchone()
        connection.commit()
        return row[0] if row else None
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
        return None
    finally:
        if connection is not None:
            connection.close()


def update_care_appointment(
    appointment_id: str,
    status: str | None = None,
    appointment_at: datetime | str | None = None,
    notes: str | None = None,
) -> bool:
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE care_appointments
                SET status = COALESCE(%s, status),
                    appointment_at = COALESCE(%s::timestamptz, appointment_at),
                    notes = CASE
                        WHEN %s IS NULL OR BTRIM(%s) = '' THEN notes
                        WHEN notes IS NULL OR BTRIM(notes) = '' THEN %s
                        ELSE notes || E'\n' || %s
                    END
                WHERE id = %s
                RETURNING id;
                """,
                (
                    status,
                    appointment_at,
                    notes,
                    notes,
                    notes,
                    notes,
                    _uuid_or_none(appointment_id),
                ),
            )
            row = cursor.fetchone()
        connection.commit()
        return row is not None
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
        return False
    finally:
        if connection is not None:
            connection.close()


def delete_care_appointment(appointment_id: str) -> bool:
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM care_appointments WHERE id = %s RETURNING id;",
                (_uuid_or_none(appointment_id),),
            )
            row = cursor.fetchone()
            cursor.execute(
                """
                UPDATE pending_tasks
                SET status = 'failed',
                    completed_at = NOW(),
                    error_message = 'appointment_deleted'
                WHERE task_type = 'appointment_confirm'
                  AND status = 'queued'
                  AND payload ->> 'appointment_id' = %s;
                """,
                (str(appointment_id),),
            )
        connection.commit()
        return row is not None
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
        return False
    finally:
        if connection is not None:
            connection.close()


def create_appointment_confirmation_task(
    patient_id: str,
    appointment_id: str,
    from_phone: str | None,
    expires_at: datetime | str | None = None,
) -> str | None:
    connection = None
    payload = {
        "appointment_id": str(appointment_id or ""),
        "target_id": str(appointment_id or ""),
        "entity_id": str(appointment_id or ""),
        "expires_at": expires_at.isoformat() if hasattr(expires_at, "isoformat") else expires_at,
    }
    try:
        ensure_appointment_workflow_schema()
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO pending_tasks (
                    task_type, payload, status, patient_id, from_phone
                )
                VALUES ('appointment_confirm', %s::jsonb, 'queued', %s, %s)
                RETURNING id::text;
                """,
                (_json_or_empty(payload), _uuid_or_none(patient_id), from_phone),
            )
            row = cursor.fetchone()
        connection.commit()
        return row[0] if row else None
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
        return None
    finally:
        if connection is not None:
            connection.close()


def create_appointment_draft_task(
    patient_id: str,
    from_phone: str | None,
    raw_text: str,
    parsed: dict | None = None,
) -> str | None:
    connection = None
    payload = {
        "raw_text": raw_text,
        "parsed": parsed or {},
        "target_id": None,
    }
    try:
        ensure_appointment_workflow_schema()
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE pending_tasks
                SET status = 'failed',
                    completed_at = NOW(),
                    error_message = 'replaced_by_new_appointment_draft'
                WHERE task_type = 'appointment_draft'
                  AND status = 'queued'
                  AND from_phone = %s;
                """,
                (from_phone,),
            )
            cursor.execute(
                """
                INSERT INTO pending_tasks (
                    task_type, payload, status, patient_id, from_phone
                )
                VALUES ('appointment_draft', %s::jsonb, 'queued', %s, %s)
                RETURNING id::text;
                """,
                (_json_or_empty(payload), _uuid_or_none(patient_id), from_phone),
            )
            row = cursor.fetchone()
        connection.commit()
        return row[0] if row else None
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
        return None
    finally:
        if connection is not None:
            connection.close()


def get_appointments_due_for_reminder(hours: int = 72, limit: int = 50) -> list[dict]:
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id::text, patient_id::text, appointment_type, title,
                       appointment_at::text, location, provider_name,
                       department, notes, status
                FROM care_appointments
                WHERE appointment_at > NOW()
                  AND appointment_at <= NOW() + (%s || ' hours')::interval
                  AND status IN ('scheduled', 'confirmed', 'tentative')
                ORDER BY appointment_at ASC
                LIMIT %s;
                """,
                (int(hours or 72), max(1, min(int(limit or 50), 200))),
            )
            rows = cursor.fetchall()
        return [
            {
                "id": row[0],
                "patient_id": row[1],
                "appointment_type": row[2],
                "title": row[3] or row[2] or "Appointment",
                "appointment_at": row[4],
                "location": row[5] or "",
                "provider_name": row[6] or "",
                "department": row[7] or "",
                "notes": row[8] or "",
                "status": row[9] or "",
            }
            for row in rows
        ]
    except Exception as error:
        print(error)
        return []
    finally:
        if connection is not None:
            connection.close()


def appointment_reminder_sent(appointment_id: str, bucket: str) -> bool:
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM audit_log
                WHERE entity_type = 'care_appointment'
                  AND entity_id = %s
                  AND action = 'APPOINTMENT_REMINDER_SENT'
                  AND new_value ->> 'bucket' = %s
                LIMIT 1;
                """,
                (_uuid_or_none(appointment_id), str(bucket or "")),
            )
            return cursor.fetchone() is not None
    except Exception:
        return False
    finally:
        if connection is not None:
            connection.close()


def get_upcoming_test_appointments(patient_id: str, days: int = 14, limit: int = 5) -> list[dict]:
    appointments = get_upcoming_appointments(patient_id, days=days, limit=max(limit * 2, limit))
    tests = []
    for item in appointments:
        text = " ".join(
            str(item.get(key) or "").lower()
            for key in ("appointment_type", "title", "department", "notes")
        )
        if any(term in text for term in ("lab", "test", "scan", "xray", "x-ray", "mri", "ct", "blood", "diagnostic")):
            tests.append(item)
        if len(tests) >= limit:
            break
    return tests


def get_upcoming_caregiver_visits(patient_id: str, days: int = 7, limit: int = 5) -> list[dict]:
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            if not _table_exists(cursor, "caregiver_visits"):
                return []
            cursor.execute(
                """
                SELECT v.id::text,
                       COALESCE(v.caregiver_name, p.full_name) AS caregiver_name,
                       v.visit_at::text,
                       v.purpose,
                       v.brief,
                       v.status
                FROM caregiver_visits v
                LEFT JOIN profiles p ON v.caregiver_profile_id = p.id
                WHERE v.patient_id = %s
                  AND v.visit_at >= NOW()
                  AND v.visit_at < NOW() + (%s || ' days')::interval
                  AND v.status IN ('planned', 'scheduled')
                ORDER BY v.visit_at ASC
                LIMIT %s;
                """,
                (_uuid_or_none(patient_id), int(days or 7), int(limit or 5)),
            )
            rows = cursor.fetchall()
        return [
            {
                "id": row[0],
                "caregiver_name": row[1] or "Secondary caregiver",
                "visit_at": row[2],
                "purpose": row[3] or "",
                "brief": row[4] or "",
                "status": row[5] or "",
            }
            for row in rows
        ]
    except Exception as error:
        print(error)
        return []
    finally:
        if connection is not None:
            connection.close()


def get_crisis_cache(patient_id: str) -> dict | None:
    """
    Fetch the pre-computed crisis packet from crisis_cache.
    Returns cache_json only when is_fresh is true.
    """
    try:
        connection = _connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT cache_json, is_fresh
                    FROM crisis_cache
                    WHERE patient_id = %s;
                    """,
                    (patient_id,),
                )
                row = cursor.fetchone()
        finally:
            connection.close()

        if row is None:
            return None

        cache_json, is_fresh = row
        if is_fresh is not True:
            return None

        if isinstance(cache_json, dict):
            return cache_json

        if isinstance(cache_json, str):
            parsed = json.loads(cache_json)
            return parsed if isinstance(parsed, dict) else None

        return None
    except Exception:
        return None


def get_crisis_cache_if_fresh(patient_id: str) -> dict | None:
    """
    Return crisis_cache.cache_json only when fresh and updated in the last 6 hours.
    """
    try:
        connection = _connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT cache_json
                    FROM crisis_cache
                    WHERE patient_id = %s
                      AND is_fresh = TRUE
                      AND last_updated > NOW() - INTERVAL '6 hours'
                    LIMIT 1;
                    """,
                    (patient_id,),
                )
                row = cursor.fetchone()
        finally:
            connection.close()

        if row is None:
            return None

        cache_json = row[0]
        if isinstance(cache_json, dict):
            return cache_json
        if isinstance(cache_json, str):
            parsed = json.loads(cache_json)
            return parsed if isinstance(parsed, dict) else None
        return None
    except Exception as error:
        print(error)
        return None


def get_patient_location(patient_id: str) -> dict | None:
    """
    Fetch patient latitude/longitude.
    Returns {"latitude": float, "longitude": float} when both values exist.
    """
    try:
        connection = _connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT latitude, longitude
                    FROM patients
                    WHERE id = %s;
                    """,
                    (patient_id,),
                )
                row = cursor.fetchone()
        finally:
            connection.close()

        if row is None or row[0] is None or row[1] is None:
            return None

        latitude = float(row[0])
        longitude = float(row[1])
        return {"latitude": latitude, "longitude": longitude}
    except Exception:
        return None


def get_patient_name(patient_id: str) -> str | None:
    try:
        connection = _connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT p.full_name
                    FROM patients patient_record
                    JOIN profiles p ON patient_record.profile_id = p.id
                    WHERE patient_record.id = %s
                    LIMIT 1;
                    """,
                    (patient_id,),
                )
                row = cursor.fetchone()
        finally:
            connection.close()

        if row is None:
            return None
        return row[0]
    except Exception:
        return None


def get_patient_hospital_preference(patient_id: str) -> str | None:
    try:
        connection = _connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT hospital_preference
                    FROM patients
                    WHERE id = %s
                    LIMIT 1;
                    """,
                    (patient_id,),
                )
                row = cursor.fetchone()
        finally:
            connection.close()

        if row is None or not row[0]:
            return None
        return str(row[0])
    except Exception:
        return None


def get_care_team_contacts(patient_id: str) -> list[dict]:
    try:
        connection = _connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT p.full_name, p.phone, ct.role
                    FROM care_team ct
                    JOIN profiles p ON ct.profile_id = p.id
                    WHERE ct.patient_id = %s
                      AND ct.is_active = TRUE
                      AND ct.role IN ('primary_caregiver', 'secondary_caregiver', 'doctor', 'hospital')
                    ORDER BY
                        CASE ct.role
                            WHEN 'primary_caregiver' THEN 1
                            WHEN 'secondary_caregiver' THEN 2
                            WHEN 'doctor' THEN 3
                            WHEN 'hospital' THEN 4
                            ELSE 5
                        END,
                        p.full_name;
                    """,
                    (patient_id,),
                )
                rows = cursor.fetchall()
        finally:
            connection.close()

        return [
            {
                "name": row[0] or "",
                "phone": row[1] or "",
                "role": row[2] or "",
            }
            for row in rows
        ]
    except Exception:
        return []


def get_caregivers(patient_id: str) -> list[dict]:
    """
    Fetch active caregiver/doctor contacts for the patient.
    Returns [{"name": str, "role": str, "phone": str}, ...].
    """
    try:
        connection = _connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT p.full_name, ct.role, p.phone
                    FROM care_team ct
                    JOIN profiles p ON ct.profile_id = p.id
                    WHERE ct.patient_id = %s
                      AND ct.is_active = TRUE
                      AND ct.role IN ('primary_caregiver', 'secondary_caregiver', 'doctor')
                      AND p.phone IS NOT NULL
                    ORDER BY CASE ct.role
                      WHEN 'primary_caregiver' THEN 1
                      WHEN 'secondary_caregiver' THEN 2
                      WHEN 'doctor' THEN 3
                    END;
                    """,
                    (patient_id,),
                )
                rows = cursor.fetchall()
        finally:
            connection.close()

        return [
            {
                "name": row[0],
                "role": row[1],
                "phone": row[2],
            }
            for row in rows
        ]
    except Exception as error:
        print(error)
        return []


def upsert_crisis_cache(patient_id: str, cache_json: dict) -> None:
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO crisis_cache (patient_id, cache_json, last_updated, is_fresh)
                VALUES (%s, %s, NOW(), TRUE)
                ON CONFLICT (patient_id)
                DO UPDATE SET
                    cache_json = EXCLUDED.cache_json,
                    last_updated = NOW(),
                    is_fresh = TRUE;
                """,
                (patient_id, _json_or_empty(cache_json)),
            )
        connection.commit()
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
    finally:
        if connection is not None:
            connection.close()


def _table_columns(connection, table_name: str) -> set[str]:
    """
    Fetch public table columns for schema-tolerant helpers.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = %s;
                """,
                (table_name,),
            )
            return {str(row[0]) for row in cursor.fetchall()}
    except Exception as error:
        print(error)
        return set()

def get_pharma_alerts(patient_id: str, limit: int = 12) -> list[dict]:
    """
    Fetch recent open PharmaAgent/safety alerts for dashboard display.
    """
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id::text, type, severity, message_template,
                       COALESCE(data_payload, '{}'::jsonb), status, created_at::text
                FROM alerts
                WHERE patient_id = %s
                  AND status = 'open'
                  AND type IN (
                    'crisis_emergency',
                    'caregiver_alert',
                    'drug_interaction',
                    'suspicious_dose',
                    'suspicious_lab_value',
                    'lab_baseline_jump',
                    'symptom_severe',
                    'extraction_review'
                  )
                ORDER BY
                  CASE severity
                    WHEN 'critical' THEN 1
                    WHEN 'high' THEN 2
                    WHEN 'medium' THEN 3
                    ELSE 4
                  END,
                  created_at DESC
                LIMIT %s;
                """,
                (_uuid_or_none(patient_id), int(limit or 12)),
            )
            rows = cursor.fetchall()
        return [
            {
                "id": row[0],
                "type": row[1],
                "severity": row[2],
                "message": row[3],
                "data_payload": row[4] or {},
                "status": row[5],
                "created_at": row[6],
            }
            for row in rows
        ]
    except Exception as error:
        print(error)
        return []
    finally:
        if connection is not None:
            connection.close()


def get_agent_approvals_for_patient(patient_id: str, limit: int = 12) -> list[dict]:
    """
    Fetch recent PharmaAgent approval/veto rows linked through gates_result.patient_id.
    """
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id::text, rule_hash, drug_a, drug_b, severity, status,
                       auto_approve_eligible, veto_expiry::text, confidence,
                       COALESCE(gates_result, '{}'::jsonb), created_at::text
                FROM agent_approvals
                WHERE gates_result ->> 'patient_id' = %s
                ORDER BY created_at DESC
                LIMIT %s;
                """,
                (str(patient_id), int(limit or 12)),
            )
            rows = cursor.fetchall()
        return [
            {
                "id": row[0],
                "rule_hash": row[1],
                "drug_a": row[2],
                "drug_b": row[3],
                "severity": row[4],
                "status": row[5],
                "auto_approve_eligible": row[6],
                "veto_expiry": row[7],
                "confidence": row[8],
                "gates_result": row[9] or {},
                "created_at": row[10],
            }
            for row in rows
        ]
    except Exception as error:
        print(error)
        return []
    finally:
        if connection is not None:
            connection.close()


def get_pending_agent_approval_count(patient_id: str) -> int:
    """
    Count all pending PharmaAgent approvals for the patient.
    Used by caregiver briefs so display limits do not become false totals.
    """
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*)::int
                FROM agent_approvals
                WHERE gates_result ->> 'patient_id' = %s
                  AND LOWER(COALESCE(status, '')) = 'pending';
                """,
                (str(patient_id),),
            )
            row = cursor.fetchone()
            return int(row[0] or 0) if row else 0
    except Exception as error:
        print(error)
        return 0
    finally:
        if connection is not None:
            connection.close()


def get_medications_awaiting_review(patient_id: str, limit: int = 8) -> list[dict]:
    """
    Return non-active medication candidates that should be visible in caregiver
    briefs, without including them in active adherence calculations.
    """
    connection = None
    try:
        connection = _connect()
        safe_limit = max(1, min(int(limit or 8), 50))
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id::text,
                       COALESCE(drug_name, raw_drug_name, 'Unknown medication') AS drug_name,
                       dose_amount::text,
                       dose_unit,
                       frequency,
                       status,
                       activation_decision,
                       recorded_at::text
                FROM medications
                WHERE patient_id = %s
                  AND COALESCE(status, '') IN (
                      'suspicious',
                      'validation_pending',
                      'interaction_pending',
                      'veto_required',
                      'approved'
                  )
                ORDER BY COALESCE(recorded_at, updated_at, validated_at, activated_at) DESC NULLS LAST
                LIMIT %s;
                """,
                (_uuid_or_none(patient_id), safe_limit),
            )
            rows = cursor.fetchall()
        return [
            {
                "id": row[0],
                "drug_name": row[1],
                "dose_amount": row[2] or "",
                "dose_unit": row[3] or "",
                "frequency": row[4] or "",
                "status": row[5] or "",
                "reason": row[6] or "",
                "recorded_at": row[7],
            }
            for row in rows
        ]
    except Exception as error:
        print(error)
        return []
    finally:
        if connection is not None:
            connection.close()


def get_medications_awaiting_review_count(patient_id: str) -> int:
    """
    Count all held medication candidates for caregiver brief totals.
    """
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*)::int
                FROM medications
                WHERE patient_id = %s
                  AND COALESCE(status, '') IN (
                      'suspicious',
                      'validation_pending',
                      'interaction_pending',
                      'veto_required',
                      'approved'
                  );
                """,
                (_uuid_or_none(patient_id),),
            )
            row = cursor.fetchone()
            return int(row[0] or 0) if row else 0
    except Exception as error:
        print(error)
        return 0
    finally:
        if connection is not None:
            connection.close()


def _approval_code_hash(code: str) -> str:
    return hashlib.sha256(str(code or "").strip().lower().encode("utf-8")).hexdigest()


def update_agent_approval_status(rule_hash: str, status: str) -> bool:
    """
    Set an approval row to vetoed/human_approved/auto_approved/finalized.
    """
    connection = None
    try:
        aliases = {
            "approved": "human_approved",
            "approve": "human_approved",
            "human_approved": "human_approved",
            "auto_approved": "auto_approved",
            "finalized": "finalized",
            "finalize": "finalized",
            "veto": "vetoed",
            "vetoed": "vetoed",
            "rejected": "rejected",
            "pending": "pending",
        }
        normalized = str(status or "").strip().lower()
        normalized = aliases.get(normalized, normalized)
        allowed = {"vetoed", "human_approved", "auto_approved", "finalized", "rejected", "pending"}
        if not rule_hash or normalized not in allowed:
            return False
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE agent_approvals
                SET status = %s,
                    vetoed_at = CASE WHEN %s = 'vetoed' THEN NOW() ELSE vetoed_at END,
                    finalized_at = CASE WHEN %s IN ('human_approved', 'auto_approved', 'finalized') THEN NOW() ELSE finalized_at END
                WHERE rule_hash = %s
                RETURNING id;
                """,
                (normalized, normalized, normalized, rule_hash),
            )
            row = cursor.fetchone()
        connection.commit()
        return row is not None
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
        return False
    finally:
        if connection is not None:
            connection.close()


def get_agent_approval_by_rule_hash_or_prefix(rule_hash_or_prefix: str) -> dict | None:
    """
    Fetch an approval by full rule hash or caregiver-friendly prefix.
    Returns None on no match or ambiguous/invalid input.
    """
    token = str(rule_hash_or_prefix or "").strip().lower()
    if len(token) < 6:
        return None
    connection = None
    try:
        ensure_pharma_rule_registry_schema()
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT rule_hash, drug_a, drug_b, severity, status,
                       auto_approve_eligible, veto_expiry::text, confidence,
                       COALESCE(gates_result, '{}'::jsonb), created_at::text,
                       approval_code_expires_at::text, approval_code_used_at::text
                FROM agent_approvals
                WHERE LOWER(rule_hash) = %s OR LOWER(rule_hash) LIKE %s
                ORDER BY CASE WHEN LOWER(rule_hash) = %s THEN 0 ELSE 1 END,
                         created_at DESC
                LIMIT 2;
                """,
                (token, f"{token}%", token),
            )
            rows = cursor.fetchall()
        if not rows:
            return None
        exact_rows = [row for row in rows if str(row[0]).lower() == token]
        row = exact_rows[0] if exact_rows else rows[0]
        return {
            "rule_hash": row[0],
            "drug_a": row[1],
            "drug_b": row[2],
            "severity": row[3],
            "status": row[4],
            "auto_approve_eligible": row[5],
            "veto_expiry": row[6],
            "confidence": row[7],
            "gates_result": row[8] or {},
            "created_at": row[9],
            "approval_code_expires_at": row[10],
            "approval_code_used_at": row[11],
            "token_match": "rule_hash",
        }
    except Exception as error:
        print(error)
        return None
    finally:
        if connection is not None:
            connection.close()


def set_agent_approval_challenge(rule_hash: str, code: str, expiry_hours: int = 48) -> bool:
    """
    Store a hashed caregiver challenge code for a pending approval.
    """
    connection = None
    try:
        if not rule_hash or not code:
            return False
        ensure_pharma_rule_registry_schema()
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE agent_approvals
                SET approval_code_hash = %s,
                    approval_code_expires_at = NOW() + (%s || ' hours')::interval,
                    approval_code_used_at = NULL,
                    approval_code_attempts = 0
                WHERE rule_hash = %s
                  AND status = 'pending'
                RETURNING id;
                """,
                (_approval_code_hash(code), int(expiry_hours or 48), rule_hash),
            )
            row = cursor.fetchone()
        connection.commit()
        return row is not None
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
        return False
    finally:
        if connection is not None:
            connection.close()


def get_agent_approval_by_code_or_prefix(token_value: str) -> dict | None:
    """
    Resolve a caregiver token. Prefer secure one-time challenge codes, then
    fall back to the old rule-hash prefix for backwards compatibility.
    """
    token = str(token_value or "").strip().lower()
    if len(token) < 6:
        return None
    connection = None
    try:
        ensure_pharma_rule_registry_schema()
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT rule_hash, drug_a, drug_b, severity, status,
                       auto_approve_eligible, veto_expiry::text, confidence,
                       COALESCE(gates_result, '{}'::jsonb), created_at::text,
                       approval_code_expires_at::text, approval_code_used_at::text,
                       CASE
                         WHEN approval_code_used_at IS NOT NULL THEN 'used'
                         WHEN approval_code_expires_at IS NOT NULL AND approval_code_expires_at < NOW() THEN 'expired'
                         ELSE 'valid'
                       END AS code_status
                FROM agent_approvals
                WHERE approval_code_hash = %s
                ORDER BY created_at DESC
                LIMIT 1;
                """,
                (_approval_code_hash(token),),
            )
            row = cursor.fetchone()
            if row:
                return {
                    "rule_hash": row[0],
                    "drug_a": row[1],
                    "drug_b": row[2],
                    "severity": row[3],
                    "status": row[4],
                    "auto_approve_eligible": row[5],
                    "veto_expiry": row[6],
                    "confidence": row[7],
                    "gates_result": row[8] or {},
                    "created_at": row[9],
                    "approval_code_expires_at": row[10],
                    "approval_code_used_at": row[11],
                    "code_status": row[12],
                    "token_match": "approval_code",
                }
        return get_agent_approval_by_rule_hash_or_prefix(token)
    except Exception as error:
        print(error)
        return get_agent_approval_by_rule_hash_or_prefix(token)
    finally:
        if connection is not None:
            connection.close()


def mark_agent_approval_code_used(rule_hash: str) -> bool:
    connection = None
    try:
        ensure_pharma_rule_registry_schema()
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE agent_approvals
                SET approval_code_used_at = COALESCE(approval_code_used_at, NOW())
                WHERE rule_hash = %s
                  AND approval_code_hash IS NOT NULL
                RETURNING id;
                """,
                (rule_hash,),
            )
            row = cursor.fetchone()
        connection.commit()
        return row is not None
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
        return False
    finally:
        if connection is not None:
            connection.close()


def mark_agent_approval_veto_notified(rule_hash: str, delivered: bool) -> bool:
    """
    Record that the caregiver veto/approval notice was attempted.
    """
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE agent_approvals
                SET veto_notified_at = COALESCE(veto_notified_at, NOW()),
                    veto_delivery_confirmed = COALESCE(veto_delivery_confirmed, FALSE) OR %s,
                    veto_delivery_attempts = COALESCE(veto_delivery_attempts, 0) + 1
                WHERE rule_hash = %s
                RETURNING id;
                """,
                (bool(delivered), rule_hash),
            )
            row = cursor.fetchone()
        connection.commit()
        return row is not None
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
        return False
    finally:
        if connection is not None:
            connection.close()


def get_pharma_audit_summary(patient_id: str, hours: int = 24) -> dict:
    """
    Summarize recent PharmaAgent, LLM fallback, ingestion, and notification audit rows.
    """
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT action, COUNT(*)::int, MAX(timestamp)::text
                FROM audit_log
                WHERE patient_id = %s
                  AND timestamp > NOW() - (%s || ' hours')::interval
                  AND (
                    entity_type IN (
                      'pharma_agent',
                      'crisis_notification',
                      'pending_tasks',
                      'caregiver_observation'
                    )
                    OR action IN (
                      'PHARMA_AGENT_DECISION',
                      'PHARMA_AGENT_ERROR',
                      'MEDIA_TASK_COMPLETED',
                      'OBSERVATION_LOGGED'
                    )
                  )
                GROUP BY action
                ORDER BY MAX(timestamp) DESC;
                """,
                (_uuid_or_none(patient_id), int(hours or 24)),
            )
            rows = cursor.fetchall()
        return {
            "window_hours": int(hours or 24),
            "items": [
                {"action": row[0], "count": row[1], "latest_at": row[2]}
                for row in rows
            ],
        }
    except Exception as error:
        print(error)
        return {"window_hours": int(hours or 24), "items": []}
    finally:
        if connection is not None:
            connection.close()


def get_recent_pharma_research_reports(patient_id: str, limit: int = 10) -> list[dict]:
    """
    Return recent research/tool evidence reports for the dashboard.
    """
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT research_key, drug_a, drug_b, status, severity, confidence,
                       evidence_count, COALESCE(tool_results, '{}'::jsonb),
                       COALESCE(synthesis, '{}'::jsonb),
                       COALESCE(gates_result, '{}'::jsonb),
                       updated_at::text, error_message
                FROM pharma_research_reports
                WHERE patient_id = %s
                ORDER BY updated_at DESC
                LIMIT %s;
                """,
                (_uuid_or_none(patient_id), int(limit or 10)),
            )
            rows = cursor.fetchall()
        return [
            {
                "research_key": row[0],
                "drug_a": row[1],
                "drug_b": row[2],
                "status": row[3],
                "severity": row[4],
                "confidence": row[5],
                "evidence_count": row[6],
                "tool_results": row[7] or {},
                "synthesis": row[8] or {},
                "gates_result": row[9] or {},
                "updated_at": row[10],
                "error_message": row[11],
            }
            for row in rows
        ]
    except Exception as error:
        print(error)
        return []
    finally:
        if connection is not None:
            connection.close()


def get_latest_medication_log(patient_id: str) -> dict | None:
    """
    Fetch most recent medication_log entry.
    Returns {"drug_name": str, "event_type": str, "reported_at": str}.
    """
    try:
        connection = _connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT m.drug_name, ml.event_type, ml.reported_at::text
                    FROM medication_log ml
                    JOIN medications m ON ml.medication_id = m.id
                    WHERE ml.patient_id = %s
                    ORDER BY ml.reported_at DESC
                    LIMIT 1;
                    """,
                    (patient_id,),
                )
                row = cursor.fetchone()
        finally:
            connection.close()

        if row is None:
            return None

        return {
            "drug_name": row[0],
            "event_type": row[1],
            "reported_at": row[2],
        }
    except Exception:
        return None


def get_pending_task_for_phone(phone: str) -> dict | None:
    try:
        connection = _connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id::text, task_type, payload, created_at
                    FROM pending_tasks
                    WHERE from_phone = %s
                      AND task_type IN (
                          'medication_confirmation',
                          'veto_window',
                          'approval_window',
                          'pending_approval',
                          'interaction_alert',
                          'new_med',
                          'appointment_confirm',
                          'appointment_draft'
                      )
                      AND status = 'queued'
                    ORDER BY created_at DESC
                    LIMIT 1;
                    """,
                    (phone,),
                )
                row = cursor.fetchone()
        finally:
            connection.close()

        if row is None:
            return None

        payload = row[2] or {}
        created_at = row[3]
        task_type = row[1]
        expires_at = _pending_task_expires_at(payload, created_at, task_type)
        if expires_at is not None and datetime.now(expires_at.tzinfo) > expires_at:
            _mark_pending_task_failed(row[0], "expired")
            return None

        return {
            "id": row[0],
            "type": task_type,
            "target_id": _pending_task_target_id(payload),
            "payload": payload,
            "asked_at": created_at.isoformat(),
            "created_at": created_at.isoformat(),
            "expires_at": expires_at.isoformat() if expires_at else None,
            "active": True,
        }
    except Exception:
        return None


def _parse_db_datetime(value):
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _pending_task_target_id(payload: dict):
    if not isinstance(payload, dict):
        return None
    return payload.get("medication_id") or payload.get("target_id") or payload.get("entity_id")


def _pending_task_expires_at(payload: dict, created_at: datetime, task_type: str = "medication_confirmation"):
    try:
        if isinstance(payload, dict):
            explicit = _parse_db_datetime(payload.get("expires_at"))
            if explicit is not None:
                return explicit
        approval_types = {
            "veto_window",
            "approval_window",
            "pending_approval",
            "interaction_alert",
            "new_med",
        }
        if task_type in approval_types:
            return created_at + timedelta(seconds=config.APPROVAL_CONTEXT_EXPIRY_SECONDS)
        if task_type == "appointment_confirm":
            return created_at + timedelta(hours=getattr(config, "APPOINTMENT_CONFIRM_EXPIRY_HOURS", 72))
        if task_type == "appointment_draft":
            return created_at + timedelta(hours=2)
        return created_at + timedelta(minutes=config.MEDICATION_CONTEXT_EXPIRY_MINUTES)
    except Exception:
        return None


def _mark_pending_task_failed(task_id: str, reason: str) -> None:
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE pending_tasks
                SET status = 'failed',
                    completed_at = NOW(),
                    error_message = %s
                WHERE id = %s
                  AND status = 'queued';
                """,
                (reason, task_id),
            )
        connection.commit()
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
    finally:
        if connection is not None:
            connection.close()


def get_recent_messages_for_phone(phone: str, limit: int = 2) -> list[str]:
    try:
        connection = _connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT body
                    FROM incoming_messages
                    WHERE from_phone = %s
                      AND body IS NOT NULL
                      AND body != ''
                    ORDER BY received_at DESC
                    LIMIT %s;
                    """,
                    (phone, limit),
                )
                rows = cursor.fetchall()
        finally:
            connection.close()

        messages = [row[0] for row in rows]
        messages.reverse()
        return messages
    except Exception:
        return []


def get_recent_media_task_for_phone(phone: str, minutes: int = 30) -> dict | None:
    """
    Fetch the latest recent media-processing pending task for this phone.
    """
    try:
        connection = _connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id::text, task_type, status, result_summary, error_message, created_at::text
                    FROM pending_tasks
                    WHERE from_phone = %s
                      AND task_type IN ('ocr_prescription', 'parse_pdf', 'transcribe_audio')
                      AND created_at > NOW() - (%s::text || ' minutes')::interval
                    ORDER BY created_at DESC
                    LIMIT 1;
                    """,
                    (phone, int(minutes)),
                )
                row = cursor.fetchone()
        finally:
            connection.close()

        if row is None:
            return None
        return {
            "id": row[0],
            "task_type": row[1],
            "status": row[2],
            "result_summary": row[3],
            "error_message": row[4],
            "created_at": row[5],
        }
    except Exception as error:
        print(error)
        return None


def log_incoming_message(
    from_phone: str,
    body: str,
    num_media: int,
    media_url: str | None,
    media_type: str | None,
    profile_id: str | None = None,
) -> None:
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO incoming_messages (
                    profile_id,
                    from_phone,
                    body
                )
                VALUES (%s, %s, %s);
                """,
                (profile_id, from_phone, body),
            )
        connection.commit()
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
    finally:
        if connection is not None:
            connection.close()


def log_medication_event(
    patient_id: str,
    medication_id: str,
    profile_id: str,
    event_type: str,
    raw_text: str,
    source_type: str = "web",
) -> str:
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO medication_log (
                    patient_id,
                    medication_id,
                    profile_id,
                    event_type,
                    raw_text,
                    source_type,
                    reported_at,
                    event_date
                )
                VALUES (%s, %s, %s, %s, %s, %s, NOW(), CURRENT_DATE)
                RETURNING id;
                """,
                (patient_id, medication_id, profile_id, event_type, raw_text, source_type),
            )
            row = cursor.fetchone()
        connection.commit()

        if row is None:
            raise Exception("medication_log insert returned no id")
        return str(row[0])
    except Exception as error:
        if connection is not None:
            connection.rollback()
        raise Exception(f"Failed to log medication event: {error}") from error
    finally:
        if connection is not None:
            connection.close()


def _ensure_caregiver_observations_table(connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS caregiver_observations (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                patient_id uuid REFERENCES patients(id),
                profile_id uuid REFERENCES profiles(id),
                raw_text text NOT NULL,
                observation_type text NOT NULL DEFAULT 'general',
                source_type text NOT NULL DEFAULT 'web',
                extracted_flags jsonb NOT NULL DEFAULT '{}'::jsonb,
                conflicts jsonb NOT NULL DEFAULT '[]'::jsonb,
                observed_at timestamptz NOT NULL DEFAULT NOW(),
                created_at timestamptz NOT NULL DEFAULT NOW()
            );
            """
        )
        cursor.execute("ALTER TABLE caregiver_observations ADD COLUMN IF NOT EXISTS observation_type text NOT NULL DEFAULT 'general';")
        cursor.execute("ALTER TABLE caregiver_observations ADD COLUMN IF NOT EXISTS observed_at timestamptz NOT NULL DEFAULT NOW();")
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_caregiver_observations_patient_created
            ON caregiver_observations(patient_id, created_at DESC);
            """
        )


def log_caregiver_observation(
    patient_id: str,
    profile_id: str | None,
    raw_text: str,
    extracted_flags: dict | None = None,
    conflicts: list[dict] | None = None,
    observation_type: str = "general",
    source_type: str = "web",
) -> dict:
    """
    Store a third-person caregiver observation. Falls back to audit_log if the
    observation table cannot be created or written.
    """
    connection = None
    payload = {
        "raw_text": raw_text,
        "observation_type": observation_type,
        "source_type": source_type,
        "extracted_flags": extracted_flags or {},
        "conflicts": conflicts or [],
    }
    try:
        connection = _connect()
        _ensure_caregiver_observations_table(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO caregiver_observations (
                    patient_id,
                    profile_id,
                    raw_text,
                    observation_type,
                    source_type,
                    extracted_flags,
                    conflicts,
                    observed_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                RETURNING id::text;
                """,
                (
                    _uuid_or_none(patient_id),
                    _uuid_or_none(profile_id),
                    raw_text,
                    observation_type,
                    source_type,
                    _json_or_empty(extracted_flags or {}),
                    _json_or_empty(conflicts or []),
                ),
            )
            row = cursor.fetchone()
        connection.commit()
        return {"id": row[0] if row else None, "storage": "caregiver_observations"}
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
        write_audit(
            patient_id,
            profile_id,
            "caregiver_observation",
            None,
            "CAREGIVER_OBSERVATION_RECORDED",
            "system",
            payload,
        )
        return {"id": None, "storage": "audit_log"}
    finally:
        if connection is not None:
            connection.close()


def get_recent_patient_self_reports(patient_id: str, limit: int = 5) -> list[dict]:
    """
    Fetch recent patient-authored text messages for conflict checks.
    """
    try:
        connection = _connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT im.body, im.received_at::text
                    FROM incoming_messages im
                    JOIN care_team ct ON im.profile_id = ct.profile_id
                    WHERE ct.patient_id = %s
                      AND ct.role = 'patient'
                      AND im.body IS NOT NULL
                      AND im.body != ''
                    ORDER BY im.received_at DESC
                    LIMIT %s;
                    """,
                    (_uuid_or_none(patient_id), limit),
                )
                rows = cursor.fetchall()
        finally:
            connection.close()
        return [{"body": row[0], "received_at": row[1]} for row in rows]
    except Exception as error:
        print(error)
        return []


def enqueue_pharmagent_side_effect_lookup(
    patient_id: str,
    profile_id: str | None,
    drug_name: str | None,
    symptom: str | None,
    raw_text: str,
    from_phone: str | None = None,
) -> str | None:
    """
    Queue an async PharmAgent research task for medication side-effect review.
    Never raises; falls back to audit_log if pending_tasks insert fails.
    """
    connection = None
    payload = {
        "request_type": "medication_side_effect_lookup",
        "drug_name": drug_name,
        "symptom": symptom,
        "raw_text": raw_text,
        "profile_id": _uuid_or_none(profile_id),
    }
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO pending_tasks (
                    task_type,
                    payload,
                    status,
                    patient_id,
                    from_phone
                )
                VALUES ('pharm_research', %s, 'queued', %s, %s)
                RETURNING id::text;
                """,
                (
                    _json_or_empty(payload),
                    _uuid_or_none(patient_id),
                    from_phone,
                ),
            )
            row = cursor.fetchone()
        connection.commit()
        med_id = row[0] if row else None
        if med_id and getattr(config, "PHARMA_AGENT_ENABLED", True):
            enqueue_pharma_medication_check(
                patient_id=patient_id,
                medication_id=med_id,
                drug_name=drug_name,
                dose_amount=dose_amount,
                prescribed_by=prescribed_by or brand_name,
                source_type=source_type,
            )
        return med_id
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
        write_audit(
            patient_id,
            profile_id,
            "pending_tasks",
            None,
            "PHARMAGENT_SIDE_EFFECT_LOOKUP_ENQUEUE_FAILED",
            "system",
            payload,
        )
        return None
    finally:
        if connection is not None:
            connection.close()


def enqueue_pharma_medication_check(
    patient_id: str,
    medication_id: str,
    drug_name: str,
    dose_amount=None,
    prescribed_by: str | None = None,
    source_type: str | None = None,
    from_phone: str | None = None,
) -> str | None:
    """
    Queue a durable PharmaAgent check for a newly inserted medication.
    Idempotent by medication_id within the pending_tasks table.
    """
    connection = None
    payload = {
        "request_type": "new_medication_safety_check",
        "medication_id": str(medication_id or ""),
        "drug_name": drug_name,
        "dose_amount": str(dose_amount) if dose_amount is not None else None,
        "prescribed_by": prescribed_by,
        "source_type": source_type,
    }
    try:
        if not patient_id or not medication_id or not drug_name:
            return None

        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id::text
                FROM pending_tasks
                WHERE task_type = 'pharm_research'
                  AND payload ->> 'request_type' = 'new_medication_safety_check'
                  AND payload ->> 'medication_id' = %s
                  AND status IN ('queued', 'in_progress', 'done')
                ORDER BY created_at DESC
                LIMIT 1;
                """,
                (str(medication_id),),
            )
            existing = cursor.fetchone()
            if existing:
                return existing[0]

            cursor.execute(
                """
                INSERT INTO pending_tasks (
                    task_type,
                    payload,
                    status,
                    patient_id,
                    from_phone
                )
                VALUES ('pharm_research', %s, 'queued', %s, %s)
                RETURNING id::text;
                """,
                (
                    _json_or_empty(payload),
                    _uuid_or_none(patient_id),
                    from_phone,
                ),
            )
            row = cursor.fetchone()
        connection.commit()
        return row[0] if row else None
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
        write_audit(
            patient_id,
            None,
            "pending_tasks",
            None,
            "PHARMA_AGENT_MEDICATION_CHECK_ENQUEUE_FAILED",
            "system",
            payload,
        )
        return None
    finally:
        if connection is not None:
            connection.close()


def ensure_pharma_medication_insert_trigger() -> bool:
    """
    Install a DB-level safety net so direct medication inserts also queue
    PharmaAgent checks. Safe to call on every app startup.
    """
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE OR REPLACE FUNCTION public.enqueue_pharma_medication_check_trigger()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    IF NEW.patient_id IS NULL OR NEW.drug_name IS NULL OR NEW.drug_name = '' THEN
                        RETURN NEW;
                    END IF;

                    IF COALESCE(NEW.status, '') <> 'active' THEN
                        RETURN NEW;
                    END IF;

                    INSERT INTO public.pending_tasks (
                        task_type,
                        payload,
                        status,
                        patient_id,
                        from_phone
                    )
                    SELECT
                        'pharm_research',
                        jsonb_build_object(
                            'request_type', 'new_medication_safety_check',
                            'medication_id', NEW.id::text,
                            'drug_name', NEW.drug_name,
                            'dose_amount', CASE WHEN NEW.dose_amount IS NULL THEN NULL ELSE NEW.dose_amount::text END,
                            'prescribed_by', NEW.prescribed_by,
                            'source_type', NEW.source_type,
                            'queued_by', 'db_trigger'
                        ),
                        'queued',
                        NEW.patient_id,
                        NULL
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM public.pending_tasks
                        WHERE task_type = 'pharm_research'
                          AND payload ->> 'request_type' = 'new_medication_safety_check'
                          AND payload ->> 'medication_id' = NEW.id::text
                    );

                    RETURN NEW;
                END;
                $$;
                """
            )
            cursor.execute(
                """
                DROP TRIGGER IF EXISTS trg_enqueue_pharma_medication_check
                ON public.medications;
                """
            )
            cursor.execute(
                """
                CREATE TRIGGER trg_enqueue_pharma_medication_check
                AFTER INSERT ON public.medications
                FOR EACH ROW
                EXECUTE FUNCTION public.enqueue_pharma_medication_check_trigger();
                """
            )
        connection.commit()
        return True
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(f"ensure_pharma_medication_insert_trigger failed: {error}")
        return False
    finally:
        if connection is not None:
            connection.close()


def ensure_pharma_research_tables() -> bool:
    """
    Create durable shadow-report storage for PharmaAgent research.
    """
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS public.pharma_research_reports (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    research_key TEXT NOT NULL UNIQUE,
                    patient_id UUID,
                    medication_id UUID,
                    drug_a TEXT NOT NULL,
                    drug_b TEXT NOT NULL,
                    resolver_result JSONB DEFAULT '{}'::jsonb,
                    planner_result JSONB DEFAULT '{}'::jsonb,
                    tool_results JSONB DEFAULT '{}'::jsonb,
                    synthesis JSONB DEFAULT '{}'::jsonb,
                    critic JSONB DEFAULT '{}'::jsonb,
                    gates_result JSONB DEFAULT '{}'::jsonb,
                    status TEXT NOT NULL DEFAULT 'shadow',
                    severity TEXT,
                    confidence DOUBLE PRECISION,
                    evidence_count INTEGER DEFAULT 0,
                    error_message TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pharma_research_reports_patient
                ON public.pharma_research_reports (patient_id, created_at DESC);
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pharma_research_reports_pair
                ON public.pharma_research_reports (LOWER(drug_a), LOWER(drug_b), created_at DESC);
                """
            )
        connection.commit()
        return True
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(f"ensure_pharma_research_tables failed: {error}")
        return False
    finally:
        if connection is not None:
            connection.close()


def enqueue_pharma_interaction_research(
    patient_id: str,
    medication_id: str | None,
    drug_a: str,
    drug_b: str,
    trigger: str,
    from_phone: str | None = None,
) -> str | None:
    """
    Queue background evidence research for a drug pair.
    """
    connection = None
    try:
        if not patient_id or not drug_a or not drug_b or str(drug_a).lower() == str(drug_b).lower():
            return None
        import hashlib

        pair = sorted([str(drug_a).strip().lower(), str(drug_b).strip().lower()])
        research_key = hashlib.sha256(f"{patient_id}:{pair[0]}:{pair[1]}".encode("utf-8")).hexdigest()
        payload = {
            "request_type": "interaction_research",
            "research_key": research_key,
            "medication_id": str(medication_id or ""),
            "drug_a": drug_a,
            "drug_b": drug_b,
            "trigger": trigger,
        }

        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id::text
                FROM pending_tasks
                WHERE task_type = 'pharm_research'
                  AND payload ->> 'request_type' = 'interaction_research'
                  AND payload ->> 'research_key' = %s
                  AND status IN ('queued', 'in_progress')
                LIMIT 1;
                """,
                (research_key,),
            )
            existing = cursor.fetchone()
            if existing:
                return existing[0]

            cursor.execute(
                """
                INSERT INTO pending_tasks (
                    task_type,
                    payload,
                    status,
                    patient_id,
                    from_phone
                )
                VALUES ('pharm_research', %s, 'queued', %s, %s)
                RETURNING id::text;
                """,
                (
                    _json_or_empty(payload),
                    _uuid_or_none(patient_id),
                    from_phone,
                ),
            )
            row = cursor.fetchone()
        connection.commit()
        return row[0] if row else None
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
        return None
    finally:
        if connection is not None:
            connection.close()


def get_medication_research_candidates(
    patient_id: str,
    exclude_medication_id: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """
    Return medication rows eligible for evidence-only Pharma research.
    Includes held candidates so suspicious prescriptions can still be checked
    against each other without making them active.
    """
    connection = None
    try:
        if not patient_id:
            return []
        safe_limit = max(1, min(int(limit or 50), 200))
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id::text,
                       patient_id::text,
                       drug_name,
                       raw_drug_name,
                       dose_amount,
                       dose_unit,
                       frequency,
                       status,
                       prescribed_by,
                       media_upload_id::text,
                       recorded_at
                FROM public.medications
                WHERE patient_id = %s
                  AND drug_name IS NOT NULL
                  AND BTRIM(drug_name) <> ''
                  AND COALESCE(status, 'active') IN (
                      'active',
                      'suspicious',
                      'validation_pending',
                      'interaction_pending',
                      'veto_required',
                      'approved'
                  )
                  AND (%s IS NULL OR id <> %s)
                ORDER BY COALESCE(recorded_at, updated_at, activated_at, validated_at) DESC NULLS LAST
                LIMIT %s;
                """,
                (
                    _uuid_or_none(patient_id),
                    _uuid_or_none(exclude_medication_id),
                    _uuid_or_none(exclude_medication_id),
                    safe_limit,
                ),
            )
            rows = cursor.fetchall()
        return [
            {
                "id": row[0],
                "patient_id": row[1],
                "drug_name": row[2],
                "raw_drug_name": row[3],
                "dose_amount": row[4],
                "dose_unit": row[5],
                "frequency": row[6],
                "status": row[7],
                "prescribed_by": row[8],
                "media_upload_id": row[9],
                "recorded_at": row[10].isoformat() if hasattr(row[10], "isoformat") else row[10],
            }
            for row in rows
        ]
    except Exception as error:
        print(f"get_medication_research_candidates failed: {error}")
        return []
    finally:
        if connection is not None:
            connection.close()


def upsert_pharma_research_report(report: dict) -> str | None:
    """
    Persist a PharmaAgent research report. Never raises.
    """
    connection = None
    try:
        ensure_pharma_research_tables()
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO public.pharma_research_reports (
                    research_key, patient_id, medication_id, drug_a, drug_b,
                    resolver_result, planner_result, tool_results, synthesis,
                    critic, gates_result, status, severity, confidence,
                    evidence_count, error_message, updated_at
                )
                VALUES (
                    %s, %s, %s, %s, %s,
                    %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
                    %s::jsonb, %s::jsonb, %s, %s, %s,
                    %s, %s, NOW()
                )
                ON CONFLICT (research_key) DO UPDATE SET
                    resolver_result = EXCLUDED.resolver_result,
                    planner_result = EXCLUDED.planner_result,
                    tool_results = EXCLUDED.tool_results,
                    synthesis = EXCLUDED.synthesis,
                    critic = EXCLUDED.critic,
                    gates_result = EXCLUDED.gates_result,
                    status = EXCLUDED.status,
                    severity = EXCLUDED.severity,
                    confidence = EXCLUDED.confidence,
                    evidence_count = EXCLUDED.evidence_count,
                    error_message = EXCLUDED.error_message,
                    updated_at = NOW()
                RETURNING id::text;
                """,
                (
                    report.get("research_key"),
                    _uuid_or_none(report.get("patient_id")),
                    _uuid_or_none(report.get("medication_id")),
                    report.get("drug_a"),
                    report.get("drug_b"),
                    _json_or_empty(report.get("resolver_result")),
                    _json_or_empty(report.get("planner_result")),
                    _json_or_empty(report.get("tool_results")),
                    _json_or_empty(report.get("synthesis")),
                    _json_or_empty(report.get("critic")),
                    _json_or_empty(report.get("gates_result")),
                    report.get("status") or "shadow",
                    report.get("severity"),
                    report.get("confidence"),
                    int(report.get("evidence_count") or 0),
                    report.get("error_message"),
                ),
            )
            row = cursor.fetchone()
        connection.commit()
        return row[0] if row else None
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
        return None
    finally:
        if connection is not None:
            connection.close()


def get_pharma_research_summary(hours: int = 24) -> dict:
    """
    Summarize recent PharmaAgent research reports and queued tasks.
    """
    connection = None
    try:
        ensure_pharma_research_tables()
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT status, COUNT(*)::int
                FROM public.pharma_research_reports
                WHERE created_at > NOW() - (%s || ' hours')::interval
                GROUP BY status;
                """,
                (int(hours or 24),),
            )
            report_rows = cursor.fetchall()
            cursor.execute(
                """
                SELECT status, COUNT(*)::int
                FROM pending_tasks
                WHERE task_type = 'pharm_research'
                GROUP BY status;
                """
            )
            task_rows = cursor.fetchall()
        return {
            "reports_by_status": {row[0]: row[1] for row in report_rows},
            "tasks_by_status": {row[0]: row[1] for row in task_rows},
        }
    except Exception as error:
        print(error)
        return {"reports_by_status": {}, "tasks_by_status": {}, "error": str(error)}
    finally:
        if connection is not None:
            connection.close()


def get_pharma_research_report_by_key(research_key: str) -> dict | None:
    connection = None
    try:
        ensure_pharma_research_tables()
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id::text, research_key, patient_id::text, medication_id::text,
                       drug_a, drug_b, resolver_result, planner_result, tool_results,
                       synthesis, critic, gates_result, status, severity, confidence,
                       evidence_count, error_message, created_at::text, updated_at::text
                FROM public.pharma_research_reports
                WHERE research_key = %s
                LIMIT 1;
                """,
                (research_key,),
            )
            row = cursor.fetchone()
        if not row:
            return None
        keys = [
            "id",
            "research_key",
            "patient_id",
            "medication_id",
            "drug_a",
            "drug_b",
            "resolver_result",
            "planner_result",
            "tool_results",
            "synthesis",
            "critic",
            "gates_result",
            "status",
            "severity",
            "confidence",
            "evidence_count",
            "error_message",
            "created_at",
            "updated_at",
        ]
        return dict(zip(keys, row))
    except Exception as error:
        print(error)
        return None
    finally:
        if connection is not None:
            connection.close()


def update_pharma_research_report_status(research_key: str, status: str, error_message: str | None = None) -> bool:
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE public.pharma_research_reports
                SET status = %s,
                    error_message = %s,
                    updated_at = NOW()
                WHERE research_key = %s
                RETURNING id;
                """,
                (status, error_message, research_key),
            )
            row = cursor.fetchone()
        connection.commit()
        return row is not None
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
        return False
    finally:
        if connection is not None:
            connection.close()


def create_agent_approval_from_research_report(report: dict, auto_approve_eligible: bool) -> str | None:
    """
    Stage a researched rule for caregiver veto / human approval.
    """
    connection = None
    try:
        synthesis = report.get("synthesis") or {}
        gates = report.get("gates_result") or {}
        evidence = synthesis.get("evidence") if isinstance(synthesis, dict) else []
        source_urls = [
            str(item.get("url"))
            for item in evidence or []
            if isinstance(item, dict) and str(item.get("url") or "").strip()
        ]
        rule_hash = report.get("research_key")
        if not rule_hash:
            return None
        connection = _connect()
        with connection.cursor() as cursor:
            ensure_pharma_rule_registry_schema()
            cursor.execute(
                """
                INSERT INTO agent_approvals (
                    rule_hash, drug_a, drug_b, severity, status,
                    auto_approve_eligible, veto_expiry, draft_csv_path,
                    source_urls, confidence, gates_result
                )
                VALUES (
                    %s, %s, %s, %s, 'pending',
                    %s, NOW() + (%s || ' hours')::interval, %s,
                    %s, %s, %s::jsonb
                )
                ON CONFLICT (rule_hash) DO UPDATE SET
                    drug_a = EXCLUDED.drug_a,
                    drug_b = EXCLUDED.drug_b,
                    severity = EXCLUDED.severity,
                    auto_approve_eligible = EXCLUDED.auto_approve_eligible,
                    veto_expiry = CASE
                        WHEN agent_approvals.status = 'pending' THEN agent_approvals.veto_expiry
                        ELSE EXCLUDED.veto_expiry
                    END,
                    draft_csv_path = EXCLUDED.draft_csv_path,
                    source_urls = EXCLUDED.source_urls,
                    confidence = EXCLUDED.confidence,
                    gates_result = EXCLUDED.gates_result
                RETURNING id::text;
                """,
                (
                    rule_hash,
                    report.get("drug_a"),
                    report.get("drug_b"),
                    report.get("severity") or synthesis.get("severity") or "medium",
                    bool(auto_approve_eligible),
                    int(getattr(config, "PHARMAGENT_VETO_EXPIRY_HOURS", 48)),
                    f"db:pharma_research_reports/{report.get('id')}",
                    source_urls,
                    report.get("confidence") or synthesis.get("confidence"),
                    _json_or_empty(
                        {
                            "patient_id": report.get("patient_id"),
                            "research_report_id": report.get("id"),
                            "research_key": rule_hash,
                            "synthesis": synthesis,
                            "gates": gates,
                            "source": "pharma_research",
                        }
                    ),
                ),
            )
            row = cursor.fetchone()
        connection.commit()
        return row[0] if row else None
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
        return None
    finally:
        if connection is not None:
            connection.close()


def get_due_auto_approvals(limit: int = 25) -> list[dict]:
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT rule_hash, drug_a, drug_b, severity, confidence,
                       COALESCE(gates_result, '{}'::jsonb), veto_expiry::text
                FROM agent_approvals
                WHERE status = 'pending'
                  AND auto_approve_eligible = TRUE
                  AND veto_expiry IS NOT NULL
                  AND veto_expiry <= NOW()
                ORDER BY veto_expiry ASC
                LIMIT %s;
                """,
                (int(limit or 25),),
            )
            rows = cursor.fetchall()
        return [
            {
                "rule_hash": row[0],
                "drug_a": row[1],
                "drug_b": row[2],
                "severity": row[3],
                "confidence": row[4],
                "gates_result": row[5] or {},
                "veto_expiry": row[6],
            }
            for row in rows
        ]
    except Exception as error:
        print(error)
        return []
    finally:
        if connection is not None:
            connection.close()


def activate_drug_interaction_rule_from_approval(rule_hash: str) -> bool:
    """
    Activate an approved researched rule in drug_interactions.
    """
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT drug_a, drug_b, severity, COALESCE(gates_result, '{}'::jsonb)
                FROM agent_approvals
                WHERE rule_hash = %s
                  AND status IN ('pending', 'human_approved', 'auto_approved', 'finalized')
                LIMIT 1;
                """,
                (rule_hash,),
            )
            row = cursor.fetchone()
            if not row:
                return False
            drug_a, drug_b, severity, gates_result = row
            synthesis = (gates_result or {}).get("synthesis") or {}
            message = synthesis.get("summary") or synthesis.get("mechanism") or (
                f"Potential interaction between {drug_a} and {drug_b}."
            )
            evidence_urls = [
                str(item.get("url"))
                for item in (synthesis.get("evidence") or [])
                if isinstance(item, dict) and str(item.get("url") or "").strip()
            ]
            confidence = float(
                (gates_result or {}).get("confidence")
                or (synthesis or {}).get("confidence")
                or 0.85
            )
            cursor.execute(
                """
                UPDATE drug_interactions
                SET interaction_type = 'researched_interaction',
                    severity = %s,
                    message_template = %s,
                    source = 'pharma_research',
                    last_reviewed = CURRENT_DATE,
                    is_active = TRUE,
                    verification_status = 'live_verified',
                    confidence_score = %s,
                    last_verified_at = NOW(),
                    last_tool_results = %s::jsonb,
                    evidence_urls = %s,
                    review_required = FALSE
                WHERE LEAST(LOWER(drug_a), LOWER(drug_b)) = LEAST(LOWER(%s), LOWER(%s))
                  AND GREATEST(LOWER(drug_a), LOWER(drug_b)) = GREATEST(LOWER(%s), LOWER(%s))
                RETURNING id;
                """,
                (
                    severity,
                    message,
                    confidence,
                    _json_or_empty(gates_result or {}),
                    evidence_urls,
                    drug_a,
                    drug_b,
                    drug_a,
                    drug_b,
                ),
            )
            updated_row = cursor.fetchone()
            if not updated_row:
                cursor.execute(
                    """
                    INSERT INTO drug_interactions (
                        drug_a, drug_b, interaction_type, severity,
                        condition_context, message_template, source,
                        last_reviewed, is_active, verification_status,
                        confidence_score, last_verified_at, last_tool_results,
                        evidence_urls, review_required
                    )
                    VALUES (
                        %s, %s, 'researched_interaction', %s, 'any', %s,
                        'pharma_research', CURRENT_DATE, TRUE, 'live_verified',
                        %s, NOW(), %s::jsonb, %s, FALSE
                    )
                    ON CONFLICT ON CONSTRAINT drug_interactions_drug_a_drug_b_key DO UPDATE SET
                    interaction_type = EXCLUDED.interaction_type,
                    severity = EXCLUDED.severity,
                    message_template = EXCLUDED.message_template,
                    source = EXCLUDED.source,
                    last_reviewed = EXCLUDED.last_reviewed,
                    is_active = TRUE,
                    verification_status = EXCLUDED.verification_status,
                    confidence_score = EXCLUDED.confidence_score,
                    last_verified_at = EXCLUDED.last_verified_at,
                    last_tool_results = EXCLUDED.last_tool_results,
                    evidence_urls = EXCLUDED.evidence_urls,
                    review_required = FALSE;
                    """,
                    (
                        drug_a,
                        drug_b,
                        severity,
                        message,
                        confidence,
                        _json_or_empty(gates_result or {}),
                        evidence_urls,
                    ),
                )
            cursor.execute(
                """
                UPDATE agent_approvals
                SET status = 'finalized',
                    finalized_at = NOW()
                WHERE rule_hash = %s;
                """,
                (rule_hash,),
            )
        connection.commit()
        return True
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
        return False
    finally:
        if connection is not None:
            connection.close()


def create_observation_conflict_alert(
    patient_id: str,
    profile_id: str | None,
    observation_id: str | None,
    conflicts: list[dict],
) -> None:
    """
    Surface caregiver/patient discrepancy records for the primary caregiver via open alerts.
    """
    if not conflicts:
        return
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO alerts (
                    patient_id,
                    type,
                    severity,
                    message_template,
                    data_payload,
                    status
                )
                VALUES (%s, %s, %s, %s, %s, 'open');
                """,
                (
                    _uuid_or_none(patient_id),
                    "caregiver_observation_conflict",
                    "medium",
                    "Caregiver observation may conflict with a recent patient self-report.",
                    _json_or_empty(
                        {
                            "profile_id": _uuid_or_none(profile_id),
                            "observation_id": observation_id,
                            "conflicts": conflicts,
                            "surface_to": "primary_caregiver",
                        }
                    ),
                ),
            )
        connection.commit()
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
        write_audit(
            patient_id,
            profile_id,
            "caregiver_observation_conflict",
            observation_id,
            "OBSERVATION_CONFLICT_ALERT_FAILED",
            "system",
            {"conflicts": conflicts},
        )
    finally:
        if connection is not None:
            connection.close()


def write_audit(
    patient_id: str | None,
    profile_id: str | None,
    entity_type: str,
    entity_id: str | None,
    action: str,
    actor_role: str,
    new_value: dict | None,
) -> None:
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO audit_log (
                    patient_id,
                    profile_id,
                    entity_type,
                    entity_id,
                    action,
                    actor_role,
                    new_value,
                    timestamp
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW());
                """,
                (
                    patient_id,
                    profile_id,
                    entity_type,
                    entity_id,
                    action,
                    actor_role,
                    _json_or_empty(new_value),
                ),
            )
        connection.commit()
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
    finally:
        if connection is not None:
            connection.close()


def _uuid_or_none(value) -> str | None:
    try:
        text = str(value or "").strip()
        return text if len(text) == 36 and text.count("-") == 4 else None
    except Exception:
        return None


def log_embedding_decision(
    patient_id: str | None,
    profile_id: str | None,
    message: str,
    intent: str,
    confidence: float,
    source: str,
    normalized_message: str,
) -> None:
    """
    Log embedding/router classification metadata to audit_log. Never raises.
    """
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO audit_log (
                    patient_id,
                    profile_id,
                    entity_type,
                    entity_id,
                    action,
                    actor_role,
                    new_value,
                    timestamp
                )
                VALUES (
                    %s,
                    %s,
                    'embedding_decision',
                    NULL,
                    'EMBEDDING_DECISION',
                    'system',
                    jsonb_build_object(
                        'message', %s,
                        'intent', %s,
                        'confidence', %s,
                        'source', %s,
                        'normalized_message', %s
                    ),
                    NOW()
                );
                """,
                (
                    _uuid_or_none(patient_id),
                    _uuid_or_none(profile_id),
                    message,
                    intent,
                    confidence,
                    source,
                    normalized_message,
                ),
            )
        connection.commit()
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
    finally:
        if connection is not None:
            connection.close()


def _json_or_empty(value) -> str:
    try:
        return json.dumps(_pg_sanitize(value if value is not None else {}), default=str)
    except Exception as error:
        print(f"JSON serialization failed: {error}")
        return json.dumps(
            {
                "_serialization_error": str(error),
                "_original_type": type(value).__name__,
            }
        )


def _pg_sanitize(value):
    """
    Remove characters PostgreSQL text/jsonb cannot store, especially NUL.
    PDF parsers can emit \x00; JSON encodes it as \\u0000, which Postgres
    rejects for json/jsonb, so sanitize recursively at the DB boundary.
    """
    if isinstance(value, str):
        return _pg_text(value)
    if isinstance(value, dict):
        return {_pg_text(str(key)): _pg_sanitize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_pg_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [_pg_sanitize(item) for item in value]
    return value


def _pg_text(value) -> str | None:
    if value is None:
        return None
    text = str(value)
    if "\x00" in text:
        text = text.replace("\x00", "")
    return text


def _media_task_type(task_type: str) -> str:
    """
    Normalize friendly media task aliases to the current pending_tasks CHECK values.
    """
    aliases = {
        "ocr": "ocr_prescription",
        "image": "ocr_prescription",
        "image_ocr": "ocr_prescription",
        "audio": "transcribe_audio",
        "transcription": "transcribe_audio",
        "pdf": "parse_pdf",
    }
    key = str(task_type or "").strip()
    return aliases.get(key.lower(), key)


# Media ingestion helper: enqueue an OCR/audio/PDF processing task.
def create_pending_task(
    patient_id,
    task_type,
    media_url,
    media_type,
    from_phone,
    payload=None,
) -> str | None:
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO pending_tasks (
                    patient_id,
                    task_type,
                    status,
                    media_url,
                    media_type,
                    from_phone,
                    payload
                )
                VALUES (%s, %s, 'queued', %s, %s, %s, %s)
                RETURNING id::text;
                """,
                (
                    _uuid_or_none(patient_id),
                    _media_task_type(task_type),
                    media_url,
                    media_type,
                    from_phone,
                    _json_or_empty(payload),
                ),
            )
            row = cursor.fetchone()
        connection.commit()
        return row[0] if row else None
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
        return None
    finally:
        if connection is not None:
            connection.close()


# Media ingestion helper: fetch one pending task as a column-name dictionary.
def get_pending_task(task_id) -> dict | None:
    try:
        connection = _connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT *
                    FROM pending_tasks
                    WHERE id = %s;
                    """,
                    (_uuid_or_none(task_id),),
                )
                row = cursor.fetchone()
                columns = [desc[0] for desc in cursor.description or []]
        finally:
            connection.close()

        if row is None:
            return None

        return dict(zip(columns, row))
    except Exception as error:
        print(error)
        return None


def get_queued_pending_tasks(task_type: str, limit: int = 10) -> list[dict]:
    """
    Fetch queued pending tasks for lightweight background pollers.
    """
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM pending_tasks
                WHERE task_type = %s
                  AND status = 'queued'
                ORDER BY created_at ASC
                LIMIT %s;
                """,
                (task_type, max(1, int(limit or 10))),
            )
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description or []]
        return [dict(zip(columns, row)) for row in rows]
    except Exception as error:
        print(error)
        return []
    finally:
        if connection is not None:
            connection.close()


def requeue_stale_pending_tasks(task_type: str, stale_minutes: int = 10, limit: int = 25) -> int:
    """
    Move stale in_progress tasks back to queued so background workers recover
    after crashes, restarts, API stalls, or serialization failures.
    """
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE pending_tasks
                SET status = 'queued',
                    error_message = NULL,
                    updated_at = NOW()
                WHERE id IN (
                    SELECT id
                    FROM pending_tasks
                    WHERE task_type = %s
                      AND status = 'in_progress'
                      AND COALESCE(started_at, updated_at, created_at) < NOW() - (%s || ' minutes')::interval
                      AND attempts < COALESCE(max_attempts, 5)
                    ORDER BY COALESCE(started_at, updated_at, created_at) ASC
                    LIMIT %s
                )
                RETURNING id;
                """,
                (task_type, int(stale_minutes or 10), max(1, int(limit or 25))),
            )
            rows = cursor.fetchall()
        connection.commit()
        return len(rows or [])
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
        return 0
    finally:
        if connection is not None:
            connection.close()


# Media ingestion helper: mark async task progress or final result.
def update_pending_task_status(
    task_id,
    status,
    result_summary=None,
    error_message=None,
) -> None:
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE pending_tasks
                SET status = %s,
                    result_summary = %s,
                    error_message = %s,
                    completed_at = NOW()
                WHERE id = %s;
                """,
                (
                    status,
                    _json_or_empty(result_summary) if result_summary is not None else None,
                    _pg_text(error_message),
                    _uuid_or_none(task_id),
                ),
            )
        connection.commit()
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
    finally:
        if connection is not None:
            connection.close()


def mark_pending_task_in_progress(task_id) -> bool:
    """
    Claim a queued pending task for processing.
    Returns False if another worker already moved it out of queued state.
    """
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE pending_tasks
                SET status = 'in_progress',
                    started_at = COALESCE(started_at, NOW()),
                    attempts = attempts + 1
                WHERE id = %s
                  AND status = 'queued'
                RETURNING id;
                """,
                (_uuid_or_none(task_id),),
            )
            row = cursor.fetchone()
        connection.commit()
        return row is not None
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
        return False
    finally:
        if connection is not None:
            connection.close()


def _media_file_type(value) -> str:
    text = str(value or "").strip().lower()
    return text if text in {"image", "audio", "pdf"} else "image"


def _media_parser_type(value, file_type=None) -> str:
    """
    Keep media_upload inserts compatible with the DB CHECK constraint.
    """
    parser = str(value or "").strip().lower()
    kind = str(file_type or "").strip().lower()
    if parser in {"paddleocr", "whisper", "pdfplumber"}:
        return parser
    if parser in {"asr", "audio"} or kind == "audio":
        return "whisper"
    if parser in {"structured", "pdf", "pymupdf", "fitz"} and kind == "pdf":
        return "pdfplumber"
    return "paddleocr"


def _media_final_status(value) -> str:
    text = str(value or "").strip().lower()
    return text if text in {"active", "suspicious", "failed", "corrected"} else "active"


# Media ingestion helper: persist parser output for uploaded image/audio/PDF.
def insert_media_upload(
    patient_id,
    profile_id,
    pending_task_id,
    file_path,
    file_type,
    parser_type,
    raw_text,
    structured_json,
    parser_confidence,
    final_status="active",
) -> str | None:
    connection = None
    try:
        safe_file_type = _media_file_type(file_type)
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO media_uploads (
                    patient_id,
                    profile_id,
                    pending_task_id,
                    file_path,
                    file_type,
                    parser_type,
                    raw_text,
                    structured_json,
                    parser_confidence,
                    final_status
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id::text;
                """,
                (
                    _uuid_or_none(patient_id),
                    _uuid_or_none(profile_id),
                    _uuid_or_none(pending_task_id),
                    _pg_text(file_path),
                    safe_file_type,
                    _media_parser_type(parser_type, safe_file_type),
                    _pg_text(raw_text),
                    _json_or_empty(structured_json),
                    parser_confidence,
                    _media_final_status(final_status),
                ),
            )
            row = cursor.fetchone()
        connection.commit()
        return row[0] if row else None
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
        return None
    finally:
        if connection is not None:
            connection.close()


# Media ingestion helper: update structured extraction JSON after review/correction.
def update_media_upload_structured(upload_id, structured_json) -> None:
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE media_uploads
                SET structured_json = %s
                WHERE id = %s;
                """,
                (_json_or_empty(structured_json), _uuid_or_none(upload_id)),
            )
        connection.commit()
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
    finally:
        if connection is not None:
            connection.close()


# Medication ingestion helper: discontinue an older active med after inserting its replacement.
def supersede_medication(patient_id, old_drug_name, new_medication_id) -> None:
    """
    Mark old active medication as discontinued and link to new one.
    Call AFTER inserting the new medication. Pass the new medication's ID.
    """
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE medications
                SET status = 'discontinued',
                    end_date = CURRENT_DATE,
                    superseded_by_id = %s
                WHERE patient_id = %s
                  AND drug_name = %s
                  AND status = 'active'
                  AND id != %s;
                """,
                (
                    _uuid_or_none(new_medication_id),
                    _uuid_or_none(patient_id),
                    old_drug_name,
                    _uuid_or_none(new_medication_id),
                ),
            )
        connection.commit()
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
    finally:
        if connection is not None:
            connection.close()


# Lab ingestion helper: fetch the latest value for jump/outlier validation.
def get_last_lab_value(patient_id, test_name) -> dict | None:
    try:
        connection = _connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT test_value, unit, report_date
                    FROM lab_reports
                    WHERE patient_id = %s
                      AND test_name = %s
                    ORDER BY report_date DESC
                    LIMIT 1;
                    """,
                    (_uuid_or_none(patient_id), test_name),
                )
                row = cursor.fetchone()
        finally:
            connection.close()

        if row is None:
            return None

        return {
            "test_value": row[0],
            "unit": row[1],
            "report_date": row[2].isoformat() if hasattr(row[2], "isoformat") else row[2],
        }
    except Exception as error:
        print(error)
        return None


# Lab ingestion helper: insert one parsed lab result linked to its source upload.
def insert_lab_report(
    patient_id,
    test_name,
    test_value,
    unit,
    reference_low,
    reference_high,
    confidence,
    status,
    media_upload_id,
) -> str | None:
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO lab_reports (
                    patient_id,
                    report_date,
                    test_name,
                    test_value,
                    unit,
                    reference_range_low,
                    reference_range_high,
                    confidence,
                    status,
                    media_upload_id,
                    created_at
                )
                VALUES (%s, CURRENT_DATE, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                RETURNING id::text;
                """,
                (
                    _uuid_or_none(patient_id),
                    test_name,
                    test_value,
                    unit,
                    reference_low,
                    reference_high,
                    confidence,
                    status,
                    _uuid_or_none(media_upload_id),
                ),
            )
            row = cursor.fetchone()
        connection.commit()
        return row[0] if row else None
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
        return None
    finally:
        if connection is not None:
            connection.close()


# Drug resolution helper: add or refresh a canonical drug alias/dose record.
def upsert_drug_formulary(
    canonical_name,
    alias,
    dose=None,
    frequency=None,
    resolved_from="rxnorm",
) -> None:
    """
    Insert or update drug formulary entry.
    IMPORTANT: This SQL has 10 placeholders. The Python call must pass exactly
    10 arguments:
    (canonical_name, alias, dose, frequency, resolved_from, alias, alias, dose,
    dose, dose).
    """
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO drug_formulary (
                    canonical_name,
                    common_aliases,
                    common_doses,
                    common_frequencies,
                    resolved_from
                )
                VALUES (%s, ARRAY[%s], ARRAY[%s]::real[], ARRAY[%s], %s)
                ON CONFLICT (canonical_name) DO UPDATE SET
                    common_aliases = CASE
                        WHEN NOT (%s = ANY(drug_formulary.common_aliases))
                        THEN array_append(drug_formulary.common_aliases, %s)
                        ELSE drug_formulary.common_aliases
                    END,
                    common_doses = CASE
                        WHEN %s IS NOT NULL AND NOT (%s = ANY(drug_formulary.common_doses))
                        THEN array_append(drug_formulary.common_doses, %s::real)
                        ELSE drug_formulary.common_doses
                    END,
                    last_used = NOW(),
                    use_count = drug_formulary.use_count + 1;
                """,
                (
                    canonical_name,
                    alias,
                    dose,
                    frequency,
                    resolved_from,
                    alias,
                    alias,
                    dose,
                    dose,
                    dose,
                ),
            )
        connection.commit()
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
    finally:
        if connection is not None:
            connection.close()


# Drug resolution helper: exact alias lookup from the local formulary cache.
def resolve_drug_by_alias(alias) -> str | None:
    try:
        connection = _connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT canonical_name
                    FROM drug_formulary
                    WHERE %s = ANY(common_aliases)
                    LIMIT 1;
                    """,
                    (alias,),
                )
                row = cursor.fetchone()
        finally:
            connection.close()

        return row[0] if row else None
    except Exception as error:
        print(error)
        return None


# Alert helper: create an open patient alert from parser validation or review flows.
def create_alert(patient_id, alert_type, severity, message_template, data_payload) -> str | None:
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO alerts (
                    patient_id,
                    type,
                    severity,
                    message_template,
                    data_payload,
                    status,
                    created_at
                )
                VALUES (%s, %s, %s, %s, %s, 'open', NOW())
                RETURNING id::text;
                """,
                (
                    _uuid_or_none(patient_id),
                    alert_type,
                    severity,
                    message_template,
                    _json_or_empty(data_payload),
                ),
            )
            row = cursor.fetchone()
        connection.commit()
        return row[0] if row else None
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
        return None
    finally:
        if connection is not None:
            connection.close()


def ensure_safe_medication_ingestion_schema() -> bool:
    """
    Add the minimal columns/statuses needed by the post-LLM medication gate.
    Safe to call repeatedly at startup or before candidate writes.
    """
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                ALTER TABLE public.medications
                DROP CONSTRAINT IF EXISTS medications_status_check;

                ALTER TABLE public.medications
                ADD CONSTRAINT medications_status_check
                CHECK (
                    status IN (
                        'draft_extracted',
                        'validation_pending',
                        'suspicious',
                        'interaction_pending',
                        'veto_required',
                        'approved',
                        'active',
                        'rejected',
                        'discontinued',
                        'pending_confirmation',
                        'discarded'
                    )
                );

                ALTER TABLE public.medications
                ADD COLUMN IF NOT EXISTS raw_drug_name TEXT,
                ADD COLUMN IF NOT EXISTS validation_payload JSONB DEFAULT '{}'::jsonb,
                ADD COLUMN IF NOT EXISTS interaction_payload JSONB DEFAULT '{}'::jsonb,
                ADD COLUMN IF NOT EXISTS unresolved_fields TEXT[] DEFAULT ARRAY[]::TEXT[],
                ADD COLUMN IF NOT EXISTS activation_decision TEXT,
                ADD COLUMN IF NOT EXISTS approval_rule_hash TEXT,
                ADD COLUMN IF NOT EXISTS advice TEXT,
                ADD COLUMN IF NOT EXISTS validated_at TIMESTAMPTZ,
                ADD COLUMN IF NOT EXISTS activated_at TIMESTAMPTZ,
                ADD COLUMN IF NOT EXISTS rejected_at TIMESTAMPTZ;

                CREATE INDEX IF NOT EXISTS idx_medications_patient_status
                ON public.medications(patient_id, status);

                CREATE INDEX IF NOT EXISTS idx_medications_media_upload
                ON public.medications(media_upload_id);
                """
            )
        connection.commit()
        return True
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(f"ensure_safe_medication_ingestion_schema failed: {error}")
        raise
    finally:
        if connection is not None:
            connection.close()


def create_draft_medication(
    patient_id: str,
    raw_drug_name: str | None,
    structured_medication: dict | None,
    media_upload_id: str | None,
    prescribed_by: str | None = None,
    source_type: str = "prescription_photo",
    start_date: str | None = None,
    scheduled_times: list[str] | None = None,
) -> str:
    """
    Create a non-active medication candidate from LLM/parser output.
    Returns medication id. Raises on DB failure.
    """
    ensure_safe_medication_ingestion_schema()
    connection = None
    payload = structured_medication or {}
    display_name = str(raw_drug_name or "").strip() or "UNRESOLVED_MEDICATION"
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO medications (
                    patient_id,
                    drug_name,
                    raw_drug_name,
                    dose_amount,
                    dose_unit,
                    frequency,
                    instructions,
                    advice,
                    prescribed_by,
                    start_date,
                    scheduled_times,
                    status,
                    confidence,
                    source_type,
                    media_upload_id,
                    raw_text,
                    validation_payload,
                    recorded_at,
                    updated_at
                )
                VALUES (
                    %s, %s, %s, NULL, NULL, NULL, %s, %s,
                    %s, %s, %s,
                    'draft_extracted', 0.0, %s, %s, %s, %s::jsonb, NOW(), NOW()
                )
                RETURNING id::text;
                """,
                (
                    _uuid_or_none(patient_id),
                    _pg_text(display_name),
                    _pg_text(raw_drug_name),
                    _pg_text(payload.get("instructions")),
                    _pg_text(payload.get("advice")),
                    _pg_text(prescribed_by or payload.get("prescribed_by")),
                    start_date,
                    scheduled_times or [],
                    _pg_text(source_type),
                    _uuid_or_none(media_upload_id),
                    _pg_text(_json_or_empty(payload)),
                    _json_or_empty({"draft": payload, "stage": "draft_extracted"}),
                ),
            )
            row = cursor.fetchone()
        connection.commit()
        if not row:
            raise RuntimeError("create_draft_medication returned no id")
        return row[0]
    except Exception:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        if connection is not None:
            connection.close()


def update_medication_validation_status(
    medication_id: str,
    status: str,
    canonical_drug_name: str | None,
    dose_amount,
    dose_unit: str | None,
    frequency: str | None,
    confidence: float,
    unresolved_fields: list[str] | None,
    validation_payload: dict | None,
    scheduled_times: list[str] | None = None,
) -> bool:
    """
    Persist deterministic validation result for a candidate medication.
    Raises on failure.
    """
    ensure_safe_medication_ingestion_schema()
    allowed = {
        "validation_pending",
        "suspicious",
        "interaction_pending",
        "veto_required",
        "approved",
        "active",
        "rejected",
    }
    if status not in allowed:
        raise ValueError(f"Unsupported medication validation status: {status}")
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE medications
                SET drug_name = COALESCE(NULLIF(%s, ''), drug_name),
                    dose_amount = %s,
                    dose_unit = %s,
                    frequency = %s,
                    confidence = %s,
                    status = %s,
                    unresolved_fields = %s,
                    validation_payload = %s::jsonb,
                    scheduled_times = %s,
                    validated_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s
                RETURNING id;
                """,
                (
                    _pg_text(canonical_drug_name),
                    dose_amount,
                    _pg_text(dose_unit),
                    _pg_text(frequency),
                    float(confidence or 0.0),
                    status,
                    unresolved_fields or [],
                    _json_or_empty(validation_payload or {}),
                    scheduled_times or [],
                    _uuid_or_none(medication_id),
                ),
            )
            row = cursor.fetchone()
        connection.commit()
        if not row:
            raise RuntimeError(f"Medication candidate not found: {medication_id}")
        return True
    except Exception:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        if connection is not None:
            connection.close()


def create_pending_verification_task(
    patient_id: str,
    medication_id: str,
    reason: str,
    payload: dict | None = None,
    from_phone: str | None = None,
) -> str:
    """
    Queue caregiver/manual review for suspicious medication candidates.
    Raises on failure.
    """
    connection = None
    task_payload = {
        "request_type": "medication_candidate_verification",
        "medication_id": str(medication_id),
        "reason": reason,
        "payload": payload or {},
    }
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO pending_tasks (
                    patient_id,
                    task_type,
                    status,
                    payload,
                    from_phone
                )
                VALUES (%s, 'medication_confirmation', 'queued', %s::jsonb, %s)
                RETURNING id::text;
                """,
                (
                    _uuid_or_none(patient_id),
                    _json_or_empty(task_payload),
                    from_phone,
                ),
            )
            row = cursor.fetchone()
        connection.commit()
        if not row:
            raise RuntimeError("create_pending_verification_task returned no id")
        return row[0]
    except Exception:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        if connection is not None:
            connection.close()


def create_interaction_alert(
    patient_id: str,
    medication_id: str,
    severity: str,
    message: str,
    payload: dict | None = None,
) -> str:
    """
    Create a medication interaction alert. Raises on failure.
    """
    connection = None
    alert_payload = dict(payload or {})
    alert_payload["medication_id"] = str(medication_id)
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO alerts (
                    patient_id,
                    type,
                    severity,
                    message_template,
                    data_payload,
                    status
                )
                VALUES (%s, 'drug_interaction', %s, %s, %s::jsonb, 'open')
                RETURNING id::text;
                """,
                (
                    _uuid_or_none(patient_id),
                    _pg_text(severity),
                    _pg_text(message),
                    _json_or_empty(alert_payload),
                ),
            )
            row = cursor.fetchone()
        connection.commit()
        if not row:
            raise RuntimeError("create_interaction_alert returned no id")
        return row[0]
    except Exception:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        if connection is not None:
            connection.close()


def create_veto_approval_record(
    patient_id: str,
    medication_id: str,
    new_drug: str,
    existing_drug: str | None,
    severity: str,
    interaction_payload: dict | None = None,
) -> str:
    """
    Create or refresh an agent_approvals row for a medication candidate.
    Raises on failure.
    """
    ensure_pharma_rule_registry_schema()
    connection = None
    other = str(existing_drug or "verification_required").strip() or "verification_required"
    rule_hash = hashlib.sha256(
        f"medication-candidate:{patient_id}:{medication_id}:{str(new_drug or '').lower()}:{other.lower()}".encode()
    ).hexdigest()
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO agent_approvals (
                    rule_hash,
                    drug_a,
                    drug_b,
                    severity,
                    status,
                    auto_approve_eligible,
                    veto_expiry,
                    draft_csv_path,
                    source_urls,
                    confidence,
                    gates_result
                )
                VALUES (
                    %s, %s, %s, %s, 'pending', false,
                    NOW() + (%s || ' hours')::interval,
                    %s, %s, %s, %s::jsonb
                )
                ON CONFLICT (rule_hash) DO UPDATE SET
                    severity = EXCLUDED.severity,
                    status = CASE
                        WHEN agent_approvals.status IN ('vetoed', 'rejected', 'finalized') THEN agent_approvals.status
                        ELSE 'pending'
                    END,
                    gates_result = EXCLUDED.gates_result,
                    veto_expiry = EXCLUDED.veto_expiry
                RETURNING rule_hash;
                """,
                (
                    rule_hash,
                    new_drug,
                    other,
                    severity,
                    int(getattr(config, "PHARMAGENT_VETO_EXPIRY_HOURS", 48)),
                    f"db:medications/{medication_id}",
                    [],
                    (interaction_payload or {}).get("confidence"),
                    _json_or_empty(
                        {
                            "patient_id": patient_id,
                            "medication_id": medication_id,
                            "interaction_payload": interaction_payload or {},
                            "approval_scope": "medication_activation",
                        }
                    ),
                ),
            )
            row = cursor.fetchone()
        connection.commit()
        if not row:
            raise RuntimeError("create_veto_approval_record returned no rule hash")
        return row[0]
    except Exception:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        if connection is not None:
            connection.close()


def promote_medication_to_active(
    medication_id: str,
    activation_decision: str = "validated_clear",
    interaction_payload: dict | None = None,
) -> bool:
    """
    Promote a previously validated candidate to active medication status.
    Raises on failure.
    """
    ensure_safe_medication_ingestion_schema()
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE medications
                SET status = 'active',
                    activation_decision = %s,
                    interaction_payload = %s::jsonb,
                    activated_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s
                  AND status IN ('interaction_pending', 'approved')
                RETURNING patient_id::text, drug_name;
                """,
                (
                    activation_decision,
                    _json_or_empty(interaction_payload or {}),
                    _uuid_or_none(medication_id),
                ),
            )
            row = cursor.fetchone()
        connection.commit()
        if not row:
            raise RuntimeError(f"Medication candidate could not be promoted: {medication_id}")
        try:
            supersede_medication(row[0], row[1], medication_id)
        except Exception as error:
            print(f"supersede_medication after promotion failed: {error}")
        return True
    except Exception:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        if connection is not None:
            connection.close()


def reject_or_hold_medication(
    medication_id: str,
    status: str,
    reason: str,
    payload: dict | None = None,
    approval_rule_hash: str | None = None,
) -> bool:
    """
    Move a medication candidate to suspicious/veto_required/rejected hold states.
    Raises on failure.
    """
    ensure_safe_medication_ingestion_schema()
    if status not in {"suspicious", "veto_required", "rejected", "validation_pending"}:
        raise ValueError(f"Unsupported hold medication status: {status}")
    connection = None
    hold_payload = dict(payload or {})
    hold_payload["reason"] = reason
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE medications
                SET status = %s,
                    interaction_payload = CASE
                        WHEN %s IN ('veto_required') THEN %s::jsonb
                        ELSE interaction_payload
                    END,
                    validation_payload = CASE
                        WHEN %s IN ('suspicious', 'validation_pending', 'rejected') THEN %s::jsonb
                        ELSE validation_payload
                    END,
                    activation_decision = %s,
                    approval_rule_hash = COALESCE(%s, approval_rule_hash),
                    rejected_at = CASE WHEN %s = 'rejected' THEN NOW() ELSE rejected_at END,
                    updated_at = NOW()
                WHERE id = %s
                RETURNING id;
                """,
                (
                    status,
                    status,
                    _json_or_empty(hold_payload),
                    status,
                    _json_or_empty(hold_payload),
                    reason,
                    approval_rule_hash,
                    status,
                    _uuid_or_none(medication_id),
                ),
            )
            row = cursor.fetchone()
        connection.commit()
        if not row:
            raise RuntimeError(f"Medication candidate not found for hold: {medication_id}")
        return True
    except Exception:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        if connection is not None:
            connection.close()


# Medication ingestion helper: insert one active/suspicious medication row.
def insert_medication(
    patient_id,
    drug_name,
    brand_name=None,
    dose_amount=None,
    dose_unit=None,
    frequency=None,
    instructions=None,
    prescribed_by=None,
    status="active",
    confidence=0.8,
    source_type="prescription_photo",
    media_upload_id=None,
    advice=None,
    start_date=None,
    scheduled_times=None,
) -> str | None:
    connection = None
    try:
        ensure_safe_medication_ingestion_schema()
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO medications (
                    patient_id,
                    drug_name,
                    dose_amount,
                    dose_unit,
                    frequency,
                    instructions,
                    advice,
                    prescribed_by,
                    start_date,
                    scheduled_times,
                    status,
                    confidence,
                    source_type,
                    media_upload_id,
                    recorded_at,
                    updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                RETURNING id::text;
                """,
                (
                    _uuid_or_none(patient_id),
                    _pg_text(drug_name),
                    dose_amount,
                    _pg_text(dose_unit),
                    _pg_text(frequency),
                    _pg_text(instructions),
                    _pg_text(advice),
                    _pg_text(prescribed_by or brand_name),
                    start_date,
                    scheduled_times or [],
                    _pg_text(status),
                    confidence,
                    _pg_text(source_type),
                    _uuid_or_none(media_upload_id),
                ),
            )
            row = cursor.fetchone()
        connection.commit()
        return row[0] if row else None
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
        return None
    finally:
        if connection is not None:
            connection.close()


# Medication ingestion helper: fetch one medication for post-processing hooks.
def get_medication_by_id(medication_id) -> dict | None:
    try:
        connection = _connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id::text, patient_id::text, drug_name, dose_amount, dose_unit,
                           frequency, status, prescribed_by, instructions, advice, scheduled_times, start_date::text
                    FROM medications
                    WHERE id = %s
                    LIMIT 1;
                    """,
                    (_uuid_or_none(medication_id),),
                )
                row = cursor.fetchone()
        finally:
            connection.close()

        if not row:
            return None
        return {
            "id": row[0],
            "patient_id": row[1],
            "drug_name": row[2],
            "dose_amount": row[3],
            "dose_unit": row[4],
            "frequency": row[5],
            "status": row[6],
            "prescribed_by": row[7],
            "instructions": row[8],
            "advice": row[9],
            "scheduled_times": list(row[10] or []),
            "start_date": row[11],
        }
    except Exception as error:
        print(error)
        return None


# Drug safety helper: lookup a known interaction if the table exists.
def check_drug_interaction(drug_a, drug_b) -> dict | None:
    try:
        a = str(drug_a or "").strip()
        b = str(drug_b or "").strip()
        if not a or not b or a.lower() == b.lower():
            return None

        connection = _connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT severity, message_template
                    FROM drug_interactions
                    WHERE (
                        LOWER(drug_a) = LOWER(%s) AND LOWER(drug_b) = LOWER(%s)
                    ) OR (
                        LOWER(drug_a) = LOWER(%s) AND LOWER(drug_b) = LOWER(%s)
                    )
                    ORDER BY CASE severity
                        WHEN 'critical' THEN 1
                        WHEN 'high' THEN 2
                        WHEN 'medium' THEN 3
                        ELSE 4
                    END
                    LIMIT 1;
                    """,
                    (a, b, b, a),
                )
                row = cursor.fetchone()
        finally:
            connection.close()

        if not row:
            return None
        return {"severity": row[0], "message": row[1], "message_template": row[1]}
    except Exception:
        return None


# Media dedupe helper: detect whether this exact file hash was already processed.
def is_duplicate_media(patient_id: str, file_hash: str) -> bool:
    connection = None
    try:
        if not patient_id or not file_hash:
            return False
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id
                FROM media_uploads
                WHERE patient_id = %s
                  AND (
                    structured_json ->> 'file_hash' = %s
                    OR raw_text LIKE %s
                  )
                LIMIT 1;
                """,
                (
                    _uuid_or_none(patient_id),
                    file_hash,
                    f"%{file_hash[:16]}%",
                ),
            )
            return cursor.fetchone() is not None
    except Exception:
        return False
    finally:
        if connection is not None:
            connection.close()


# Medication dedupe helper: prevent same drug+dose duplicate inserts within 1 hour.
def is_duplicate_medication(patient_id: str, drug_name: str, dose_amount, recorded_at: str | None = None) -> bool:
    connection = None
    try:
        if not patient_id or not drug_name:
            return False
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id
                FROM medications
                WHERE patient_id = %s
                  AND LOWER(drug_name) = LOWER(%s)
                  AND (
                    dose_amount = %s
                    OR (%s IS NULL AND dose_amount IS NULL)
                  )
                  AND recorded_at > NOW() - INTERVAL '1 hour'
                LIMIT 1;
                """,
                (
                    _uuid_or_none(patient_id),
                    drug_name,
                    dose_amount,
                    dose_amount,
                ),
            )
            return cursor.fetchone() is not None
    except Exception:
        return False
    finally:
        if connection is not None:
            connection.close()


# ENHANCEMENT IDEA: Crisis cache freshness could be made stricter by also checking
# an expires_at or generated_at column, if the schema later stores one. That would
# prevent an accidentally stale row from being treated as safe just because
# is_fresh stayed true.
#
# ENHANCEMENT IDEA: If crisis_cache.cache_json ever stores nested JSON as text
# from a non-jsonb source, add lightweight schema validation here for required
# emergency packet keys before returning it to crisis handlers.
#
# ENHANCEMENT IDEA: If get_patient_location returns None during a crisis, the
# crisis handler should fall back to the latest trusted caregiver-confirmed
# address from the crisis cache, then ask the user to share live location.
#
# ENHANCEMENT IDEA: If coordinates exist but are out of range, keep returning
# None and log a non-blocking data-quality alert instead of using unsafe location
# data in an emergency packet.
#
# ENHANCEMENT IDEA: For "last medicine taken" UI copy, filter the latest
# medication log to successful taken events so missed/skipped entries do not
# sound like adherence success:
# SELECT m.drug_name, ml.event_type, ml.reported_at::text
# FROM medication_log ml
# JOIN medications m ON ml.medication_id = m.id
# WHERE ml.patient_id = %s
#   AND ml.event_type = 'taken'
# ORDER BY ml.reported_at DESC
# LIMIT 1;


# APPROVED ENHANCEMENTS (commented-out for future activation):
# 1. Phonetic matching for Hinglish: Use jellyfish.metaphone() to match "dard"/"dardh".
# 2. Aho-Corasick automaton for hard gate: Build once at startup for O(n) crisis scanning.
# 3. Centroid auto-refresh: Weekly job to recompute centroids from confirmed interactions.
# 4. Per-user personalisation: Store user-specific alias expansions in profiles.preferences jsonb.
# 5. Fallback to Krutrim Vyakyarth model: If MiniLM confidence < 0.5, try Vyakyarth.
# 6. Async embedding pre-computation: Pre-embed common phrases at idle time.
# 7. Audit-based seed expansion: Monthly job to add high-confidence misclassifications to seed bank.


def get_patient_conditions(patient_id: str) -> list[dict]:
    """
    Fetch known patient conditions.
    Returns [{"condition_name": str, "diagnosed_date": str | None}, ...].
    """
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT condition_name, diagnosed_date::text
                FROM patient_conditions
                WHERE patient_id = %s
                ORDER BY diagnosed_date DESC NULLS LAST, condition_name;
                """,
                (_uuid_or_none(patient_id),),
            )
            rows = cursor.fetchall()
        return [
            {
                "condition_name": row[0],
                "diagnosed_date": row[1],
            }
            for row in rows
        ]
    except Exception as error:
        print(error)
        return []
    finally:
        if connection is not None:
            connection.close()


def get_patient_latest_renal_markers(patient_id: str) -> dict | None:
    """
    Fetch the latest creatinine/eGFR/BUN result per renal marker.
    Returns {"creatinine": {"value": ..., "unit": ..., "report_date": ...}, ...}.
    """
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT ON (LOWER(test_name))
                    LOWER(test_name) AS marker_name,
                    test_value,
                    unit,
                    report_date::text
                FROM lab_reports
                WHERE patient_id = %s
                  AND LOWER(test_name) IN ('creatinine', 'egfr', 'blood_urea_nitrogen')
                ORDER BY LOWER(test_name), report_date DESC NULLS LAST, created_at DESC NULLS LAST;
                """,
                (_uuid_or_none(patient_id),),
            )
            rows = cursor.fetchall()

        if not rows:
            return None

        result = {}
        for row in rows:
            result[row[0]] = {
                "value": row[1],
                "unit": row[2],
                "report_date": row[3],
            }
        return result or None
    except Exception as error:
        print(error)
        return None
    finally:
        if connection is not None:
            connection.close()


def is_pharma_decision_recent(patient_id: str, new_drug: str, decision_hash: str | None = None) -> bool:
    """
    Return True when PharmaAgent already made the same decision recently.
    Prefer the precise context hash when available; fall back to drug-only
    legacy audit rows only when no hash is supplied by older callers.
    """
    connection = None
    try:
        if not patient_id or not new_drug:
            return False
        connection = _connect()
        with connection.cursor() as cursor:
            if decision_hash:
                cursor.execute(
                    """
                    SELECT 1
                    FROM audit_log
                    WHERE patient_id = %s
                      AND action = 'PHARMA_AGENT_DECISION'
                      AND new_value ->> 'decision_hash' = %s
                      AND timestamp > NOW() - (%s || ' minutes')::interval
                    LIMIT 1;
                    """,
                    (
                        _uuid_or_none(patient_id),
                        decision_hash,
                        int(getattr(config, "PHARMA_IDEMPOTENCY_WINDOW_MINUTES", 60)),
                    ),
                )
            else:
                cursor.execute(
                    """
                    SELECT 1
                    FROM audit_log
                    WHERE patient_id = %s
                      AND action = 'PHARMA_AGENT_DECISION'
                      AND LOWER(new_value ->> 'new_drug') = LOWER(%s)
                      AND timestamp > NOW() - (%s || ' minutes')::interval
                    LIMIT 1;
                    """,
                    (
                        _uuid_or_none(patient_id),
                        new_drug,
                        int(getattr(config, "PHARMA_IDEMPOTENCY_WINDOW_MINUTES", 60)),
                    ),
                )
            return cursor.fetchone() is not None
    except Exception as error:
        print(error)
        return False
    finally:
        if connection is not None:
            connection.close()


def get_all_drug_interactions() -> list[dict]:
    """
    Query active offline interaction rules.
    """
    connection = None
    try:
        ensure_default_drug_interactions()
        ensure_pharma_rule_registry_schema()
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT drug_a, drug_b, severity, message_template,
                       COALESCE(verification_status, 'seeded') AS verification_status,
                       COALESCE(confidence_score, 0.60) AS confidence_score,
                       COALESCE(last_tool_results, '{}'::jsonb) AS last_tool_results,
                       COALESCE(evidence_urls, ARRAY[]::text[]) AS evidence_urls,
                       COALESCE(review_required, FALSE) AS review_required,
                       source,
                       last_verified_at::text
                FROM drug_interactions
                WHERE is_active = TRUE
                  AND COALESCE(verification_status, 'seeded') <> 'retired';
                """
            )
            rows = cursor.fetchall()
        return [
            {
                "drug_a": row[0],
                "drug_b": row[1],
                "severity": row[2],
                "message_template": row[3],
                "verification_status": row[4],
                "confidence_score": float(row[5] or 0.0),
                "last_tool_results": row[6] or {},
                "evidence_urls": list(row[7] or []),
                "review_required": bool(row[8]),
                "source": row[9],
                "last_verified_at": row[10],
            }
            for row in rows
        ]
    except Exception as error:
        print(error)
        return []
    finally:
        if connection is not None:
            connection.close()


def ensure_pharma_rule_registry_schema() -> bool:
    """
    Add verification metadata to drug_interactions and approval challenge codes.
    This keeps seeded rules as a fast safety baseline while allowing live API
    verification and primary-caregiver veto enforcement.
    """
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                ALTER TABLE drug_interactions
                ADD COLUMN IF NOT EXISTS verification_status TEXT NOT NULL DEFAULT 'seeded',
                ADD COLUMN IF NOT EXISTS confidence_score FLOAT NOT NULL DEFAULT 0.60,
                ADD COLUMN IF NOT EXISTS last_verified_at TIMESTAMPTZ,
                ADD COLUMN IF NOT EXISTS last_tool_results JSONB NOT NULL DEFAULT '{}'::jsonb,
                ADD COLUMN IF NOT EXISTS evidence_urls TEXT[] NOT NULL DEFAULT '{}',
                ADD COLUMN IF NOT EXISTS review_required BOOLEAN NOT NULL DEFAULT TRUE;
                """
            )
            cursor.execute(
                """
                UPDATE drug_interactions
                SET verification_status = COALESCE(NULLIF(verification_status, ''), 'seeded'),
                    confidence_score = COALESCE(confidence_score, 0.60),
                    review_required = COALESCE(review_required, TRUE)
                WHERE verification_status IS NULL
                   OR confidence_score IS NULL
                   OR review_required IS NULL;
                """
            )
            cursor.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_drug_interactions_unordered_active
                ON drug_interactions (
                    LEAST(LOWER(drug_a), LOWER(drug_b)),
                    GREATEST(LOWER(drug_a), LOWER(drug_b))
                )
                WHERE is_active = TRUE;
                """
            )
            cursor.execute(
                """
                ALTER TABLE agent_approvals
                ADD COLUMN IF NOT EXISTS approval_code_hash TEXT,
                ADD COLUMN IF NOT EXISTS approval_code_expires_at TIMESTAMPTZ,
                ADD COLUMN IF NOT EXISTS approval_code_used_at TIMESTAMPTZ,
                ADD COLUMN IF NOT EXISTS approval_code_attempts INTEGER NOT NULL DEFAULT 0;
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_agent_approvals_code_hash
                ON agent_approvals (approval_code_hash)
                WHERE approval_code_hash IS NOT NULL;
                """
            )
        connection.commit()
        return True
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(f"ensure_pharma_rule_registry_schema failed: {error}")
        return False
    finally:
        if connection is not None:
            connection.close()


def ensure_default_drug_interactions() -> int:
    """
    Ensure the deterministic PharmaAgent has a baseline offline rule set.
    This is intentionally idempotent and only seeds when there are no active
    rules, so production rule curation remains the source of truth once loaded.
    Returns the active rule count after the check/seed attempt.
    """
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM drug_interactions WHERE is_active = TRUE;")
            active_count = int(cursor.fetchone()[0] or 0)
            if active_count > 0:
                return active_count

            cursor.executemany(
                """
                INSERT INTO drug_interactions (
                    drug_a,
                    drug_b,
                    interaction_type,
                    severity,
                    condition_context,
                    message_template,
                    source,
                    last_reviewed,
                    is_active
                )
                VALUES (%s, %s, %s, %s, 'any', %s, 'carecircle_baseline_seed', CURRENT_DATE, TRUE)
                ON CONFLICT (drug_a, drug_b) DO UPDATE SET
                    interaction_type = EXCLUDED.interaction_type,
                    severity = EXCLUDED.severity,
                    condition_context = EXCLUDED.condition_context,
                    message_template = EXCLUDED.message_template,
                    source = EXCLUDED.source,
                    last_reviewed = EXCLUDED.last_reviewed,
                    is_active = TRUE;
                """,
                DEFAULT_DRUG_INTERACTION_RULES,
            )
            cursor.execute("SELECT COUNT(*) FROM drug_interactions WHERE is_active = TRUE;")
            active_count = int(cursor.fetchone()[0] or 0)
        connection.commit()
        return active_count
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(f"ensure_default_drug_interactions failed: {error}")
        return 0
    finally:
        if connection is not None:
            connection.close()


def update_drug_interaction_verification(
    drug_a: str,
    drug_b: str,
    verification_status: str,
    confidence_score: float,
    tool_results: dict,
    evidence_urls: list[str] | None = None,
    review_required: bool = False,
) -> bool:
    connection = None
    try:
        ensure_pharma_rule_registry_schema()
        allowed = {"seeded", "live_verified", "review_required", "disputed", "retired"}
        status = str(verification_status or "review_required").lower()
        if status not in allowed:
            status = "review_required"
        score = max(0.0, min(1.0, float(confidence_score or 0.0)))
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE drug_interactions
                SET verification_status = %s,
                    confidence_score = %s,
                    last_verified_at = NOW(),
                    last_tool_results = %s::jsonb,
                    evidence_urls = %s,
                    review_required = %s,
                    last_reviewed = CURRENT_DATE
                WHERE is_active = TRUE
                  AND LEAST(LOWER(drug_a), LOWER(drug_b)) = LEAST(LOWER(%s), LOWER(%s))
                  AND GREATEST(LOWER(drug_a), LOWER(drug_b)) = GREATEST(LOWER(%s), LOWER(%s))
                RETURNING id;
                """,
                (
                    status,
                    score,
                    _json_or_empty(tool_results or {}),
                    evidence_urls or [],
                    bool(review_required),
                    drug_a,
                    drug_b,
                    drug_a,
                    drug_b,
                ),
            )
            row = cursor.fetchone()
        connection.commit()
        return row is not None
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
        return False
    finally:
        if connection is not None:
            connection.close()


def get_drug_interaction_registry_summary() -> dict:
    connection = None
    try:
        ensure_pharma_rule_registry_schema()
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COALESCE(verification_status, 'seeded'), COUNT(*)
                FROM drug_interactions
                WHERE is_active = TRUE
                GROUP BY COALESCE(verification_status, 'seeded')
                ORDER BY 1;
                """
            )
            by_status = {row[0]: int(row[1] or 0) for row in cursor.fetchall()}
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM drug_interactions
                WHERE is_active = TRUE
                  AND review_required = TRUE;
                """
            )
            review_required = int(cursor.fetchone()[0] or 0)
        return {"by_status": by_status, "review_required": review_required}
    except Exception as error:
        print(error)
        return {"by_status": {}, "review_required": 0}
    finally:
        if connection is not None:
            connection.close()


def ensure_renal_dosing_rules() -> int:
    """
    Ensure broad renal safety rules exist. Production can extend this table
    without changing Python code.
    """
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS renal_dosing_rules (
                    drug_name TEXT PRIMARY KEY,
                    advisory_egfr DOUBLE PRECISION DEFAULT 60,
                    warning_egfr DOUBLE PRECISION DEFAULT 45,
                    critical_egfr DOUBLE PRECISION DEFAULT 30,
                    default_severity TEXT DEFAULT 'medium',
                    message_template TEXT,
                    source TEXT DEFAULT 'carecircle_baseline',
                    is_active BOOLEAN DEFAULT TRUE,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );
                """
            )
            cursor.executemany(
                """
                INSERT INTO renal_dosing_rules (
                    drug_name, advisory_egfr, warning_egfr, critical_egfr,
                    default_severity, message_template, source, is_active, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, 'carecircle_baseline', TRUE, NOW())
                ON CONFLICT (drug_name) DO NOTHING;
                """,
                DEFAULT_RENAL_DOSING_RULES,
            )
            cursor.execute("SELECT COUNT(*) FROM renal_dosing_rules WHERE is_active = TRUE;")
            count = int(cursor.fetchone()[0] or 0)
        connection.commit()
        return count
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(f"ensure_renal_dosing_rules failed: {error}")
        return 0
    finally:
        if connection is not None:
            connection.close()


def get_renal_dosing_rules_for_drug(drug_name: str) -> list[dict]:
    connection = None
    try:
        ensure_renal_dosing_rules()
        normalized = str(drug_name or "").strip().lower()
        if not normalized:
            return []
        tokens = [token for token in normalized.replace("+", " ").split() if token]
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT drug_name, advisory_egfr, warning_egfr, critical_egfr,
                       default_severity, message_template, source
                FROM renal_dosing_rules
                WHERE is_active = TRUE
                  AND (
                    LOWER(drug_name) = %s
                    OR LOWER(drug_name) = ANY(%s)
                    OR %s LIKE '%%' || LOWER(drug_name) || '%%'
                  )
                ORDER BY LENGTH(drug_name) DESC
                LIMIT 10;
                """,
                (normalized, tokens, normalized),
            )
            rows = cursor.fetchall()
        return [
            {
                "drug_name": row[0],
                "advisory_egfr": row[1],
                "warning_egfr": row[2],
                "critical_egfr": row[3],
                "default_severity": row[4],
                "message_template": row[5],
                "source": row[6],
            }
            for row in rows
        ]
    except Exception as error:
        print(error)
        return []
    finally:
        if connection is not None:
            connection.close()


def ensure_notification_outbox() -> bool:
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS notification_outbox (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    patient_id UUID,
                    profile_id UUID,
                    to_phone TEXT,
                    channel TEXT NOT NULL DEFAULT 'web',
                    priority TEXT NOT NULL DEFAULT 'normal',
                    message TEXT NOT NULL,
                    payload JSONB DEFAULT '{}'::jsonb,
                    status TEXT NOT NULL DEFAULT 'queued',
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    sent_at TIMESTAMPTZ
                );
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_notification_outbox_status
                ON notification_outbox (status, priority, created_at);
                """
            )
        connection.commit()
        return True
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(f"ensure_notification_outbox failed: {error}")
        return False
    finally:
        if connection is not None:
            connection.close()


def enqueue_notification_outbox(
    patient_id: str | None,
    to_phone: str | None,
    message: str,
    channel: str = "web",
    priority: str = "normal",
    payload: dict | None = None,
    profile_id: str | None = None,
) -> str | None:
    connection = None
    try:
        ensure_notification_outbox()
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO notification_outbox (
                    patient_id, profile_id, to_phone, channel, priority,
                    message, payload, status
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, 'queued')
                RETURNING id::text;
                """,
                (
                    _uuid_or_none(patient_id),
                    _uuid_or_none(profile_id),
                    to_phone,
                    channel,
                    priority,
                    message,
                    _json_or_empty(payload or {}),
                ),
            )
            row = cursor.fetchone()
        connection.commit()
        return row[0] if row else None
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
        return None
    finally:
        if connection is not None:
            connection.close()


def update_notification_outbox_status(outbox_id: str, status: str, error_message: str | None = None) -> bool:
    connection = None
    try:
        if not outbox_id:
            return False
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE notification_outbox
                SET status = %s,
                    last_error = %s,
                    sent_at = CASE WHEN %s = 'sent' THEN NOW() ELSE sent_at END,
                    retry_count = CASE WHEN %s = 'failed' THEN retry_count + 1 ELSE retry_count END
                WHERE id = %s;
                """,
                (
                    status,
                    error_message,
                    status,
                    status,
                    _uuid_or_none(outbox_id),
                ),
            )
        connection.commit()
        return True
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
        return False
    finally:
        if connection is not None:
            connection.close()


def daily_summary_sent_today(patient_id: str, brief_type: str = "summary") -> bool:
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM audit_log
                WHERE patient_id = %s
                  AND action = 'DAILY_CARE_SUMMARY_SENT'
                  AND timestamp::date = CURRENT_DATE
                  AND COALESCE(new_value ->> 'brief_type', 'summary') = %s
                LIMIT 1;
                """,
                (_uuid_or_none(patient_id), str(brief_type or "summary")),
            )
            return cursor.fetchone() is not None
    except Exception:
        return False
    finally:
        if connection is not None:
            connection.close()


def get_patient_name(patient_id: str) -> str | None:
    """
    Fetch patient display name by joining patients.profile_id to profiles.id.
    """
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT profiles.full_name
                FROM patients
                JOIN profiles ON patients.profile_id = profiles.id
                WHERE patients.id = %s
                LIMIT 1;
                """,
                (_uuid_or_none(patient_id),),
            )
            row = cursor.fetchone()
        return row[0] if row else None
    except Exception as error:
        print(error)
        return None
    finally:
        if connection is not None:
            connection.close()


def record_pharmagent_feedback(
    drug_pair: str,
    outcome: str,
    reason: str | None,
    created_by: str | None,
) -> str | None:
    """
    Record caregiver feedback for PharmaAgent self-learning.
    Uses created_by when the deployed schema has that column; older schemas still
    accept the feedback row without breaking callers.
    """
    connection = None
    try:
        connection = _connect()
        columns = _table_columns(connection, "pharmagent_feedback")
        insert_columns = ["drug_pair", "outcome", "reason"]
        values = [drug_pair, outcome, reason]

        if "created_by" in columns:
            insert_columns.append("created_by")
            values.append(created_by)

        placeholders = ", ".join(["%s"] * len(insert_columns))
        column_sql = ", ".join(insert_columns)

        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO pharmagent_feedback ({column_sql})
                VALUES ({placeholders})
                RETURNING id::text;
                """,
                tuple(values),
            )
            row = cursor.fetchone()
        connection.commit()
        return row[0] if row else None
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
        return None
    finally:
        if connection is not None:
            connection.close()


def get_unprocessed_feedback() -> list[dict]:
    """
    Fetch feedback rows that have not been processed for PharmaAgent rule updates.
    If the deployed schema predates the processed flag, return recent rows with
    processed=False in the response so callers can still operate safely.
    """
    connection = None
    try:
        connection = _connect()
        columns = _table_columns(connection, "pharmagent_feedback")
        has_processed = "processed" in columns

        select_columns = [
            "id::text",
            "drug_pair",
            "outcome",
            "reason",
            "created_at::text",
        ]
        keys = ["id", "drug_pair", "outcome", "reason", "created_at"]

        if "created_by" in columns:
            select_columns.append("created_by::text")
            keys.append("created_by")

        if has_processed:
            select_columns.append("processed")
            keys.append("processed")
            where_sql = "WHERE processed = FALSE"
        else:
            where_sql = ""

        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT {", ".join(select_columns)}
                FROM pharmagent_feedback
                {where_sql}
                ORDER BY created_at ASC
                LIMIT 100;
                """
            )
            rows = cursor.fetchall()

        feedback = []
        for row in rows:
            item = {key: row[index] for index, key in enumerate(keys)}
            if "processed" not in item:
                item["processed"] = False
            feedback.append(item)
        return feedback
    except Exception as error:
        print(error)
        return []
    finally:
        if connection is not None:
            connection.close()


def mark_feedback_processed(feedback_id: str) -> bool:
    """
    Mark a PharmaAgent feedback row as processed when the schema supports it.
    Older deployments without the processed column return False without raising.
    """
    connection = None
    try:
        connection = _connect()
        columns = _table_columns(connection, "pharmagent_feedback")
        if "processed" not in columns:
            return False

        set_columns = ["processed = TRUE"]
        values = []
        if "processed_at" in columns:
            set_columns.append("processed_at = NOW()")

        values.append(_uuid_or_none(feedback_id) or feedback_id)
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE pharmagent_feedback
                SET {", ".join(set_columns)}
                WHERE id = %s;
                """,
                tuple(values),
            )
            updated = cursor.rowcount > 0
        connection.commit()
        return updated
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(error)
        return False
    finally:
        if connection is not None:
            connection.close()


def insert_care_note(patient_id: str, note_type: str, content: str, source_type: str, file_hash: str) -> bool:
    """
    Insert a care note such as advice, discharge, or general document content.
    Never raises to callers.
    """
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS care_notes (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    patient_id UUID REFERENCES patients(id) ON DELETE CASCADE,
                    note_type TEXT NOT NULL DEFAULT 'general',
                    content TEXT NOT NULL DEFAULT '',
                    source_type TEXT,
                    file_hash TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_care_notes_patient_created
                ON care_notes (patient_id, created_at DESC);
                """
            )
            cursor.execute(
                """
                INSERT INTO care_notes (
                    patient_id, note_type, content, source_type, file_hash, created_at
                )
                VALUES (%s, %s, %s, %s, %s, NOW());
                """,
                (_uuid_or_none(patient_id), note_type, content, source_type, file_hash),
            )
        connection.commit()
        return True
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(f"DB insert_care_note failed: {error}")
        return False
    finally:
        if connection is not None:
            connection.close()


def insert_referral(
    patient_id: str,
    specialist: str,
    reason: str,
    urgency: str,
    source_type: str,
    file_hash: str,
) -> None:
    """
    Insert a referral document extraction into referrals. Never raises.
    """
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO referrals (
                    patient_id, specialist_name, reason, urgency, source_type,
                    file_hash, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, NOW());
                """,
                (_uuid_or_none(patient_id), specialist, reason, urgency, source_type, file_hash),
            )
        connection.commit()
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(f"DB insert_referral failed: {error}")
    finally:
        if connection is not None:
            connection.close()


def insert_caregiver_event(
    patient_id: str,
    event_type: str,
    details: str,
    source_type: str,
    file_hash: str,
) -> None:
    """
    Insert a caregiver event extracted from notes or voice input. Never raises.
    """
    connection = None
    try:
        connection = _connect()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO caregiver_events (
                    patient_id, event_type, details, source_type, file_hash,
                    created_at
                )
                VALUES (%s, %s, %s, %s, %s, NOW());
                """,
                (_uuid_or_none(patient_id), event_type, details, source_type, file_hash),
            )
        connection.commit()
    except Exception as error:
        if connection is not None:
            connection.rollback()
        print(f"DB insert_caregiver_event failed: {error}")
    finally:
        if connection is not None:
            connection.close()
