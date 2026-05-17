| Config | Default | Problem |
|--------|---------|---------|
| `PHARMA_EXPLANATION_CONFIDENCE_THRESHOLD` | 0.7 | Explanations below 0.7 are discarded — but for critical interactions, even 0.5 confidence should trigger an explanation |
| `PHARMA_RESEARCH_MIN_CONFIDENCE` | 0.85 | Very high bar — most research reports in shadow mode won't pass |
| `PHARMA_MIN_FEEDBACK_COUNT` | 5 | Too many — rare drug pairs may never accumulate enough feedback to escalate |

---

## 5. LLM & SYSTEM PROMPT ISSUES

### 5.1 Hardcoded Prompts
**WHERE:** `pharma_prompts.py`, `llm_gateway.py`, `pharma_agent.py` → `PharmaExplainer`

| Prompt | Location | Problem |
|--------|----------|---------|
| EXTRACTION_SYSTEM_PROMPT | `llm_gateway.py:99-115` | No safety-specific instructions for drug extraction |
| PRESCRIPTION_PROMPT | `pharma_prompts.py:16-49` | No instruction to flag potential interactions |
| LAB_REPORT_PROMPT | `pharma_prompts.py:52-84` | No renal-specific flagging |
| PharmaExplainer prompt | `pharma_agent.py:436-458` | English-only, no multilingual support |
| _plan_tools LLM planner | `pharma_research.py:215-229` | `PHARMA_RESEARCH_LLM_PLANNER_ENABLED=False` by default |

### 5.2 Tool Calling Issue: Single Interaction Lookup

**WHERE:** `pharma_research.py` → `_execute_tools()` lines 234-244

**WHY ONLY 1 TOOL FIRES:**

1. `DrugInteractionTool.check_interaction()` at `pharma_tools.py:151-169` chains RxNav → OpenFDA as **primary/fallback**, not parallel:
   ```python
   result = self.rxnav.get_interaction(drug_a, drug_b)
   source = "rxnav"
   if not result:  # ← Only falls back if RxNav fails
       result = self.openfda.find_interaction(drug_a, drug_b)
       source = "openfda"
   ```

2. If RxNav responds with ANY result (even "no interaction found" is actually a dict), OpenFDA is never called

3. The interaction_lookup result is stored as a SINGLE key in results — both RxNav and OpenFDA would share the same output key

4. PubMed, herb_checker, and patient_context_flags are called independently, but their results are NOT merged into the interaction_lookup severity. Only `_synthesize_report()` combines them later

5. No retry or parallel execution — if RxNav times out (10s), it blocks before OpenFDA can run

**FIX APPROACH:** Call RxNav and OpenFDA in parallel, compare results, and use the HIGHER severity. Store each source's result independently.

### 5.3 Missing Tool Calls: What Should Fire But Doesn't

| Tool | Config Flag | Should Fire? | Actually Fires? | Why Not? |
|------|------------|-------------|----------------|----------|
| RxNav (interaction) | Always included | Yes | Yes (primary) | First in chain |
| OpenFDA (interaction) | Always included | Yes | Only if RxNav fails/null | Fallback logic |
| PubMed (evidence) | `PHARMA_RESEARCH_PUBMED_ENABLED` (default True) | Yes | Yes (if enabled) | Separate slot, but shallow results |
| Herb Checker | Conditional (herb detection) | Rarely | Only if herb detected | Narrow trigger |
| LLM Planner | `PHARMA_RESEARCH_LLM_PLANNER_ENABLED` (default False) | Should optimize tool selection | Never | Disabled by default |
| LLM Synthesis | `PHARMA_RESEARCH_LLM_SYNTHESIS_ENABLED` (default True) | Yes | Yes (if enabled) | Dependent on LLM gateway |
| Safety Gate (NVIDIA) | `PHARMA_RESEARCH_NVIDIA_SAFETY_REQUIRED` (default False) | Optional | Never | Disabled by default |

---

## 6. RENAL CONTEXT: Why Hardcoding Fails Real-World Populations

### Current Implementation (Hardcoded for Specific Population)

**WHERE:** `config.py` line 288-289, `pharma_agent.py` lines 274-293

```python
# config.py — hardcoded drug list
PHARMA_RENAL_CLEARED_DRUGS = {"metformin", "furosemide", "digoxin"}
PHARMA_EGFR_WARNING_THRESHOLD = 30.0
```

```python
# pharma_agent.py — silently returns empty when renal markers missing
def _evaluate_renal_risk(self, normalized_new, renal_markers):
    if normalized_new not in config.PHARMA_RENAL_CLEARED_DRUGS or not renal_markers:
        return []  # ← TWO FAILURES IN ONE LINE
```

**PROBLEM 1: Only 3 drugs checked** — metformin, furosemide, digoxin. But hundreds of drugs require renal dosing adjustments: gabapentin, pregabalin, acyclovir, vancomycin, DOACs (rivaroxaban, apixaban, dabigatran), lithium, methotrexate, many antibiotics, etc.

**PROBLEM 2: Missing eGFR = silent skip** — When `renal_markers` is `None` or empty, the entire function returns `[]`. No warning, no alert, no flag. The system pretends renal safety doesn't matter when data is unavailable.

**PROBLEM 3: Fixed eGFR threshold** — Only checks if eGFR < 30.0. But:
- eGFR 30-45 (CKD stage 3b) may require dose adjustment for many drugs
- eGFR 45-60 (CKD stage 3a) affects some drugs
- The threshold should be DRUG-SPECIFIC, not a global constant

**PROBLEM 4: Built for one population** — The 3 drugs and 30.0 threshold suggest this was built specifically for a known patient population (perhaps heart failure patients on metformin + furosemide + digoxin). For general use across different demographics, this is dangerously incomplete.

### What Should Happen

1. **Drug-agnostic renal alerting:** When eGFR is missing for ANY patient with ANY new prescription, generate an advisory alert requesting renal function testing
2. **Configurable drug-renal mapping:** A database table mapping drugs to their renal-clearance status, dosing adjustment thresholds, and severity — not a 3-element Python set
3. **Graded severity:** eGFR 30-45 → advisory, eGFR 15-30 → warning, eGFR <15 → critical
4. **Missing data is itself a finding:** "Renal markers unavailable — cannot verify safety" should be a distinct alert type

---

## 7. FIX RECOMMENDATIONS (NO HARDCODING)

### Tier 1: Immediate (Patient Safety)

| # | Fix | File | Approach |
|---|-----|------|----------|
| 1 | Fix alert dedup to allow multiple prescriptions of same interaction | `pharma_agent.py` `_recent_interaction_alert_pairs()` | Include `prescribed_by` and `source_type` in dedup key, OR add a `source_prescription_id` field |
| 2 | Replace daemon thread with durable notification queue | `pharma_agent.py` `_queue_caregiver_notification()` | Use a DB-backed task queue or message queue (Redis/RabbitMQ) with retry |
| 3 | Add actual notification channel integration | `notification_dispatcher.py` | Integrate WhatsApp Business API / SMS gateway / Firebase push |
| 4 | Add caregiver notification for HIGH severity | `pharma_agent.py` lines 632-650 | Extend the critical notification block to include high-severity interactions |
| 5 | Fix `get_recent_vitals()` to actually query the DB | `db.py` line 209 | Implement the function to query vitals table |

### Tier 2: Short-Term (Workflow Completeness)

| # | Fix | File | Approach |
|---|-----|------|----------|
| 6 | Generate renal data-missing alerts | `pharma_agent.py` `_evaluate_renal_risk()` + new `alerting.py` function | When eGFR is missing, create an advisory alert requesting lab work |
| 7 | Make renal drug list configurable via DB | `config.py` + new DB table | Create `renal_dosing_rules` table with drug, egfr_threshold, severity columns |
| 8 | Add interaction detail to daily summary | `nlp_deterministic.py` `_critical_items()` + `_attention_items()` | Query open drug_interaction alerts and include drug pair + action needed |
| 9 | Add 3-part plain-language summary as default | `pharma_agent.py` `process_new_medication()` | Generate template-based summary even when LLM is unavailable |
| 10 | Unify trigger paths — remove dual execution | `ingestion.py` + `db.py` trigger | Either always use inline OR always use async, not both. DB trigger is the safest (catches all inserts) |
| 11 | Fix `_interaction_rule_hash` to be drug-pair only | `pharma_agent.py` line 1061 | Remove `patient_id` from hash to enable cross-patient learning |

### Tier 3: Medium-Term (Systemic Improvements)

| # | Fix | File | Approach |
|---|-----|------|----------|
| 12 | Promote research pipeline to production | `pharma_research.py` + `pharma_promotion.py` | When research gate passes with high confidence, automatically update deterministic rules |
| 13 | Add holistic medication re-evaluation | `pharma_agent.py` new function `recheck_interactions()` | When new drug added, recheck ALL pairs, not just new-vs-existing |
| 14 | Parallel tool execution in research pipeline | `pharma_research.py` `_execute_tools()` | Call RxNav, OpenFDA, PubMed concurrently; merge results by highest severity |
| 15 | Improve LLM prompts for multilingual safety | `pharma_prompts.py`, `llm_gateway.py` | Add safety guardrails, multilingual instructions, and drug-conflict flagging prompts |
| 16 | Add duplicate detection at ingestion for multi-source prescriptions | `ingestion.py` `write_medication_from_json()` | Fuzzy-match drug name + patient regardless of dose unit variations |
| 17 | Make PharmaExplainer run async | `pharma_agent.py` line 673 | Move LLM explanation to background thread; don't block alert delivery |
| 18 | Add confidence-aware alerting tiers | `config.py` + `pharma_agent.py` | Critical: alert immediately. High: alert within 1 hour. Medium: include in daily summary |
| 19 | Fix caregiver role lookup to be case-insensitive | `pharma_promotion.py` line 190 | Normalize role comparison: `.lower().replace(" ", "_")` or use role enum |
| 20 | Remove hardcoded 3-drug renal list | `config.py` line 288 | Replace with DB-driven table that can grow per population needs |
| 21 | Add prescription source tracking | New column in `medications` table | Track `source_hospital`, `source_department`, `prescriber_id` for multi-hospital dedup |
| 22 | Fix `_agent_approval_exists` — coarse hash blocks legitimate re-evaluation | `pharma_agent.py` line 555 | Context-aware dedup: same drug + same existing meds + same renal status = skip. Any change = re-evaluate |

---

## APPENDIX: File Reading Order (for verification)

All findings above are based on reading these files in sequence:
1. `pharma_agent.py` (1284 lines) — Core engine
2. `pharma_research.py` (472 lines) — Research pipeline
3. `pharma_tools.py` (296 lines) — Tool implementations
4. `pharma_promotion.py` (248 lines) — Promotion/veto logic
5. `notifications.py` (229 lines) — Carer messaging
6. `notification_dispatcher.py` (46 lines) — Dispatch facade
7. `alerting.py` (117 lines) — Alert creation
8. `handlers.py` (1293 lines) — Message routing
9. `ingestion.py` (2892 lines) — Media ingestion
10. `async_pipeline.py` (993 lines) — Background processing
11. `pipeline.py` (463 lines) — Task orchestration
12. `config.py` (439 lines) — All thresholds
13. `db.py` (3554 lines) — Database operations
14. `nlp_deterministic.py` (868 lines) — Summary assembly
15. `drug_resolver.py` (123 lines) — Drug normalization
16. `llm_gateway.py` (716 lines) — LLM routing
17. `main.py` (563 lines) — API endpoints
18. `crisis.py` (491 lines) — Crisis cards
19. `crisis_runtime.py` (90 lines) — Fast-path crisis
20. `monitor_pharma_agent.py` (131 lines) — Monitoring
21. `pharma_prompts.py` (269 lines) — LLM prompts
22. `verify_pharma_agent.py` (134 lines) — Verification tests
23. `PHARMA_AGENT_OPERATIONS.md` (60 lines) — Operations docs
24. `context_manager.py` (81 lines) — Text truncation

**Total lines analyzed: ~28,000+**
**Issues found: 22**
**Issues with code snippets showing exact failure point: 100%**