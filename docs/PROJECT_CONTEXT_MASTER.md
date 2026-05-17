# PROJECT_CONTEXT_MASTER

## What This Project Is

CareCircle is a care coordination and health context system for an adult child managing an elderly parent's health remotely across fragmented providers, formats, and people. The central user is Meera, a working professional in Bangalore trying to coordinate her father's care in Lucknow across multiple doctors, hospital apps, prescriptions, lab reports, caregiver updates, and urgent incidents.

The project is not just a record viewer. Its purpose is to:

- unify fragmented health information into one patient-specific memory,
- detect meaningful risks such as drug interactions or care gaps,
- help the family coordinate the next right action,
- respond differently in routine, planning, and crisis situations,
- communicate clearly to a non-clinical family caregiver under low time and emotional bandwidth.

The source documents frame this as a missing care ecosystem, not a broken existing one. The product exists to reduce real-world harm caused by disconnected records, missing follow-through, conflicting updates, outdated prescriptions, and slow crisis retrieval.

## Core Problem the System Must Solve

The problem statement is explicit: existing solutions either force Meera to be the human integration layer or operate on only one fragment of the picture.

Key real-world failures the system must address:

- prescriptions, lab reports, caregiver notes, and doctor instructions live in different places and formats,
- multiple doctors do not coordinate with each other,
- medication conflicts can be missed across specialties,
- important follow-up work such as blood tests or refills can go unscheduled,
- the caregiver, patient, and documents may all describe the same event differently,
- a 2 AM emergency requires a different system behavior than a normal question,
- silence or missing updates are meaningful in this domain.

The project repeatedly emphasizes that the right standard is not feature richness; it is whether each part of the system exists because removing it would reintroduce a real risk.

## Product Promise and Non-Goals

What CareCircle is trying to be:

- the most prepared family member in the room,
- a system that connects dots across time, sources, and participants,
- a coordination layer that recommends actions when gaps appear,
- a briefing system that answers: Is Dad okay? Is anything urgent? What do I need to do?

What it is explicitly not trying to be:

- a physician,
- a generic dashboard full of charts,
- a system that autonomously takes irreversible actions without Meera's awareness,
- a free-form search engine over raw documents,
- a workflow where every decision is delegated to an LLM.

## First-Principles Product Logic

The architecture document establishes several non-negotiable principles:

- No single parser can handle all health inputs well. The ingestion layer must be modular by modality.
- Raw inputs are not the long-term system memory. They must be converted into structured records/events.
- The system must track state, not just recency. Information can be active, superseded, or unresolved.
- Conflicts should be represented, not silently resolved.
- Confidence decays over time and should influence both communication and task generation.
- The system crosses from retrieval into coordination only on explicit trigger conditions.
- Crisis mode must be separate from routine mode.
- Uncertainty should be communicated as an actionable data gap, not a probability score.

## End-to-End Operating Model

From the PDFs and architecture visualization, the intended operating flow is:

1. A message or document arrives through WhatsApp.
2. The sender is identified from a known profile and care-team relationship.
3. The input is classified into a mode or intent.
4. Deterministic flows handle routine structured cases immediately.
5. Media or reasoning-heavy inputs are dispatched asynchronously.
6. Parsed information is normalized into a unified internal schema.
7. Records are assigned state such as active, superseded, or unresolved.
8. Patient memory, knowledge sources, and triggers generate alerts, tasks, or updates.
9. Daily briefings and emergency packets are served from pre-assembled or cached data.

The visual architecture also shows audit logging as a cross-cutting concern around state changes.

## User and Stakeholder Model

Known actors from the source files:

- Meera: primary remote family caregiver and primary decision-maker.
- Father/patient: elderly patient, diabetic and hypertensive, living alone, history of cardiac episode.
- Part-time caregiver: sends updates, likely handles adherence confirmations and observational inputs.
- Multiple doctors: cardiologist, endocrinologist, general physician; potentially more in future.
- System agents/components: deterministic handlers, async ingestion, alerting, briefing, crisis packet, and optional PharmAgent research loop.

The architecture document strongly prefers a responsibility matrix: each task has a primary owner plus escalation chain.

## Input Types and Ingestion Requirements

The documented input types are:

- prescription photos,
- lab report PDFs,
- caregiver voice notes,
- doctor instructions relayed by phone,
- handwritten notes,
- plain text chat messages,
- location shares,
- structured YES/NO confirmations,
- possibly future specialty documents such as MRI or cognitive assessment records.

The first-principles architecture recommends four specialized parser families:

- vision-based document parser for photos and report-like documents,
- multilingual ASR plus NLP for Hindi-English code-switched voice notes,
- structured extractor for digital PDFs,
- human-relay parser for phone-reported updates.

The build guidance narrows the MVP implementation path into concrete media stacks:

- image prescriptions: Google Cloud Vision, then Claude Haiku extraction,
- PDFs: text extraction plus regex and Claude Haiku for unclear fields,
- audio: Whisper base locally, then Claude Haiku extraction.

Important ingestion rules:

- the webhook must not block on OCR, ASR, or LLM calls,
- blurry or low-confidence inputs should trigger clarification rather than silent failure or guessing,
- the system should treat Hindi-English code-switching as normal, not exceptional,
- the stored system memory should be structured events/records rather than raw transcripts as the primary query surface.

## Unified Data Model and Record Semantics

Across the architecture PDF and SQL schema, the conceptual internal model includes:

- patient-specific structured memory,
- typed records/events instead of raw documents,
- source metadata,
- confidence,
- freshness/half-life,
- status or lineage,
- triggerability by downstream logic.

The architecture PDF explicitly proposes a generic record shape with ideas like:

- `data_category`,
- `source_type`,
- `source_doctor`,
- `timestamp`,
- `structured_fields`,
- `confidence`,
- `freshness_halflife`,
- `status` as `Active`, `Superseded`, or `Unresolved`.

The SQL schema does not implement one universal polymorphic table. Instead, it uses normalized domain tables such as `medications`, `medication_log`, `lab_reports`, `patient_conditions`, `alerts`, `pending_tasks`, `agent_runs`, and `agent_approvals`. Future implementation work should remember that the conceptual model is unified, but the persisted model is specialized.

## Record State, Versioning, and Freshness

This is a major architectural theme.

### State model

The architecture document requires every structured fact to conceptually be in one of three states:

- Active,
- Superseded,
- Unresolved.

In the SQL schema, this is most visible in the `medications` table:

- `status` supports `active`, `discontinued`, `pending_confirmation`, `discarded`,
- `superseded_by_id` supports lineage.

This is not a perfect one-to-one mapping with the architecture document's state vocabulary. It is an implementation variant, not a full conceptual match.

### Freshness and confidence decay

The architecture PDF defines category-specific half-lives:

- cardiac specialist data: 6 weeks,
- medications: 4 weeks,
- lab reports: 8 weeks,
- caregiver stream: expected frequency based rather than static age alone.

The build guidance encodes a concrete `HALF_LIFE_DAYS` configuration:

- cardiac: 42,
- endocrinology: 42,
- general: 28,
- medications: 28,
- lab_reports: 56,
- caregiver: 3.

Freshness is important for:

- how confidently the system speaks,
- whether information is treated as current,
- whether proactive follow-up should be suggested,
- whether crisis data is stale,
- whether alerting should be downgraded or escalated.

## Modes of Operation

The source documents clearly divide the system into different operational modes.

### Routine mode

Used for straightforward retrieval or structured updates such as:

- "What medicines is Dad on?"
- medication confirmations,
- help/greeting,
- document upload acknowledgement.

This path should be deterministic where possible.

### Planning mode

Used when the system identifies future gaps that require coordination, for example:

- appointment approaching without prerequisite lab work,
- medication stock running low,
- specialist follow-up overdue,
- caregiver schedule conflict,
- no first-dose confirmation after a new prescription.

This is where the system stops being only reactive and becomes a coordination system.

### Crisis mode

Crisis mode is architecturally distinct and must be fast, reliable, and LLM-independent on the user-facing path. Trigger examples include:

- chest pain,
- breathing difficulty,
- unconsciousness,
- collapse,
- seizure,
- equivalent Hindi-English crisis phrases,
- present-tense temporal markers such as "abhi", "right now", "just now".

The system must distinguish live crisis from retrospective mention.

## Routine vs Deterministic vs LLM Boundary

The build guidance is unambiguous: most operational errors in the previous attempts came from routing deterministic work through LLMs.

### Deterministic zone: never use an LLM

Examples explicitly called out:

- sender identification by phone number,
- `VETO` / `APPROVE` command detection,
- structured YES/NO medication confirmations,
- active medication lookups,
- first-pass crisis keyword detection,
- freshness score math,
- duplicate detection,
- medication log writes,
- appointment reminder threshold checks,
- emergency packet assembly,
- confidence threshold comparisons.

### Intelligence zone: use an LLM

Documented LLM-worthy tasks:

- prescription extraction from OCR text,
- voice-note event extraction after transcription,
- plain-language drug interaction explanation,
- daily briefing synthesis,
- conflict clarification question generation,
- PharmAgent multi-source research synthesis,
- open-ended conversational replies.

The biggest design consequence is that user-facing WhatsApp responses should usually come from deterministic logic or precomputed cache, while many LLM tasks run in background jobs.

## Communication Philosophy

All three core documents agree on the style of output:

- plain language,
- low jargon,
- specific actionability,
- minimal cognitive burden,
- no charts in the primary briefing,
- no probabilistic confidence numbers shown directly,
- no overclaiming of certainty.

### Daily briefing format

The architecture PDF says the briefing should have exactly three sections:

- Status,
- Alerts,
- For Later.

The build guidance's JSON contract for briefing generation matches that:

- `status`: `green | yellow | red`,
- `status_line`,
- `alerts` with max 3 items,
- `for_later` with max 5 items.

The briefing should answer:

- Is Dad okay?
- Is anything urgent?
- What do I need to do?

Morning and evening briefings differ in detail level, with evening being intentionally shorter.

### Drug interaction communication

The architecture PDF requires a fixed three-part structure:

- what it means in plain language,
- what to watch for,
- what to do.

The build guidance turns that into a machine contract:

- `severity`,
- `plain_language`,
- `watch_for`,
- `action`.

A key rule is repeated: do not use drug names in the headline/plain-language summary; use understandable categories instead.

### Uncertainty communication

Uncertainty should be framed as:

- a data gap,
- a conflict,
- or a small next question/action.

It should not be framed as abstract percentages for end users.

## Coordination, Responsibility, and Escalation

The architecture document makes a strong distinction between notification and true coordination.

Important coordination rules:

- every task should have a primary owner,
- escalation only happens after a response window expires,
- Meera should not be spammed with everything immediately,
- crisis alerts are immediate,
- non-urgent items can wait for briefing windows,
- lack of caregiver response should be framed as a data gap, not blame.

Examples from the documents:

- blood-test scheduling before follow-up: primary owner Meera, escalate after 24 hours,
- medication reminder: primary caregiver, then Dad, then Meera for information,
- crisis event: immediate Meera plus simultaneous caregiver notification.

The architecture also insists the system should recommend actions, not take hidden actions autonomously.

## Conflict Handling

One explicit principle: the system should not choose a winner when sources disagree.

Instead it should:

- store each source with provenance,
- create conflict records when accounts disagree,
- surface unresolved conflict with minimal useful context,
- ask targeted clarification questions only when a specific missing answer would resolve ambiguity.

The architecture visualization includes conflict handling inside medical reasoning and trigger processing, with conflict records flowing into Meera's view.

## Silence as Data

Silence is treated as meaningful.

If the caregiver usually sends updates and then goes silent:

- confidence in caregiver-dependent inferences should drop,
- the system may gently check in with the caregiver first,
- unresolved silence should later surface to Meera,
- downstream adherence or monitoring conclusions should be marked unverified.

The build guidance operationalizes this with a proactive check:

- caregiver last update older than 1.5x expected interval triggers a check-in.

## Crisis Handling and Emergency Packet

The crisis flow is the most reliability-sensitive path in the project.

The build guidance defines a three-layer redundancy model:

1. `crisis_cache` precomputed JSON,
2. DB rebuild if cache is stale,
3. minimal fallback using active medications only.

The emergency packet should include:

- current medications,
- last cardiac report summary,
- emergency contacts / nearby hospitals,
- cardiologist emergency number,
- last updated timestamp.

The architecture PDF adds an important product expectation: the packet should be readable in under 60 seconds and optimized for emergency use rather than conversational exploration.

## Proactive System Behavior

The product is intentionally proactive, but only when justified by strong signals.

Documented proactive conditions include:

- appointment coming up without recent prerequisite lab,
- medication about to run out,
- caregiver silence,
- stale specialist data,
- critical alert still unacknowledged,
- new prescription without first-dose confirmation,
- missed time-sensitive medication confirmation by a deadline,
- low-confidence prescription quality requiring a clearer upload.

The architecture and build guidance both stress that proactive mistakes can destroy trust. Therefore, proactive behavior should require:

- real gap, not ambiguous inference,
- meaningful health consequence,
- clear owner,
- enough confidence in the underlying data.

## Knowledge Sources and Medical Reasoning

The conceptual architecture proposes a three-layer reasoning stack:

- patient-specific memory,
- curated medical knowledge base,
- LLM reasoning layer that combines patient state with retrieved knowledge.

The build guidance translates this into a practical MVP stack:

- OpenFDA Drug API for interaction data,
- RxNav for drug normalization,
- Claude Haiku for plain-language summaries,
- Claude Sonnet only for deeper PharmAgent synthesis,
- optional vector-style medical knowledge appears in the architecture vision but is not concretely specified in the build plan.

This means the intended long-term reasoning model is richer than the initial MVP implementation plan.

## Database Understanding

The database material consists of:

- `Context/Database/Supabase.sql`: a schema definition intended as the main source,
- `Context/Database/Supabase Snippet List Public Table Columns.csv`: a table/column snapshot,
- architecture/build documents that assume additional tables beyond the base SQL.

### Main schema entities from `Supabase.sql`

Identity and people:

- `profiles`: person records with name, phone, DOB, sex.
- `patients`: one patient profile plus diagnosis/notes.
- `care_team`: joins profiles to patients with roles such as primary caregiver, secondary caregiver, doctor, patient.

Medication domain:

- `medications`: current and historical medication records, schedule, confidence, raw source text, supersession link.
- `medication_log`: taken/missed/skipped events with source and date.

Clinical data:

- `lab_reports`: one row per test result.
- `patient_conditions`: condition list per patient.

Operational state:

- `crisis_cache`: precomputed emergency packet cache.
- `alerts`: surfaced issues with severity, status, payload.
- `audit_log`: state-change history.
- `pending_tasks`: async work queue.

Agent / advanced reasoning layer:

- `agent_runs`,
- `agent_approvals`,
- `agent_briefs`,
- `pharmagent_feedback`.

Knowledge/reference tables:

- `drug_interactions`,
- `symptom_map`,
- `herb_interactions`,
- `schema_version`.

### Database themes visible in the schema

- UUID primary keys everywhere.
- Postgres-specific features such as `JSONB`, arrays, and `TIMESTAMPTZ`.
- explicit foreign keys and `ON DELETE CASCADE` in core relationships.
- a mix of normalized relational storage and semi-structured JSON payloads.
- support for both deterministic operations and agent-style review workflows.

### Important database-level product capabilities implied by schema

- medication versioning and supersession,
- structured adherence/event tracking,
- per-patient alert lifecycle,
- cached emergency context,
- async task processing,
- optional human-in-the-loop approval for drug interaction updates,
- reusable agent briefs by role,
- auditable state changes.

### Database inconsistencies and likely drift

The CSV snapshot does not fully match `Supabase.sql`. This is important and should be treated as real schema drift rather than ignored.

Examples:

- CSV includes `lab_reports.profile_id`; SQL does not define that column.
- CSV includes `alerts.profile_id`; SQL does not define that column.
- CSV includes `patients.latitude`, `patients.longitude`, and `patients.hospital_preference`; SQL does not define them.
- SQL defines many columns not obvious from the CSV slice because the CSV is incomplete and selective.

This suggests the CSV was captured from a different or later database state than the SQL file.

### Tables expected by the build plan but missing from the provided SQL

The build guidance requires or mentions several structures not present in `Supabase.sql`:

- `incoming_messages`,
- `briefing_cache`,
- `pending_context` or `pending_response` style state for structured prompt replies,
- possibly an appointments table, though the guidance sometimes falls back to using `patients.notes`.

This means the SQL file is not sufficient by itself for the full staged build described in the guidance PDF.

### Notes on advanced agent tables

The schema includes a more ambitious agentic subsystem than the early-stage build plan depends on:

- `agent_runs` with modes `research`, `symptom`, `audit`,
- `agent_approvals` with veto window logic and auto-approval flow,
- `agent_briefs`,
- `pharmagent_feedback`.

The architecture visualization reinforces this with a PharmAgent research loop, safety gates, veto window, draft-to-production update path, and human review queue. This appears to represent a more advanced MVP2/MVP3 direction rather than the minimal Day 1 to Day 3 rebuild path.

## Recommended Application Structure from the Build Guidance

The build guidance wants a very controlled code layout:

- `main.py`: FastAPI app and routes only,
- `db.py`: all database access in one place, raw SQL only,
- `intent.py`: deterministic intent classification,
- `crisis.py`: crisis detection and emergency packet handling,
- `ingestion.py`: modality parsers,
- `alerts.py`: alert generation and severity classification,
- `briefing.py`: daily briefing logic,
- `llm.py`: all LLM calls,
- `config.py`: constants and thresholds,
- `.env`: secrets.

The deeper rule is more important than the exact filenames:

- one way to talk to the DB,
- explicit SQL,
- clear function contracts,
- stage-by-stage verification,
- no architectural improvisation by the coding agent.

## Stage-by-Stage Build Intent

The build guidance proposes a staged rebuild with verification gates:

0. environment and schema,
1. WhatsApp webhook logging,
2. profile identification,
3. deterministic intent classification,
4. core deterministic handlers,
5. crisis mode with cache,
6. async media ingestion,
7. drug interaction checks,
8. daily briefing generation,
9. proactive scan plus structured caregiver prompts.

The sequencing matters because the documents explicitly blame prior failures on building too far ahead of the stable entry path.

## Reliability and Operational Constraints

The most repeated operational constraint is:

- the WhatsApp webhook must return in under 5 seconds or Twilio may retry and create duplicates.

Other critical operational rules:

- all expensive work must be async,
- every DB write should either return an inserted ID or raise,
- every stage must have a verification gate,
- every function should have a clear input/output/error contract,
- do not switch models or architectures mid-build,
- do not use an ORM,
- do not silently swallow write failures,
- validate LLM JSON output before DB writes,
- normalize drug names before comparing medication records if possible.

## External Services and Tooling Assumptions

The build guidance recommends this practical stack:

- Twilio WhatsApp Sandbox for inbound/outbound WhatsApp,
- Supabase Postgres for storage,
- FastAPI for the application server,
- Railway or Render for deployment,
- ngrok for local webhook testing,
- Claude Haiku for most LLM tasks,
- Claude Sonnet for deeper PharmAgent synthesis,
- Whisper base locally for audio transcription,
- Google Cloud Vision for prescription OCR,
- OpenFDA and RxNav for drug interaction/reference normalization.

This is presented as a cost-conscious but reliable MVP path.

## Risks and Failure Patterns the Project Already Knows About

The build guidance is useful because it records known failure modes from prior attempts. These should be treated as project memory:

- overusing LLMs for deterministic tasks,
- switching models/tools midstream and creating incompatible code,
- skipping verification between stages,
- failing to stabilize the webhook first,
- having a good schema but inconsistent application queries,
- crisis flow depending on LLM latency,
- caregiver YES/NO replies misclassified without stored prompt context,
- DB writes failing silently,
- rate limits or API failures propagating into the webhook,
- drug name mismatch causing duplicate meds and missed supersession,
- crisis mode triggered by historical rather than present-tense mentions,
- briefing context growing too large for prompt limits.

## Major Ambiguities, Conflicts, and Open Questions

These are the most important unresolved or conflicting areas across the source files.

### 1. SQL schema vs CSV snapshot

There is clear schema drift between `Supabase.sql` and the CSV column export. Future work should choose one authoritative schema source or reconcile them before implementation.

### 2. Build guidance vs base SQL

The rebuild plan requires additional tables and state objects not present in the provided SQL. The schema is therefore incomplete for the full staged plan.

### 3. High-level architecture vs practical MVP build

The first-principles architecture imagines:

- a curated medical knowledge layer,
- vector-style retrieval,
- generalized conflict records,
- extensible category-based data model,
- richer coordination logic.

The build guidance proposes a narrower MVP implementation with:

- OpenFDA and RxNav,
- explicit tables and functions,
- deterministic handlers first,
- only limited agentic behavior initially.

These are not mutually exclusive, but they operate at different abstraction levels. The project likely intends the build plan as the tactical path toward the broader architecture.

### 4. Locked file structure inconsistency

The guidance first locks a file structure, but later stages introduce files or concepts not listed there, such as `handlers.py`, `scanner.py`, and implicit pending-context storage. That means the locked structure is more a discipline principle than a fully exhaustive file manifest.

### 5. Appointment data model

Proactive scans rely on upcoming appointments, but no appointment table exists in the provided SQL. The guidance loosely references either an appointments table or `patients.notes`, which is not a settled design.

### 6. Emergency contacts storage

The crisis build step references emergency contacts inside `patients.notes` JSONB, but `patients.notes` is typed as `TEXT` in the provided SQL. This is a meaningful mismatch.

### 7. Alert severity vocabulary

The SQL schema supports `critical`, `high`, `medium`, `low`, `info`. The architecture/build docs often use `critical`, `advisory`, `informational`. This is a domain vocabulary mismatch that future implementation will need to reconcile.

### 8. State vocabulary mismatch

The architecture's conceptual states are `Active`, `Superseded`, `Unresolved`, while the SQL medication status enum is `active`, `discontinued`, `pending_confirmation`, `discarded`. These are related but not identical.

## What Should Be Treated as Non-Negotiable Going Forward

- The webhook path must stay fast and deterministic.
- LLMs should only be used where language understanding/reasoning is genuinely required.
- Crisis responses must never depend on a live LLM call.
- Structured health memory should be source-aware, confidence-aware, and freshness-aware.
- Conflicts should be preserved, not flattened away.
- The user experience should optimize for Meera's cognitive load, not data completeness on the main screen.
- Every proactive behavior must justify itself through confidence and consequence.
- Database access should remain explicit and consistent.
- Verification gates should be part of the build discipline.

## Practical Takeaway for Future Build Sessions

If this file is used as the primary project memory, the most important thing to remember is that CareCircle is a health-context orchestration system with three intertwined responsibilities:

- ingest and normalize fragmented multimodal health inputs,
- maintain a trustworthy current picture with conflict and freshness awareness,
- help a remote family caregiver act appropriately in routine, planning, and crisis contexts.

Almost every design choice in the source material follows from those three responsibilities and from one UX rule: the system must help Meera make the next good decision without overwhelming her.
