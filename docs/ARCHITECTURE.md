# CareCircle Architecture

This document describes the current architecture before the full package migration.

## Runtime Entry

- FastAPI entrypoint: `main:app`
- Chat and command handling: `handlers.py`
- Intent routing: `intent.py`, `router.py`, `intent_embedding.py`
- Database access: `db.py`

## Major Domains

- Media ingestion: `ingestion.py`, `async_pipeline.py`, `extraction_engine.py`, `doc_classifier.py`
- Medication safety: `pharma_agent.py`, `pharma_research.py`, `pharma_tools.py`, `pharma_promotion.py`
- Care coordination: `appointment_manager.py`, `daily_summary.py`, `crisis.py`, `alerting.py`
- LLM access: `llm.py`, `llm_gateway.py`, `llm_policy.py`
- Notifications: `notifications.py`, `notification_dispatcher.py`

## Migration Strategy

The future package layout lives under `carecircle/app/`. During migration, root modules remain compatibility entrypoints so existing imports and scripts keep working.

