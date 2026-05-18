# CareCircle Demo Video Script

Date prepared: 2026-05-18

This script is written for a detailed demo video. It explains what the viewer is seeing, why the UI is designed this way, which workflows are being demonstrated, and how CareCircle can later connect to WhatsApp even though the demo UI shows built-in profiles for convenience.

Recommended demo runtime:

```powershell
cd C:\Users\DELL\Documents\100x-Project
.\venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Open:

```text
http://127.0.0.1:8000/
```

Use demo caregiver:

```text
+919876543211
```

## 1. Opening Narration

Screen:

Show the CareCircle browser UI.

Presenter script:

```text
This is CareCircle, a caregiver coordination and medical safety assistant.

The goal of CareCircle is to help families manage elderly or chronic-care patients through one simple chat-first interface. A caregiver can ask about medicines, appointments, symptoms, emergencies, uploaded prescriptions, lab reports, and medication safety.

Behind this simple UI, the system has several layers: intent routing, database-backed patient memory, medical document ingestion, medication validation, emergency-card generation, daily caregiver briefings, and PharmaAgent for medication interaction safety.
```

Key point to say:

```text
The UI is intentionally simple. It is not meant to look like a hospital dashboard first. It is meant to feel like a caregiver conversation, because real caregivers usually ask questions in natural language.
```

## 2. Explain The Demo UI And Profiles

Screen:

Show the visible demo profile selector or profile area in the UI.

Presenter script:

```text
For this demo, we show caregiver profiles directly in the UI. This is mainly for convenience during testing and video recording.

In a real deployment, the same identity mapping can come from WhatsApp. For example, when a message arrives from a caregiver phone number, CareCircle can identify the linked profile, patient, caregiver role, and permissions automatically.

So the UI profile selector is not a replacement for WhatsApp. It is a demo-friendly way to simulate the same profile identification that would normally happen from a WhatsApp sender number.
```

Explain the represented UI:

```text
The UI represents a caregiver chat console. The left or profile portion represents who is speaking to CareCircle. The main chat area represents the caregiver conversation. System replies include confidence, intent, and task IDs where relevant so that we can debug and verify how the system understood the message.

In production, this chat interface can be connected to WhatsApp, SMS, or another caregiver communication channel. The backend logic remains the same: a message comes in, the profile is identified, the intent is classified, the correct workflow runs, and the response goes back to the caregiver.
```

What to highlight:

```text
Profiles are shown in the UI for demo convenience.
Phone numbers can map to caregiver records.
The same backend can support WhatsApp integration.
The chat UI is a test and demonstration surface for real caregiver workflows.
```

## 3. Architecture Overview

Screen:

Open the README architecture section or show:

```text
docs/carecircle-architecture.jpeg
```

Presenter script:

```text
This architecture diagram shows the full CareCircle flow.

Incoming messages first go through profile identification. Then the system checks whether the message is a veto or approval command, a media upload, a crisis message, an appointment request, a medication question, or a general caregiver conversation.

For media, CareCircle detects whether the upload is an image, PDF, or audio file. Images go through OCR, audio goes through speech-to-text, and PDFs go through text extraction or OCR fallback. The extracted raw text is then converted into structured medical data and stored with validation metadata.

For chat, the message goes through deterministic and embedding-assisted intent routing. If the message is a crisis, the system goes to emergency mode. If it is an appointment request, it uses the appointment workflow. If it is about medicines, it uses medication and PharmaAgent workflows.
```

Key point:

```text
CareCircle is not just a chatbot. It is a workflow system with medical memory, safety checks, and caregiver-facing outputs.
```

## 4. Health Check And System Readiness

Screen:

Open:

```text
http://127.0.0.1:8000/health/system
```

Presenter script:

```text
Before demonstrating features, I am checking the system health endpoint.

This tells us whether important guardrails are enabled, whether intent locking is active, whether crisis fast-path handling is enabled, whether notification dispatch is enabled, and whether the database schema is reachable.
```

Expected output to point at:

```text
guardrails_enabled: true
intent_locking_enabled: true
crisis_fast_path_enabled: true
notification_dispatch_enabled: true
db.status: ok
```

Then open:

```text
http://127.0.0.1:8000/health/pharma
```

Presenter script:

```text
This second health endpoint checks PharmaAgent readiness. PharmaAgent is the medication safety layer. It loads interaction rules, checks whether self-learning is enabled, checks database rule availability, and reports model gateway status.
```

Expected output to point at:

```text
pharma_agent_status
rules_loaded
db_rules_loaded
self_learning_enabled
daily_summary_enabled
```

## 5. Greeting And Caregiver Help

Screen:

Use the chat UI.

Type:

```text
Hi
```

Presenter script:

```text
Now I will start with a simple greeting. The system identifies the caregiver profile and gives a helpful menu of what can be asked.
```

Expected reply:

```text
Hello Meera Sharma! CareCircle is here to help. You can ask: 'What meds are active?', 'How is the patient?', or send BP/sugar readings.
```

Explain:

```text
This confirms that the chat layer is connected to profile context. In WhatsApp, this same mapping would happen through the caregiver phone number.
```

## 6. Active Medication List

Type:

```text
what medicines am I on
```

Presenter script:

```text
Now I am asking for active medicines. The important word here is active. CareCircle should only show medications that are active in the patient record.

If a newly uploaded medicine is suspicious, pending review, or waiting for veto approval, it should not appear here as a normal active medicine.
```

Expected reply:

```text
Active medications:
1. atorvastatin 40.0mg HS
2. telmisartan 40.0mg OD
```

Explain:

```text
This data is coming from the database. It is not a hardcoded UI list. This is important because medication safety depends on knowing what the patient is actually taking.
```

## 7. Informal Medication Question

Type:

```text
what should I take now
```

Presenter script:

```text
Caregivers do not always use exact commands. Here I ask an informal question: what should I take now?

The system routes it safely to the medication workflow and shows active medications.
```

Expected reply:

```text
Active medications:
1. atorvastatin 40.0mg HS
2. telmisartan 40.0mg OD
```

Explain:

```text
This demonstrates natural-language routing, but the answer still comes from structured medication records.
```

## 8. Appointment Query

Type:

```text
when is the next general checkup for uncle
```

Presenter script:

```text
CareCircle also handles care coordination, not only medicines. Here I am asking for the next general checkup.

The system understands that this is an appointment query and filters the database by the appointment context instead of returning every unrelated appointment.
```

Expected reply:

```text
Upcoming appointments:
1. 19 May 2026, 11:09 PM IST - Dr. Rajan Mehta at Apollo Hospital, Bangalore. Notes: Annual physical exam, fasting required
```

Explain:

```text
The appointment system supports natural caregiver phrasing. It can understand terms like checkup, doctor visit, cardiology, lab test, and similar appointment-related wording.
```

## 9. Appointment Creation With Multi-Turn Context

Type:

```text
create cardiology appointment on 29 June 2026
```

Expected reply:

```text
I can add this appointment, but I need time. Please send the appointment time.
```

Presenter script:

```text
This is a multi-turn workflow. I gave the appointment date and department, but I did not give the time. CareCircle does not guess. It asks for the missing field.
```

Type:

```text
2:00 PM
```

Expected reply:

```text
Appointment saved: 29 Jun 2026, 02:00 PM IST with cardiology at location not recorded. Reply YES to confirm or NO to cancel.
```

Presenter script:

```text
Now the system remembers that the previous context was appointment creation, so it understands that 2:00 PM is the appointment time.
```

Type:

```text
YES
```

Expected reply:

```text
Appointment confirmed. I will include it in reminders and daily briefs.
```

Presenter script:

```text
This confirms the appointment. The key feature is context memory. A short reply like YES is interpreted based on the active pending workflow, not as a random unrelated message.
```

## 10. Emergency Crisis Card

Type:

```text
heart attack
```

Presenter script:

```text
Now I will demonstrate the emergency pathway. If the caregiver sends a crisis message, CareCircle should not behave like a normal chatbot. It should immediately switch to emergency mode.
```

Expected reply starts with:

```text
EMERGENCY CARD - Rajesh Sharma
```

Show these areas:

```text
Current medicines
Emergency contacts
Doctor contact
Caregiver alerts sent
Location or map context if available
```

Presenter script:

```text
The emergency card gives quick information that a caregiver or responder may need immediately: current medicines, patient identity, emergency contacts, doctor contacts, and alert status.

This is designed for speed. In a crisis, we do not want a long uncertain answer. We want a compact emergency packet.
```

## 11. Daily Caregiver Briefing

Screen:

Show terminal.

Run:

```powershell
.\venv\Scripts\python.exe -c "import daily_summary, db; pid=db.get_active_patient_ids()[0]; ctx=daily_summary.build_brief_context(pid); print(daily_summary.format_day_brief(ctx)); print(); print(daily_summary.format_night_summary(ctx))"
```

Presenter script:

```text
CareCircle can also prepare daily caregiver briefings.

The morning brief is meant for planning the day. It includes yesterday's medication confirmation status, upcoming doctor appointments, lab or test appointments, pending approvals, medication review items, caregiver visits, and alerts.

The night summary is shorter. It gives the primary caregiver a quick review of today's medication state, open alerts, pending approvals, and what is coming next.
```

Expected sections:

```text
10AM CareCircle day brief
Yesterday meds
Doctor
Tests
Pending approvals
Medication review
Alerts
```

Expected night sections:

```text
10PM quick summary
Today meds
Open alerts
Pending approvals
Next
```

Explain:

```text
These summaries are generated from database state. If there are no appointments or no tests, the system should say that clearly instead of failing or inventing data.
```

## 12. Document Pipeline Overview

Screen:

Show terminal.

Run:

```powershell
.\venv\Scripts\python.exe verify_document_pipeline.py
```

Presenter script:

```text
CareCircle includes a medical document pipeline. It can process prescription images, PDFs, lab reports, voice notes, advice notes, discharge summaries, referral letters, and medical history notes.

The pipeline has multiple stages: extraction, document type classification, context preservation, prompt selection, structured JSON extraction, validation, and database insertion.
```

Expected output:

```text
ALL VERIFICATION TESTS PASSED
```

Important explanation:

```text
The LLM is not treated as final truth. It can create a draft structured JSON, but deterministic validation decides whether the data is safe enough to use.

For medications, the system checks drug name, dose amount, dose unit, and frequency. If anything is unresolved or suspicious, the medicine should not become an active medication automatically.
```

## 13. Media Upload Bridge

Screen:

Use UI upload or terminal.

Terminal verification:

```powershell
.\venv\Scripts\python.exe -c "from fastapi.testclient import TestClient; from pathlib import Path; import base64; from main import app; c=TestClient(app); p=Path('tests/fixtures/test_prescription.jpg'); r=c.post('/api/upload-media', json={'filename':p.name,'media_type':'image/jpeg','data_base64':base64.b64encode(p.read_bytes()).decode('ascii')}); print(r.status_code, r.json()['status'], r.json()['size'])"
```

Presenter script:

```text
This verifies that the app can accept uploaded media and store it through the upload bridge.

In the full workflow, uploaded prescriptions or lab reports are parsed, raw text is stored, structured JSON is created, and validated data is written into the correct medical tables.
```

Expected output:

```text
200 ok <size greater than 0>
```

## 14. WhatsApp Integration Explanation

Screen:

Return to UI profile section.

Presenter script:

```text
In this demo, we use the browser UI and visible profiles because it makes testing and recording easier.

But the product is designed so that WhatsApp can be connected as the real caregiver channel. In that setup, the incoming WhatsApp phone number becomes the identity key.

For example, if Meera sends a WhatsApp message, the backend can map her phone number to her caregiver profile, patient relationship, permissions, and linked patient record. Then the same intent router, appointment manager, emergency workflow, document pipeline, and PharmaAgent safety checks can run behind the scenes.
```

Explain clearly:

```text
The UI is a demonstration and operations console.
WhatsApp can become the real-world caregiver input channel.
The backend workflows are channel-independent.
```

## 15. Deployment Explanation

Screen:

Open:

```text
https://carecircle-amber.vercel.app
```

Presenter script:

```text
CareCircle is also deployed to Vercel, so the web interface can be accessed remotely.

The production deployment is serverless-safe. Core FastAPI routes and database-backed health checks work on Vercel. Heavy local workloads like OCR, ASR, and embeddings are better suited to a long-running worker or server because those tasks need more runtime and local dependencies.
```

Show:

```text
https://carecircle-amber.vercel.app/health/system
https://carecircle-amber.vercel.app/health/pharma
```

Presenter script:

```text
This separation is intentional. The web app can be deployed on Vercel, while heavy medical processing can run on a more suitable backend worker environment.
```

## 16. PharmaAgent Final Section

Screen:

Show `/health/pharma` again or the Pharma dashboard.

Presenter script:

```text
Now I will end with PharmaAgent, which is the medication safety layer.

PharmaAgent is responsible for checking whether a new medication is safe with the patient's current active medications and context. It is designed around a safety-first principle: a new medication should not become active automatically if validation is incomplete or if an interaction is found.
```

Explain the workflow:

```text
When a prescription is uploaded, the system extracts candidate medication data. That candidate is only a draft.

Next, deterministic validation checks the drug name, dose amount, dose unit, and frequency. If the core fields are unresolved, the record stays suspicious or pending review.

If validation passes, PharmaAgent compares the candidate drug against currently active medications one at a time.

If no interaction is found, the medication can be promoted to active.

If an interaction is found, the system creates an alert, notifies the primary caregiver, and moves the case into an approval or veto-required flow.
```

Run:

```powershell
.\venv\Scripts\python.exe verify_pharma_agent.py
```

Expected:

```text
PHARMA_AGENT_VERIFICATION_PASS
```

Presenter script:

```text
This verification confirms that PharmaAgent rules load correctly, known critical interactions are detected, side-effect hints work, duplicate decisions are skipped, and the approval path is available.
```

Run critical interaction example:

```powershell
.\venv\Scripts\python.exe -c "from pharma_agent import PharmaSafetyEngine; e=PharmaSafetyEngine(); r=e.evaluate('d0000001-0002-0001-0001-000000000001','Warfarin',{'active_meds':[{'drug_name':'Aspirin'}],'conditions':[],'renal_markers':None}); print('severity:', r['max_severity']); print('interactions:', len(r['interactions'])); print(r['interactions'][0]['message'])"
```

Expected:

```text
severity: critical
interactions: 1
Warfarin with aspirin can greatly increase bleeding risk. Contact the doctor before combining.
```

Presenter script:

```text
This is the main safety value. If a patient is already taking Aspirin and a new Warfarin prescription is added, PharmaAgent identifies the bleeding-risk interaction as critical.

In a real caregiver workflow, this should trigger an alert to the primary caregiver and prevent unsafe automatic activation until the case is reviewed or approved.
```

Run live tools check if internet is stable:

```powershell
.\venv\Scripts\python.exe verify_pharma_live_tools.py
```

Presenter script:

```text
PharmaAgent can use multiple evidence sources. It checks local interaction rules and can use live sources such as OpenFDA and PubMed where available.

The system does not depend on only one tool. If a tool is unavailable, the result should be reported honestly instead of silently pretending everything worked.
```

Expected:

```text
overall_confidence_score: 1.0
failing_tools: []
```

If live APIs are slow:

```text
This part depends on external APIs, so for recording reliability we can use the deterministic PharmaAgent verification as the primary proof.
```

## 17. Final Closing

Screen:

Show the chat UI, then architecture image, then PharmaAgent health.

Presenter script:

```text
To summarize, CareCircle is a caregiver-first medical coordination system.

The UI gives caregivers a simple chat experience. The backend adds structured safety: profile mapping, database memory, appointment handling, emergency cards, daily briefings, document parsing, validation, and PharmaAgent medication safety.

The profiles shown in the UI are for demo convenience, but the same system can connect to WhatsApp by mapping incoming caregiver phone numbers to profiles.

The most important design principle is that CareCircle does not blindly trust LLM output. LLMs can help with extraction and explanation, but deterministic validation, database state, alerts, approvals, and caregiver review control what becomes active.

CareCircle is built to make caregiving safer, faster, and more organized.
```

Final line:

```text
CareCircle: safer caregiving through structured memory, medication safety, and fast emergency context.
```

## 18. Demo Recording Checklist

Before recording:

```text
Server is running locally.
Browser UI opens correctly.
Demo profile/phone is available.
Database is reachable.
/health/system returns 200.
/health/pharma returns 200.
verify_pharma_agent.py passes.
verify_appointment_workflow.py passes.
```

Recommended order:

```text
1. Opening and UI explanation
2. Profiles and WhatsApp explanation
3. Architecture overview
4. Health checks
5. Greeting
6. Medication list
7. Appointment query
8. Appointment creation
9. Emergency card
10. Daily brief
11. Document pipeline verification
12. Deployment explanation
13. PharmaAgent final section
14. Closing
```

Avoid relying on these as the only proof:

```text
Full OCR upload in a live recording, because OCR quality depends on the sample image.
Live LLM explanation if model/API keys are not confirmed immediately before recording.
RxNav as a required tool, because its interaction endpoint may be unavailable or skipped.
Vercel for heavy OCR/ASR processing, because serverless runtime is not ideal for long-running parser workloads.
```
