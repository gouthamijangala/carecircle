# Demo Video Scenarios

Date verified: 2026-05-18

These scenarios were tested end to end in the current local environment. They are suitable for a demo video because they returned successful responses through the app API or dedicated verification scripts.

Use the demo caregiver phone number:

```text
+919876543211
```

In JSON/API calls, use:

```text
+919876543211
```

If typing into the web UI, use the visible caregiver chat as normal.

## Pre-Demo Health Check

Start the server:

```powershell
.\venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Run the baseline check:

```powershell
.\venv\Scripts\python.exe scripts\verify\run_baseline.py
```

Expected result:

```text
BASELINE_VERIFICATION_PASS
```

## Scenario 1: System Health And Pharma Dashboard

Purpose: show the system has health endpoints and PharmaAgent dashboard data.

Demo steps:

1. Open:

```text
http://127.0.0.1:8000/health/pharma
```

2. Open:

```text
http://127.0.0.1:8000/api/pharma/dashboard?phone=%2B919876543211
```

Expected output:

- `/health/pharma` returns HTTP 200.
- The response includes keys such as `pharma_agent_status`, `rules_loaded`, `llm_available`, `daily_summary_enabled`, or equivalent PharmaAgent health fields.
- `/api/pharma/dashboard` returns HTTP 200.
- The dashboard response includes `status`, `profile`, `alerts`, `approvals`, `research_reports`, `rule_registry`, and `llm_gateway_health`.

Verification command:

```powershell
.\venv\Scripts\python.exe -c "from fastapi.testclient import TestClient; from main import app; c=TestClient(app); print(c.get('/health/pharma').status_code); print(c.get('/api/pharma/dashboard?phone=%2B919876543211').status_code)"
```

Pass criteria:

```text
200
200
```

## Scenario 2: Greeting And Help

Purpose: show the caregiver can start a natural chat.

Input:

```text
Hi
```

Verified API response:

```text
Hello Meera Sharma! CareCircle is here to help. You can ask: 'What meds are active?', 'How is the patient?', or send BP/sugar readings.
```

Demo steps:

1. In the chat UI, send `Hi`.
2. Show the assistant greeting and suggested actions.

Verification command:

```powershell
.\venv\Scripts\python.exe -c "from fastapi.testclient import TestClient; from main import app; r=TestClient(app).post('/api/send', json={'phone':'+919876543211','message':'Hi'}).json(); print(r['intent']); print(r['reply'])"
```

Pass criteria:

- Intent is `greeting_help`.
- Reply greets `Meera Sharma`.
- Reply includes examples of what the caregiver can ask.

## Scenario 3: Active Medication List

Purpose: show active medications are fetched from the live database.

Input:

```text
what medicines am I on
```

Verified API response:

```text
Active medications:
1. atorvastatin 40.0mg HS
2. telmisartan 40.0mg OD

Reply HELP for menu.
```

Demo steps:

1. Send `what medicines am I on`.
2. Show the active medication list.

Verification command:

```powershell
.\venv\Scripts\python.exe -c "from fastapi.testclient import TestClient; from main import app; r=TestClient(app).post('/api/send', json={'phone':'+919876543211','message':'what medicines am I on'}).json(); print(r['intent']); print(r['reply'])"
```

Pass criteria:

- Intent is `medication_list`.
- Reply starts with `Active medications:`.
- Reply includes `atorvastatin` and `telmisartan` in the current demo DB.

## Scenario 4: Current Medication Query

Purpose: show informal medication timing queries route to the active medication list.

Input:

```text
what should I take now
```

Verified API response:

```text
Active medications:
1. atorvastatin 40.0mg HS
2. telmisartan 40.0mg OD

Reply HELP for menu.
```

Demo steps:

1. Send `what should I take now`.
2. Show that CareCircle answers from active medication records.

Verification command:

```powershell
.\venv\Scripts\python.exe -c "from fastapi.testclient import TestClient; from main import app; r=TestClient(app).post('/api/send', json={'phone':'+919876543211','message':'what should I take now'}).json(); print(r['intent']); print(r['reply'])"
```

Pass criteria:

- Intent is `medication_list`.
- Reply returns the active medications.

## Scenario 5: Filtered Appointment Query

Purpose: show appointment search respects the caregiver query and does not return unrelated appointments.

Input:

```text
when is the next general checkup for uncle
```

Verified API response:

```text
Upcoming appointments:
1. 19 May 2026, 11:09 PM IST - Dr. Rajan Mehta at Apollo Hospital, Bangalore. Notes: Annual physical exam, fasting required
```

Demo steps:

1. Send `when is the next general checkup for uncle`.
2. Show that only the general checkup appears, not blood panel, physiotherapy, or MRI appointments.

Verification command:

```powershell
.\venv\Scripts\python.exe -c "from fastapi.testclient import TestClient; from main import app; r=TestClient(app).post('/api/send', json={'phone':'+919876543211','message':'when is the next general checkup for uncle'}).json(); print(r['intent']); print(r['reply'])"
```

Pass criteria:

- Intent is `appointment_query`.
- Reply includes `Dr. Rajan Mehta` or `Annual physical exam`.
- Reply does not list unrelated appointment types.

## Scenario 6: Appointment Creation With Follow-Up Time And Confirmation

Purpose: show multi-turn context works for creating and confirming appointments.

Inputs:

```text
create cardiology appointment on 29 June 2026
2:00 PM
YES
```

Verified output sequence:

```text
I can add this appointment, but I need time. Please send the appointment time.
```

```text
Appointment saved: 29 Jun 2026, 02:00 PM IST with cardiology at location not recorded. Reply YES to confirm or NO to cancel.
```

```text
Appointment confirmed. I will include it in reminders and daily briefs.
```

Demo steps:

1. Send `create cardiology appointment on 29 June 2026`.
2. When the system asks for time, send `2:00 PM`.
3. When the system asks for confirmation, send `YES`.
4. Show that the final reply confirms the appointment.

Verification command:

```powershell
.\venv\Scripts\python.exe verify_appointment_workflow.py
```

Pass criteria:

- `APPOINTMENT_WORKFLOW_VERIFICATION_PASS`
- In manual UI flow, intents are `appointment_add`, `appointment_add`, and `appointment_confirmed`.

## Scenario 7: Emergency / Crisis Card

Purpose: show crisis routing, emergency card generation, active medications, doctor/caregiver contacts, and notification logging.

Input:

```text
heart attack
```

Verified API response starts with:

```text
EMERGENCY CARD - Rajesh Sharma
Current medicines:
- atorvastatin 40.0mg - HS (9:30 PM)
- telmisartan 40.0mg - OD (7:30 AM)
```

Verified response also includes:

```text
Emergency contacts
Caregiver alerts sent: Meera Sharma (primary_caregiver), Rani Gupta (secondary_caregiver), Dr. Babul Reddy (doctor)
```

Demo steps:

1. Send `heart attack`.
2. Show the generated emergency card.
3. Highlight active medicines, doctor, caregivers, location/map link, and alert confirmation.

Verification command:

```powershell
.\venv\Scripts\python.exe -c "from fastapi.testclient import TestClient; from main import app; r=TestClient(app).post('/api/send', json={'phone':'+919876543211','message':'heart attack'}).json(); print(r['intent']); print(r['confidence']); print(r['reply'][:500])"
```

Pass criteria:

- Intent is `crisis_medical`.
- Confidence is `1.0`.
- Reply contains `EMERGENCY CARD`.
- Reply contains current medicines and emergency contacts.

## Scenario 8: Daily Caregiver Briefs

Purpose: show the system can generate a 10AM day brief and a 10PM quick summary from live DB data.

Demo command:

```powershell
.\venv\Scripts\python.exe -c "import daily_summary, db; pid=db.get_active_patient_ids()[0]; ctx=daily_summary.build_brief_context(pid); print(daily_summary.format_day_brief(ctx)); print(); print(daily_summary.format_night_summary(ctx))"
```

Verified output includes:

```text
10AM CareCircle day brief for Rajesh Sharma
Yesterday meds:
Doctor:
Tests:
Pending approvals:
Medication review:
Alerts:
```

And:

```text
10PM quick summary for Rajesh Sharma
Today meds:
Open alerts:
Pending approvals:
Next:
```

Pass criteria:

- Both summaries render without exceptions.
- Times are shown in IST.
- Missing data uses friendly fallback text.
- Pending approvals, medication review, alerts, appointments, and tests are read from DB.

## Scenario 9: PharmaAgent Deterministic Interaction Check

Purpose: show PharmaAgent detects a critical interaction from active-medication context.

Demo command:

```powershell
.\venv\Scripts\python.exe verify_pharma_agent.py
```

Expected output:

```text
PHARMA_AGENT_VERIFICATION_PASS
```

Technical behavior verified:

- Rule engine loads active interaction rules.
- Warfarin + Aspirin evaluates as `critical`.
- Side-effect known-hint lookup works.
- Idempotency protection works.
- Approval/alert side-effect path is verified in an isolated repeatable way.

Pass criteria:

- Script prints `PHARMA_AGENT_VERIFICATION_PASS`.

## Scenario 10: PharmaAgent Live Evidence Tools

Purpose: show live evidence tooling status for interaction research.

Demo command:

```powershell
.\venv\Scripts\python.exe verify_pharma_live_tools.py
```

Verified result:

```text
overall_confidence_score: 1.0
failing_tools: []
```

Current tool behavior:

- Local `drug_interactions`: OK.
- OpenFDA: OK.
- PubMed: OK.
- RxNav interaction API: skipped because the interaction endpoint is disabled/discontinued in current config.

Pass criteria:

- Script exits with code `0`.
- `failing_tools` is empty.
- At least local rules and one live evidence source return OK.

Demo note:

This scenario depends on external internet/API availability. If the network is unstable during recording, use Scenario 9 instead.

## Scenario 10A: PharmaAgent Health And Rule Registry Readiness

Purpose: show PharmaAgent is enabled, has DB rules loaded, and exposes operational health for the demo.

Demo steps:

1. Open:

```text
http://127.0.0.1:8000/health/pharma
```

2. Point out these fields:

```text
pharma_agent_status
rules_loaded
db_rules_loaded
llm_gateway_health
notification_outbox_ready
daily_summary_enabled
```

3. Open:

```text
http://127.0.0.1:8000/api/pharma/dashboard?phone=%2B919876543211
```

4. Show that the dashboard includes rule registry, alerts, approvals, research reports, and profile context.

Verification command:

```powershell
.\venv\Scripts\python.exe -c "from fastapi.testclient import TestClient; from main import app; c=TestClient(app); h=c.get('/health/pharma').json(); d=c.get('/api/pharma/dashboard?phone=%2B919876543211').json(); print(h.get('pharma_agent_status'), h.get('rules_loaded') or h.get('db_rules_loaded')); print(d.get('status'), sorted(d.keys()))"
```

Pass criteria:

- `/health/pharma` returns HTTP 200.
- `rules_loaded` or `db_rules_loaded` is greater than zero.
- Dashboard returns `status`, `profile`, `alerts`, `approvals`, `research_reports`, and `rule_registry`.

## Scenario 10B: PharmaAgent Critical Pair Detection Without Mutating Medications

Purpose: show the deterministic rule engine can detect a critical Warfarin + Aspirin interaction against active-medication context.

Demo command:

```powershell
.\venv\Scripts\python.exe -c "from pharma_agent import PharmaSafetyEngine; e=PharmaSafetyEngine(); r=e.evaluate('d0000001-0002-0001-0001-000000000001','Warfarin',{'active_meds':[{'drug_name':'Aspirin'}],'conditions':[],'renal_markers':None}); print('severity:', r['max_severity']); print('interactions:', len(r['interactions'])); print(r['interactions'][0]['message'])"
```

Expected output:

```text
severity: critical
interactions: 1
Warfarin with aspirin can greatly increase bleeding risk. Contact the doctor before combining.
```

Demo steps:

1. Run the command in terminal.
2. Explain that PharmaAgent checks one candidate drug against active medication context.
3. Show `severity: critical`.
4. Show the plain-language interaction message.

Pass criteria:

- Severity is `critical`.
- Interaction count is at least `1`.
- Message mentions Warfarin + Aspirin bleeding risk.

## Scenario 10C: PharmaAgent Full Verification With Idempotency And Approval Path

Purpose: show the full PharmaAgent verification script covers rule loading, interaction evaluation, known side-effect hints, idempotency, and approval/alert side-effect behavior.

Demo command:

```powershell
.\venv\Scripts\python.exe verify_pharma_agent.py
```

Expected output:

```text
PHARMA_AGENT_VERIFICATION_PASS
```

What this proves:

- `PHARMA_AGENT_ENABLED` is true.
- `drug_interactions` rules are available.
- Warfarin + Aspirin resolves as a critical interaction.
- Side-effect known-hint lookup works.
- Duplicate/recent PharmaAgent decisions are skipped.
- Approval/alert path is verified in an isolated repeatable way.

Pass criteria:

- Script exits with code `0`.
- Script prints `PHARMA_AGENT_VERIFICATION_PASS`.

Demo note:

Use this as the safest all-in-one PharmaAgent proof for the demo video.

## Scenario 10D: PharmaAgent Real Pipeline And DB Wiring

Purpose: show the real PharmaAgent pipeline is connected to database schema, DB triggers, active medication counts, rule registry verification, and live tool status.

Demo command:

```powershell
.\venv\Scripts\python.exe verify_pharma_pipeline_real.py
```

Expected output includes:

```text
PHARMA_PIPELINE_REAL_VERIFICATION_PASS
```

Important fields to show in the terminal output:

```text
schema_ready: true
research_tables_ready: true
trigger_present: true
active_rule_count: 43
active_medications_count: 2
warfarin_plus_aspirin_max_severity: critical
live_tools.openfda: ok
live_tools.merged: ok
```

Demo steps:

1. Run the command.
2. Show `schema_ready`, `research_tables_ready`, and `trigger_present`.
3. Show active rule count and active medication count.
4. Show Warfarin + Aspirin severity is `critical`.
5. Show live tools report OpenFDA and merged lookup as OK.

Pass criteria:

- Script exits with code `0`.
- Output includes `PHARMA_PIPELINE_REAL_VERIFICATION_PASS`.
- DB schema and trigger fields are true.
- Critical interaction check is present.

## Scenario 10E: PharmaAgent Live Evidence Sources

Purpose: show PharmaAgent checks interaction evidence across local rules and live/public tools.

Demo command:

```powershell
.\venv\Scripts\python.exe verify_pharma_live_tools.py
```

Expected output includes:

```text
db_active_rules: 43
overall_confidence_score: 1.0
failing_tools: []
```

Tool behavior verified in the current environment:

- `local_drug_interactions`: OK.
- `openfda`: OK.
- `pubmed`: OK.
- `rxnav`: skipped because the RxNav interaction endpoint is disabled/discontinued in current config.

Demo steps:

1. Run the command.
2. Show Warfarin + Aspirin result.
3. Show Amlodipine + Simvastatin result.
4. Show OpenFDA and PubMed statuses.
5. Explain RxNav is intentionally skipped, not failing.

Pass criteria:

- Script exits with code `0`.
- `failing_tools` is empty.
- `overall_confidence_score` is `1.0`.

Demo note:

This scenario depends on network/API availability. If recording must be fully offline, use Scenario 10B or 10C instead.

## Scenario 10F: PharmaAgent Known Side-Effect Hint

Purpose: show PharmaAgent can answer a known medication side-effect question from deterministic hints without waiting for external research.

Demo command:

```powershell
.\venv\Scripts\python.exe -c "from ingestion import process_side_effect_lookup; r=process_side_effect_lookup('d0000001-0002-0001-0001-000000000001','Amlodipine','dizziness','Dad is feeling dizzy after taking Amlodipine'); print(r['source']); print(r['reply']); print(r['confidence'])"
```

Expected output:

```text
known_hint
Amlodipine can cause dizziness, especially when standing up. Sit before standing.
0.95
```

Demo steps:

1. Run the command in terminal.
2. Show that the source is `known_hint`.
3. Show the caregiver-safe reply.
4. Explain that this is deterministic and fast, not an LLM hallucination.

Pass criteria:

- Source is `known_hint`.
- Reply mentions Amlodipine and dizziness.
- Confidence is high.

## Scenario 11: Document Pipeline Components

Purpose: show parser/classifier/prompt/validator orchestration is wired.

Demo command:

```powershell
.\venv\Scripts\python.exe verify_document_pipeline.py
```

Expected output:

```text
ALL VERIFICATION TESTS PASSED
```

Verified components:

- Extractor initializes.
- Document classifier routes prescription, discharge summary, and medical history.
- Context manager preserves critical medication lines.
- Eight document prompt schemas are available.
- Validator catches hallucinated/out-of-range lab values.
- Async pipeline orchestrator initializes.

Pass criteria:

- Script exits with code `0`.
- It prints `ALL VERIFICATION TESTS PASSED`.

Demo note:

This is a component-level document pipeline demo, not a guaranteed full upload-to-DB prescription demo.

## Scenario 12: Local Media Upload Bridge

Purpose: show the browser upload bridge can accept local files and serve them back to the app.

Verified test:

```text
POST /api/upload-media -> 200 {"status":"ok", "media_url": "...", "size": 295358}
GET /media/<stored_file> -> 200 image/jpeg
```

Demo steps:

1. In the browser UI, select `tests/fixtures/test_prescription.jpg`.
2. Upload it.
3. Show the immediate upload response or chat acknowledgement.

Verification command:

```powershell
.\venv\Scripts\python.exe -c "from fastapi.testclient import TestClient; from pathlib import Path; import base64; from main import app; c=TestClient(app); p=Path('tests/fixtures/test_prescription.jpg'); r=c.post('/api/upload-media', json={'filename':p.name,'media_type':'image/jpeg','data_base64':base64.b64encode(p.read_bytes()).decode('ascii')}); print(r.status_code, r.json()['status'], r.json()['size'])"
```

Pass criteria:

- HTTP status is `200`.
- JSON status is `ok`.
- Size is greater than zero.

Demo note:

Use this to demonstrate upload acceptance. Use Scenario 11 to demonstrate parser pipeline readiness.
