# PharmaAgent Operations

Use this as the lightweight rollout checklist for CareCircle PharmaAgent.

## Live Checks

1. Confirm triggers are firing:

```powershell
python monitor_pharma_agent.py
```

Look at `recent_pharma_audit` and `recent_drugs`. A healthy system should show recent `PHARMA_AGENT_DECISION` rows after prescription-photo ingestion, async pipeline medication writes, or manual medication triggers.

2. Check API health:

```powershell
curl http://localhost:8000/health/pharma | python -m json.tool
```

Expected core signals:

```json
{
  "pharma_agent_status": "healthy",
  "rules_loaded": 1,
  "self_learning_enabled": true
}
```

`llm_available` can be `false` while deterministic safety still works. It only affects explanation synthesis.

3. Review feedback:

```powershell
python -c "import db; print(db.get_unprocessed_feedback())"
```

Unprocessed caregiver feedback is used by `PharmaSafetyEngine._apply_self_learning()` to escalate repeated vetoed drug pairs once the configured threshold is reached.

## Tuning

Tune only after reviewing real audit and feedback rows:

- `PHARMA_EXPLANATION_CONFIDENCE_THRESHOLD`: default `0.7`
- `PHARMA_MIN_FEEDBACK_COUNT`: default `5`
- `PHARMA_FEEDBACK_CONFIDENCE_THRESHOLD`: default `0.8`

Lower thresholds increase sensitivity. Higher thresholds reduce noisy escalations.

## Seed Growth

Add new rows to `drug_interactions` only when a source is credible and the pair is clinically meaningful. Prefer:

- `severity='critical'` for avoid/urgent prescriber-review pairs.
- `severity='high'` for strong monitoring/escalation needs.
- `severity='medium'` for routine caution.
- `is_active=true` only after review.

Keep the deterministic rule table as the source of truth. LLM output can explain rules, but it should not silently create clinical rules.
