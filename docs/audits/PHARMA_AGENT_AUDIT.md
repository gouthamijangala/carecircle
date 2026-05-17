# PHARMA AGENT AUDIT REPORT
## CareCircle - Complete System Analysis

**Date:** May 12, 2026  
**Scope:** Pharma Agent Pipeline, LLM Integration, Alert System, Notification Flow  
**Scenario Tested:** Uploading 2 prescriptions from 2 different hospitals/departments with:
- Same drug (potential duplicate)
- Drugs that should NOT be combined (causing health crisis)
- Critical drug interaction detection
- Primary caregiver alerting
- Renal patient context

---

## TABLE OF CONTENTS

1. [Executive Summary](#executive-summary)
2. [Full Pipeline Trace - Prescription to Alert](#full-pipeline-trace)
3. [Critical Issues Found](#critical-issues-found)
4. [Issue #1: Alert Primary Caregiver - NOT WORKING](#issue-1-alert-primary-caregiver-not-working)
5. [Issue #2: Renal Context - HARDCODED NOT GENERAL](#issue-2-renal-context-hardcoded-not-general)
6. [Issue #3: 3-Part Severity Summary - NOT IMPLEMENTED](#issue-3-3-part-severity-summary-not-implemented)
7. [Issue #4: Two Prescriptions Cross-Check - BROKEN](#issue-4-two-prescriptions-cross-check-broken)
8. [Issue #5: Daily Summary - NOT CONNECTED](#issue-5-daily-summary-not-connected)
9. [Issue #6: Pharma Research PubMed Results - NOT LINKED](#issue-6-pharma-research-pubmed-results-not-linked)
10. [LLM System Prompts Audit](#llm-system-prompts-audit)
11. [Issue #7: Tool Selection - ONLY 1 TOOL CALLED](#issue-7-tool-selection---only-1-tool-called)
12. [Pipeline Leaks and Undefined Behavior](#pipeline-leaks-and-undefined-behavior)
12. [Config Hardcoding Issues](#config-hardcoding-issues)
13. [Root Cause Analysis](#root-cause-analysis)
14. [Recommendations](#recommendations)
15. [Priority Fix List](#priority-fix-list)

---

## EXECUTIVE SUMMARY

| Category | Status | Severity |
|----------|--------|----------|
| Pharma Agent Pipeline | BROKEN | CRITICAL |
| Critical Alert Notifications | AUDIT-ONLY | CRITICAL |
| Renal Context Handling | HARDCODED | HIGH |
| Cross-Prescription Detection | PARTIAL | HIGH |
| 3-Part Severity Summary | NOT IMPLEMENTED | MEDIUM |
| Daily Summary Auto-Delivery | MISSING | MEDIUM |
| PubMed Research Integration | DISCONNECTED | MEDIUM |
| LLM Prompt Quality | NEEDS WORK | MEDIUM |
| Multi-language Support | MISSING | HIGH |
| Tool Selection | RXNAV-ONLY | HIGH |

**The Pharma Agent is NOT fully functional for the described use case.**

---

## FULL PIPELINE TRACE: PRESCRIPTION TO ALERT

### Current Flow (Broken)

```
1. User uploads prescription photo
   └─ main.py:442-492 (/api/send endpoint)
      └─ Creates pending_task with task_type='ocr_prescription'
      └─ executor.submit(process_task, task_id)

2. Task Processing
   └─ pipeline.py:364-410 (process_task function)
      └─ Derives task_type from media_type
      └─ IF task_type == 'ocr_prescription':
         └─ CALLS ingestion.process_prescription_photo()
         └─ DOES NOT use async_pipeline.py
         └─ ⚠️ ASYNC_PIPELINE NEVER TOUCHES PRESCRIPTION UPLOADS

3. Prescription Processing (ingestion.py)
   └─ process_prescription_photo() - Line ~1800+
   └─ OCR extraction (_run_paddle_ocr)
   └─ LLM extraction (_fallback_llm_extraction)
   └─ Classify content (classify_media_content)
   └─ Write medications (write_medication_from_json)
      └─ INSERT to medications table
      └─ IF trigger_pharma_agent=True:
         └─ CALL _trigger_pharma_agent_for_prescription()

4. Pharma Agent Trigger
   └─ ingestion.py:1357-1383 (_trigger_pharma_agent_for_prescription)
   └─ CALLS pharma_agent.process_new_medication()
   └─ trigger='prescription_photo'

5. Pharma Agent Evaluation (pharma_agent.py)
   └─ PharmaSafetyEngine.evaluate()
   └─ Check drug_interactions table
   └─ Check active_meds (new drug vs existing)
   └─ Check renal markers
   └─ IF critical interaction found:
      └─ Create alert (db.create_alert)
      └─ CALL _queue_caregiver_notification(force=True)
      └─ ⚠️ CAREGIVER NOTIFICATION IS FIRE-AND-FORGET

6. Notification (notifications.py)
   └─ send_caregiver_notifications()
   └─ WRITE TO AUDIT_LOG ONLY
   └─ NO WhatsApp/SMS/Twilio sender
   └─ NOTIFICATION_AUDIT_ONLY = True (config.py:348)

7. Alert Created but NOT Delivered
   └─ Alert exists in alerts table
   └─ Caregiver NOT notified in real-time
   └─ No 3-part severity summary format
```

### Pipeline Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        PRESCRIPTION UPLOAD FLOW                          │
└─────────────────────────────────────────────────────────────────────────┘

USER UPLOADS PHOTO
        │
        ▼
┌───────────────────┐
│  main.py:442-492  │
│  /api/send        │
│  media_url received│
└────────┬──────────┘
         │
         ▼
┌───────────────────┐     ┌─────────────────────────┐
│  main.py:467-477  │────►│  create_pending_task()  │
│  Create task      │     │  task_type='ocr_prescription'
└────────┬──────────┘     └──────────┬──────────────┘
         │                           │
         ▼                           ▼
┌───────────────────┐     ┌─────────────────────────┐
│  executor.submit  │     │  pending_tasks table    │
│  process_task     │     │  queued status          │
└────────┬──────────┘     └─────────────────────────┘
         │
         ▼
┌───────────────────────────────────────────────────────────────────┐
│                      pipeline.py:364-410                          │
│                      process_task(task_id)                         │
├───────────────────────────────────────────────────────────────────┤
│  task = db.get_pending_task(task_id)  ← Fetch task               │
│  task_type = derive_task_type(media_type)                        │
│  IF task_type == 'ocr_prescription':                               │
│     CALL ingestion.process_prescription_photo(processor_task)     │
│  ELIF task_type == 'pharm_research':                               │
│     CALL _process_pharm_research_task(task)                        │
│  ⚠️ ASYNC_PIPELINE.PY NEVER HANDLES PRESCRIPTION UPLOADS!          │
└────────────────────────────────┬──────────────────────────────────┘
                                 │
                                 ▼
┌───────────────────────────────────────────────────────────────────┐
│                   ingestion.py - process_prescription_photo()     │
│                   (NOT async_pipeline.py)                          │
├───────────────────────────────────────────────────────────────────┤
│  1. Download media from media_url                                 │
│  2. Run OCR (_run_paddle_ocr) - Multiple variants                  │
│  3. Merge OCR outputs (_merge_ocr_outputs)                       │
│  4. Classify content (classify_media_content)                     │
│  5. LLM Extraction (llm_gateway.extract_structured_data)          │
│  6. Validate (validate_and_fill_missing)                           │
│  7. write_medication_from_json() ← INSERT TO DB                   │
│     └─ IF trigger_pharma_agent=True:                              │
│        └─ CALL _trigger_pharma_agent_for_prescription()           │
│           └─ CALLS pharma_agent.process_new_medication()          │
└────────────────────────────────┬──────────────────────────────────┘
                                 │
                                 ▼
┌───────────────────────────────────────────────────────────────────┐
│                   pharma_agent.py - process_new_medication()        │
├───────────────────────────────────────────────────────────────────┤
│  1. PharmaSafetyEngine.evaluate()                                 │
│     ├─ Load drug_interactions from DB                              │
│     ├─ Get active_meds for patient                                │
│     ├─ Get patient_conditions                                      │
│     ├─ Get renal_markers                                          │
│     │   └─ ⚠️ Returns empty if None - NO WARNING GENERATED        │
│     └─ Check against PHARMA_RENAL_CLEARED_DRUGS                    │
│        └─ ⚠️ HARDCODED: only metformin, furosemide, digoxin        │
│                                                                │
│  2. IF critical interaction found:                               │
│     ├─ db.create_alert() → alerts table                          │
│     └─ _queue_caregiver_notification(force=True)                  │
│        └─ ⚠️ FIRE-AND-FORGET THREAD - NO REAL DELIVERY            │
│                                                                │
│  3. Return envelope with status/max_severity/alerts_created      │
└────────────────────────────────┬──────────────────────────────────┘
                                 │
                                 ▼
┌───────────────────────────────────────────────────────────────────┐
│                   notifications.py - send_caregiver_notifications()  │
├───────────────────────────────────────────────────────────────────┤
│  1. Get caregivers from DB                                        │
│  2. Build crisis message (crisis.format_crisis_card)             │
│  3. FOR EACH caregiver:                                           │
│     └─ db.write_audit() → audit_log (NOT REAL NOTIFICATION)      │
│  4. NOTIFICATION_AUDIT_ONLY = True (config.py:348)                │
│  5. ⚠️ NO WhatsApp/SMS/Twilio/Email integration                   │
│  6. ⚠️ NO 3-part severity summary format                           │
└───────────────────────────────────────────────────────────────────┘

RESULT: Alert created in DB, but caregiver NEVER notified in real-time.
```

---

## CRITICAL ISSUES FOUND

---

## ISSUE #1: ALERT PRIMARY CAREGIVER WHEN CRITICAL DRUG INTERACTION FOUND - NOT WORKING

### Location
- `pharma_agent.py:602-631` (Critical severity handling)
- `notifications.py:163-226` (Caregiver notification)
- `config.py:348` (`NOTIFICATION_AUDIT_ONLY = True`)
- `alerting.py:79-108` (Crisis alert creation)

### Problem Description

When a critical drug interaction is detected, the code does the following:

```python
# pharma_agent.py:602-631
if this_severity == "critical":
    alert_id = db.create_alert(
        patient_id,
        "drug_interaction",
        "critical",
        message,
        _alert_payload(new_drug, existing_drug, interaction, dose_amount, prescribed_by, trigger),
    )
    # ...
    if alert_id:
        caregiver_notifications.extend(
            _queue_caregiver_notification(
                patient_id,
                patient_name,
                force=True,  # Force notification
                trigger_message=message,
            )
        )
```

**BUT `_queue_caregiver_notification` is a fire-and-forget function:**

```python
# pharma_agent.py:989-1019
def _queue_caregiver_notification(
    patient_id: str,
    patient_name: str,
    force: bool = False,
    trigger_message: str | None = None,
    triggered_by_phone: str | None = None,
) -> list[dict]:
    def _worker():
        try:
            notifications.send_caregiver_notifications(
                patient_id=patient_id,
                patient_name=patient_name,
                force=force,
                trigger_message=trigger_message,
                triggered_by_phone=triggered_by_phone,
            )
        except Exception:
            pass  # SILENTLY IGNORES FAILURE

    threading.Thread(target=_worker, daemon=True).start()  # Fire and forget
    return [
        {
            "notification_status": "queued",
            "delivery_channel": "background_audit_log",  # ⚠️ ONLY AUDIT LOG
            "trigger_message": trigger_message,
        }
    ]
```

**And `send_caregiver_notifications` only writes to audit_log:**

```python
# notifications.py:163-226
def send_caregiver_notifications(...) -> list[dict]:
    # ...
    for caregiver in caregivers:
        db.write_audit(
            patient_id=patient_id,
            profile_id=None,
            entity_type="crisis_notification",
            entity_id=None,
            action="caregiver_notified",
            actor_role="system",
            new_value={
                "caregiver_name": caregiver.get("name"),
                "caregiver_role": caregiver.get("role"),
                "caregiver_phone": caregiver.get("phone"),
                "message_length": len(message),
                "message": message,  # Message logged but NOT sent
                # ...
            },
        )
        notifications.append({
            "name": caregiver.get("name"),
            "notification_status": "logged",  # Only logged, not sent!
            # ...
        })
```

### Root Cause

1. **Phase 1 implementation only** - All notifications are audit-log backed
2. **No Twilio/WhatsApp integration** - The actual delivery channel is missing
3. **`NOTIFICATION_AUDIT_ONLY = True`** in config.py:348
4. **Fire-and-forget pattern** - Failures are silently ignored
5. **No retry mechanism** - Failed notifications are never retried

### Why It Fails for Your Scenario

When you upload 2 prescriptions from different hospitals:
1. First prescription → PharmaAgent checks → Critical interaction found
2. Alert created in DB (alerts table)
3. Caregiver notification LOGGED to audit_log ONLY
4. **Caregiver NEVER receives real WhatsApp/SMS notification**
5. Patient is at risk until someone manually checks the dashboard

### How It Should Work

```python
# When severity == 'critical', the system SHOULD:
# 1. Create alert in DB
# 2. IMMEDIATELY send WhatsApp message to primary caregiver:
#    "⚠️ CRITICAL: [Drug A] + [Drug B] - Contact doctor immediately"
# 3. Send SMS as backup
# 4. Log to audit for compliance
# 5. Return confirmation to uploading user

# Current: Step 2 and 3 are MISSING
```

---

## ISSUE #2: RENAL CONTEXT - HARDCODED NOT GENERAL

### Location
- `pharma_agent.py:274-293` (`_evaluate_renal_risk`)
- `config.py:288` (`PHARMA_RENAL_CLEARED_DRUGS = {"metformin", "furosemide", "digoxin"}`)
- `config.py:289` (`PHARMA_EGFR_WARNING_THRESHOLD = 30.0`)

### Problem Description

The renal context handling is HARDCODED for specific drugs only:

```python
# pharma_agent.py:274-293
def _evaluate_renal_risk(self, normalized_new: str, renal_markers: dict | None) -> list[dict]:
    # ⚠️ HARDCODED: Only checks these 3 drugs
    if normalized_new not in config.PHARMA_RENAL_CLEARED_DRUGS or not renal_markers:
        return []  # Returns EMPTY if drug not in list OR markers missing!

    egfr = self._marker_value(renal_markers, "egfr")
    if egfr is None or egfr >= config.PHARMA_EGFR_WARNING_THRESHOLD:
        return []

    return [{
        "type": "renal_clearance_warning",
        "drug": normalized_new,
        "severity": "high",
        "message": f"{normalized_new.title()} is renal-cleared and eGFR is {egfr}",
        "source": "renal_marker_rule",
    }]
```

```python
# config.py:288-289
PHARMA_RENAL_CLEARED_DRUGS = {"metformin", "furosemide", "digoxin"}
PHARMA_EGFR_WARNING_THRESHOLD = 30.0
```

**And when renal_markers is None:**

```python
# If renal_markers is None, the function returns [] immediately
# NO WARNING IS GENERATED
# NO ALERT IS CREATED
# NO CAREGIVER IS NOTIFIED
```

### Why This Is Wrong

**You said: "We could see pharma agent specifically trained for Renal issue, it should handle any case"**

Currently the system:
1. ❌ Only checks 3 specific drugs for renal risk
2. ❌ Does NOT generate any warning if renal_markers is missing
3. ❌ Does NOT alert caregivers when critical patient data is absent
4. ❌ Does NOT handle other renal-cleared drugs (lisinopril, enalapril, etc.)
5. ❌ Does NOT handle hepatic impairment context
6. ❌ Does NOT handle cardiac conditions
7. ❌ Does NOT handle pediatric/elderly populations

### What Should Happen

```python
# When patient has renal condition BUT no eGFR data:
# 1. Generate HIGH severity alert
# 2. Notify caregiver: "Cannot complete safety check - missing eGFR"
# 3. Flag medication as "pending_review" until eGFR available
# 4. Do NOT allow medication to be marked as "active"

# When patient has ANY condition that requires monitoring:
# 1. Check if drug is affected by that condition
# 2. Generate appropriate warning
# 3. Notify caregiver
# 4. Mark medication for review

# NOT HARDCODED to 3 drugs
```

### Config Hardcoding Issues

```python
# config.py:288
PHARMA_RENAL_CLEARED_DRUGS = {"metformin", "furosemide", "digoxin"}
# ⚠️ Should be configurable, loaded from DB
# ⚠️ Should support all renally cleared drugs
# ⚠️ Should support hepatic cleared drugs
# ⚠️ Should support cardiac cleared drugs
```

---

## ISSUE #3: 3-PART SEVERITY SUMMARY - NOT IMPLEMENTED

### Location
- `pharma_agent.py:422-522` (PharmaExplainer class)
- `pharma_agent.py:673-685` (When explanation is generated)

### Required Format (From Your Spec)

```
SEVERITY IN PLAIN-LANGUAGE SUMMARY IN 3-PART FORMAT:
Part 1: What is the risk?
Part 2: Why does it matter?
Part 3: What should the caregiver do?

IF severity=critical → send alert message to primary caregiver immediately.
```

### Current Implementation

```python
# pharma_agent.py:422-522 - PharmaExplainer
class PharmaExplainer:
    schema = {
        "summary": "One clear sentence for caregivers",
        "mechanism": "Simple explanation of why this interaction matters",
        "monitoring": ["list", "of", "specific", "things", "to", "watch"],
        "action": "continue|monitor|consult|avoid",
        "confidence": 0.0,
    }
```

**The schema has the right fields, but:**

1. ❌ `summary` is NOT formatted as 3-part structure
2. ❌ `mechanism` is not tied to part 2 requirement
3. ❌ `action` is not tied to part 3 requirement
4. ❌ No auto-detection to send alert when severity=critical
5. ❌ Explanation is NOT sent to caregiver in real-time

### Where Explanation Is Called

```python
# pharma_agent.py:673-685
if getattr(config, "PHARMA_SYNC_EXPLANATION_ENABLED", False) and \
   evaluation_result.get("max_severity") in {"medium", "high", "critical"}:
    explainer = PharmaExplainer()
    explanation_target = _first_explanation_target(evaluation_result)
    # ...
    explanation = explainer.generate(explanation_target, patient_context, use_reasoning=use_reasoning)
    if explanation and float(explanation.get("confidence") or 0.0) >= config.PHARMA_EXPLANATION_CONFIDENCE_THRESHOLD:
        # Updates alert with explanation
        for alert_id in alert_ids:
            _update_alert_with_explanation(alert_id, explanation)
        evaluation_result["explanation"] = explanation
```

**BUT:**
- `PHARMA_SYNC_EXPLANATION_ENABLED` may be False
- Explanation is written to DB, NOT sent to caregiver
- No 3-part format structure
- No immediate alert trigger for critical

---

## ISSUE #4: TWO PRESCRIPTIONS CROSS-CHECK - BROKEN

### Location
- `pharma_agent.py:554-562` (Approval skip logic)
- `db.py:2990-3022` (Duplicate medication check)

### Problem #1: Agent Approval Skip

```python
# pharma_agent.py:554-562
coarse_hash = hashlib.sha256(f"{patient_id}:{normalized_new}".encode()).hexdigest()
if not force_recheck and _agent_approval_exists(coarse_hash):
    return {
        "status": "skipped",
        "reason": "existing_agent_approval",  # ⚠️ SKIPS THE CHECK!
        "max_severity": "none",
        "new_drug": new_drug,
        "rule_hash": coarse_hash,
    }
```

**When 2nd prescription arrives:**
1. First prescription created approval for Drug A
2. Second prescription with Drug A from different hospital
3. `_agent_approval_exists(coarse_hash)` returns True
4. **Entire PharmaAgent check is SKIPPED**
5. **No interaction check against Drug B (the contraindicated drug)**

### Problem #2: Duplicate Medication Check

```python
# db.py:2990-3022
def is_duplicate_medication(patient_id: str, drug_name: str, dose_amount, recorded_at: str | None = None) -> bool:
    # Only checks within 1 hour
    # Does NOT cross-check with different hospitals
    # Does NOT check for conflicting drugs
    # Does NOT check for different departments
```

**Current check:**
- Same drug + same dose + within 1 hour = duplicate
- **Different hospital = NOT detected as duplicate**
- **Different department = NOT detected as duplicate**

### What Should Happen

```python
# When 2nd prescription arrives:
# 1. Check all active medications (not just duplicate check)
# 2. If new drug conflicts with existing medication:
#    - Create interaction alert
#    - Alert primary caregiver IMMEDIATELY
#    - Do NOT skip based on previous approval
# 3. If same drug from different hospital:
#    - Flag for review (potential dosage change)
#    - Do NOT silently skip

# Critical interaction check must ALWAYS run
# Do NOT skip based on approval hash
```

---

## ISSUE #5: DAILY SUMMARY - NOT CONNECTED

### Location
- `handlers.py:763-786` (handle_care_summary)
- `nlp_deterministic.py` (assemble_care_summary)
- No scheduled task for auto-delivery

### Current State

```python
# handlers.py:763-786
def handle_care_summary(profile: dict) -> str:
    # Manual trigger only - user must ASK for summary
    # No scheduled auto-delivery
    # No integration with notification system
```

**What's Missing:**
- ❌ No cron job or scheduled task
- ❌ No daily summary generation at midnight/morning
- ❌ No automatic sending to primary caregiver
- ❌ No notification integration
- ❌ No summary format defined

### What Should Exist

```python
# Should have a scheduled task like:
def generate_daily_summary(patient_id: str):
    """Runs daily at 7:00 AM via scheduler"""
    active_meds = db.get_active_medications_schedule(patient_id)
    adherence = compute_adherence_snapshot(patient_id, ...)
    open_alerts = db.get_open_alerts(patient_id)
    
    summary = {
        "date": today,
        "medications": [...],
        "adherence": adherence,
        "alerts": [...],
        "notes": [...]
    }
    
    # Format for caregiver
    message = format_daily_summary(summary)
    
    # Send via notification
    send_caregiver_notifications(patient_id, message)
```

---

## ISSUE #6: PHARMA RESEARCH PUBMED RESULTS - NOT LINKED

### Location
- `pharma_research.py:234-244` (_execute_tools)
- `pharma_research.py:297-308` (_context_flags)

### Problem Description

```python
# pharma_research.py:297-308
def _context_flags(drug_a: str, drug_b: str, patient_context: dict) -> list[dict]:
    # Detects renal context
    if egfr is None:
        flags.append({"type": "renal_context", "status": "missing", "message": "No recent eGFR available."})
    # ...
    return flags
```

**But this renal_context flag:**
1. ❌ Is NOT used to escalate severity in pharma_agent
2. ❌ Does NOT trigger immediate caregiver alert
3. ❌ Does NOT block medication until eGFR available
4. ❌ Is stored in pharma_research_reports table only
5. ❌ Is NEVER sent to primary caregiver

### Your Spec Requirement

```
Type renal context - In pharma_research tools table in pubmed tool results

{
  "pubmed": [...],
  "interaction_lookup": null,
  "patient_context_flags": [
    {
      "type": "renal_context",
      "status": "missing",
      "message": "No recent eGFR available."
    }
  ]
}
```

**This structure is created but NOT USED to:**
1. Modify severity in the main pharma_agent evaluation
2. Create a separate renal context alert
3. Notify caregivers about missing critical data
4. Block medication pending eGFR

---

## LLM SYSTEM PROMPTS AUDIT

### LLM Gateway Prompts (llm_gateway.py)

```python
# Lines 99-115
EXTRACTION_SYSTEM_PROMPT = """You are a deterministic medical data extractor.
Your ONLY job is to convert unstructured text into a strict JSON object.
You are NOT a conversational assistant. Do NOT explain. Do NOT add markdown.
Do NOT apologize. Do NOT add fields not in the schema."""

# Issues:
# 1. ❌ No mention of multi-language support (Hinglish)
# 2. ❌ No handling of handwritten text
# 3. ❌ No mention of Indian brand names
# 4. ❌ No instruction for unclear/ambiguous data
# 5. ❌ No instruction for dose extraction from Indian prescriptions
```

### Pharma Agent Prompts (llm_gateway.py:479-554)

```python
# ModelRouter - call_primary system prompt
"You are CareCircle's structured medical safety assistant. "
"Return only compact valid JSON. Do not invent patient facts."

# Issues:
# 1. ❌ No instruction for 3-part severity format
# 2. ❌ No instruction for caregiver alert trigger
# 3. ❌ No instruction for renal/hepatic context
# 4. ❌ No instruction for multi-drug interactions
# 5. ❌ No instruction for conflicting prescriptions
```

### LLM Policy Prompts (llm_policy.py)

```python
# BASE_LOW_RISK_SYSTEM
"You are CareCircle, a warm but factual assistant for family caregivers.
You may only answer low-risk greetings, emotional check-ins, or clarification questions."

# Issues:
# 1. ❌ No Hindi/Hinglish instruction
# 2. ❌ No multi-language support
# 3. ❌ Only handles "low-risk" cases
# 4. ❌ No medication safety explanation format
```

### Pharma Prompts (pharma_prompts.py)

```python
# PRESCRIPTION_PROMPT
"Extract ONLY medication orders from the source text.
Return JSON ONLY."

# Issues:
# 1. ❌ No instruction for Indian brand names (Glycomet, Amlodipine, etc.)
# 2. ❌ No instruction for combination drugs (Telma-AM, etc.)
# 3. ❌ No instruction for handwritten doses
# 4. ❌ No instruction for frequency in Hindi (OD, BD, TDS)
# 5. ❌ No instruction for different hospital formats
```

### Missing LLM Capabilities

1. **Multi-language extraction** - No Hindi prescription support
2. **Indian brand name resolution** - Glycomet → Metformin not in prompt
3. **Handwritten dose parsing** - Common in Indian prescriptions
4. **Combination drug handling** - Telma-AM is 2 drugs in 1 pill
5. **3-part severity format** - Not in any prompt
6. **Critical alert trigger** - Not in any prompt

---

## ISSUE #7: TOOL SELECTION - ONLY 1 TOOL CALLED (rxnav)

### Location
- `pharma_research.py:209` (`default_tools = ["rxnav", "openfda"]`)
- `pharma_research.py:224` (LLM planner override → `tools=["rxnav"]`)
- `pharma_research.py:234` (`_execute_tools` runs planner_result["tools"]`)
- `pharma_tools.py:1-165` (`DrugInteractionTool` class)
- `pharma_tools.py:146-165` (`DrugInteractionTool()` fallback → `rxnav + openfda`)

### Problem Description

The drug interaction check uses a **3-layer bottleneck** that limits tool calls to rxnav only:

**Layer 1 - Default Tools (pharma_research.py:209):**
```python
default_tools = ["rxnav", "openfda"]
# Only 2 tools available by default
```

**Layer 2 - LLM Planner Override (pharma_research.py:224):**
```python
# LLM is asked to pick tools
planner_result = self._plan_tools(drug_a, drug_b, ...)  # Returns tool list
# BUT the prompt only passes ["rxnav"] - openfda never selected
```

**Layer 3 - _execute_tools Limit (pharma_research.py:234):**
```python
for tool_name in planner_result["tools"]:
    tool = available_tools.get(tool_name)
    # Executes ONLY what planner returned
    # If planner returned ["rxnav"] only → openfda NEVER runs
```

**DrugInteractionTool Fallback (pharma_tools.py:146):**
```python
class DrugInteractionTool:
    def __init__(self):
        self.clients = {
            "rxnav": RxNavClient(...),
            "openfda": OpenFDAClient(...),
        }
    # Only has rxnav + openfda - no PubChem, DrugBank, etc.
```

### Why Only 1 Tool Happens

The LLM planner prompt in `_plan_tools` (pharma_research.py:224) is given a context with `["rxnav", "openfda"]` as options, but it decides to only return `["rxnav"]`. Then `_execute_tools` runs exactly what the planner returned, so `openfda` never executes.

**Result:**
- Only RxNorm data is fetched
- OpenFDA adverse event data is NEVER retrieved
- No drug label information from FDA
- No additional validation source

### What Should Happen

```python
# 1. Planner should return BOTH tools for critical checks:
planner_result["tools"] = ["rxnav", "openfda"]  # Both tools

# 2. OR Default tools should include all available tools:
default_tools = ["rxnav", "openfda", "pubchem", "drugbank"]

# 3. OR _execute_tools should have fallback behavior:
for tool_name in ["rxnav", "openfda"]:  # Always run critical tools
    tool = available_tools.get(tool_name)
    if tool:
        results[tool_name] = tool.execute(drug_a, drug_b)
```

### Missing Tool Coverage

| Tool | Status | Purpose |
|------|--------|---------|
| RxNav | ✅ Only one used | Drug interaction lookup |
| OpenFDA | ❌ Never called | Adverse events, drug labels |
| PubChem | ❌ Not integrated | Chemical data |
| DrugBank | ❌ Not integrated | Full drug database |
| Wikipedia | ❌ Not integrated | General info |

### Additional Findings

#### drug_resolver.py Partial Coverage
- Only resolves RxNorm CUIs
- No PubChem CID resolution
- No DrugBank ID resolution
- Limited to RxNav's known mappings

#### No Multi-Source Validation
- Single source (RxNav) used for critical decisions
- No cross-validation between databases
- No confidence scoring based on source agreement

---

## PIPELINE LEAKS AND UNDEFINED BEHAVIOR

### Leak #1: Async Pipeline Never Handles Prescriptions

**Location:** `pipeline.py:384-385`, `async_pipeline.py:838-857`

```python
# pipeline.py:384-385
if task_type == "ocr_prescription":
    result = ingestion.process_prescription_photo(processor_task)
    # NEVER calls async_pipeline.py
```

**Problem:**
- `async_pipeline.py` has `AsyncDocumentPipeline` class
- BUT prescription uploads go through `ingestion.process_prescription_photo`
- `async_pipeline.py` only handles `pharm_research` tasks
- **Two different code paths for similar functionality**

### Leak #2: Trigger Pharma Agent - Conflicting Logic

**Location:** `ingestion.py:1328-1335`, `pipeline.py:240-261`

```python
# ingestion.py:1328-1335
if trigger_pharma_agent:
    med["pharma_agent_triggered"] = _trigger_pharma_agent_for_prescription(...)

# pipeline.py:240-261
def _run_post_hooks(patient_id: str, result: dict) -> None:
    if not result.get("pharma_agent_triggered"):  # If NOT triggered
        import pharma_agent
        for med_id in medications_added_list:
            # Trigger pharma_agent AGAIN
```

**Problem:**
- `ingestion.write_medication_from_json` triggers pharma_agent
- `pipeline._run_post_hooks` also triggers pharma_agent (if not already triggered)
- Potential double-trigger
- Confusing logic flow

### Leak #3: Pharma Agent Skip Logic

**Location:** `pharma_agent.py:554-562`

```python
# If approval exists for same drug, skip entire check
if not force_recheck and _agent_approval_exists(coarse_hash):
    return {"status": "skipped", "reason": "existing_agent_approval", ...}
```

**Problem:**
- When 2nd prescription from different hospital arrives
- System checks if approval exists for that drug
- If yes, SKIPS entire interaction check
- **This breaks the cross-prescription detection**

### Leak #4: Renal Markers None Handling

**Location:** `pharma_agent.py:274-293`

```python
def _evaluate_renal_risk(self, normalized_new: str, renal_markers: dict | None) -> list[dict]:
    if normalized_new not in config.PHARMA_RENAL_CLEARED_DRUGS or not renal_markers:
        return []  # ⚠️ Returns empty, no warning generated
```

**Problem:**
- If `renal_markers` is None (no eGFR data)
- AND patient has renal condition
- NO warning is generated
- NO alert is created
- Caregiver NOT notified
- **Patient safety compromised**

### Leak #5: Notification Fire-and-Forget

**Location:** `pharma_agent.py:989-1019`

```python
def _queue_caregiver_notification(...) -> list[dict]:
    def _worker():
        notifications.send_caregiver_notifications(...)  # May fail silently
    
    threading.Thread(target=_worker, daemon=True).start()  # Fire and forget
    return [{"notification_status": "queued", ...}]  # Fake success
```

**Problem:**
- No error handling in worker thread
- No retry mechanism
- No delivery confirmation
- No fallback to alternative channels

---

## CONFIG HARDCODING ISSUES

### Hardcoded Drug Lists

```python
# config.py:288
PHARMA_RENAL_CLEARED_DRUGS = {"metformin", "furosemide", "digoxin"}
# Should be loaded from DB, not hardcoded
```

```python
# pharma_agent.py:345-368
aliases = {
    "ecosprin": "aspirin",
    "glycomet": "metformin",  # Indian brand
    # More should be configurable
}
```

### Hardcoded Thresholds

```python
# config.py:289
PHARMA_EGFR_WARNING_THRESHOLD = 30.0
# Should be configurable per patient condition
```

### Hardcoded Notification Settings

```python
# config.py:347-348
NOTIFICATION_DISPATCH_ENABLED = True
NOTIFICATION_AUDIT_ONLY = True  # Phase 1 only
# No Twilio/WhatsApp integration yet
```

---

## ROOT CAUSE ANALYSIS

### Why Pharma Agent Is Not Working as Expected

| Root Cause | Impact | Location |
|------------|--------|----------|
| Notification is audit-log only | Caregiver never notified | notifications.py, config.py |
| Renal check is hardcoded to 3 drugs | Other renal drugs not checked | pharma_agent.py:274 |
| Renal markers None returns empty | No warning for missing eGFR | pharma_agent.py:275 |
| Approval skip logic breaks cross-check | 2nd prescription skipped | pharma_agent.py:554 |
| 3-part severity format not implemented | No structured summary | pharma_agent.py:422 |
| No critical alert trigger | No immediate notification | pharma_agent.py:673 |
| Daily summary not scheduled | No auto-delivery | handlers.py:763 |
| PubMed renal context not used | Flagged but ignored | pharma_research.py:297 |
| LLM prompts lack multi-language | Hindi prescriptions fail | llm_gateway.py:99 |
| Two code paths for prescriptions | Inconsistent behavior | pipeline.py vs async_pipeline.py |

---

## RECOMMENDATIONS

### Priority 1 (Critical - Must Fix)

1. **Implement Real Notification Delivery**
   - Add Twilio SMS integration
   - Add WhatsApp Business API integration
   - Remove `NOTIFICATION_AUDIT_ONLY = True` or make it configurable
   - Add retry mechanism for failed notifications

2. **Fix Renal Context Handling**
   - Remove hardcoded drug list
   - Load all renal-cleared drugs from DB
   - When `renal_markers` is None AND patient has renal condition:
     - Generate HIGH severity alert
     - Notify caregiver immediately
     - Mark medication as "pending_review"

3. **Fix Cross-Prescription Detection**
   - Remove/fix approval skip logic in pharma_agent.py:554
   - Always run interaction check regardless of previous approvals
   - Check new drug against ALL active medications

### Priority 2 (High - Should Fix)

4. **Implement 3-Part Severity Summary**
   - Format PharmaExplainer output as 3-part structure
   - Part 1: What is the risk?
   - Part 2: Why does it matter?
   - Part 3: What should caregiver do?
   - Auto-trigger alert for critical severity

5. **Add Multi-Language Support**
   - Update LLM prompts for Hindi/Hinglish extraction
   - Add Indian brand name resolution
   - Handle combination drugs (Telma-AM)

6. **Implement Daily Summary Scheduler**
   - Create scheduled task for daily summary generation
   - Send to primary caregiver at configured time
   - Format summary for WhatsApp (short, actionable)

### Priority 3 (Medium - Nice to Have)

7. **Unify Prescription Processing**
   - Use single code path for prescription uploads
   - Remove duplicate logic between ingestion.py and async_pipeline.py

8. **Add Drug Interaction Confidence Scoring**
   - Not just severity, but confidence in detection
   - Handle uncertain cases with caregiver review

9. **Improve LLM Prompt Engineering**
   - Test prompts with real Indian prescriptions
   - Add few-shot examples for common patterns
   - Add handling for unclear/missing data

---

## PRIORITY FIX LIST

### Fix #1: Real-time Caregiver Notification
```
File: notifications.py
Change: Implement actual WhatsApp/SMS sending
Add: Twilio/WhatsApp API integration
Add: Retry mechanism
Add: Delivery confirmation
```

### Fix #2: General Renal Context
```
File: pharma_agent.py
Change: Remove hardcoded PHARMA_RENAL_CLEARED_DRUGS check
Add: Load all condition-specific drugs from DB
Add: When markers missing → generate HIGH alert + notify caregiver
Add: Handle hepatic, cardiac, pediatric contexts
```

### Fix #3: Cross-Prescription Detection
```
File: pharma_agent.py:554-562
Change: Remove or fix _agent_approval_exists skip logic
Always: Run interaction check against ALL active medications
Never: Skip based on previous approval
```

### Fix #4: 3-Part Severity Summary Format
```
File: pharma_agent.py:422-522 (PharmaExplainer)
Change: Format output as 3-part structure
Add: Part 1 = summary (What is the risk?)
Add: Part 2 = mechanism (Why does it matter?)
Add: Part 3 = action (What should caregiver do?)
Add: Auto-trigger alert when severity=critical
```

### Fix #5: Daily Summary Auto-Delivery
```
File: handlers.py or new file: daily_summary.py
Add: Scheduled task for daily summary
Add: Format for WhatsApp
Add: Send to primary caregiver
Add: Configurable delivery time
```

### Fix #6: Multi-Language LLM Support
```
File: llm_gateway.py, pharma_prompts.py
Add: Hindi prescription extraction instructions
Add: Indian brand names
Add: Hinglish handling
Add: Combination drugs (Telma-AM)
```

---

## SUMMARY

The Pharma Agent has significant gaps that prevent it from working as expected for the described use case:

1. **Critical Alerts** - Only logged, not sent to caregivers
2. **Renal Context** - Hardcoded to 3 drugs, fails when markers missing
3. **Cross-Prescription** - Skip logic breaks detection
4. **3-Part Summary** - Not implemented
5. **Daily Summary** - Not connected
6. **LLM Prompts** - Missing multi-language support

**The system needs significant fixes before it can reliably protect patients from drug interactions.**

---

*Report Generated: May 12, 2026*  
*CareCircle Pharma Agent Audit*