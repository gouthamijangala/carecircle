# Repository Structure

CareCircle is being migrated toward a package-based layout while preserving root-level compatibility wrappers for existing scripts and local workflows.

## Important Root Files

- `main.py`: current FastAPI app entrypoint.
- `db.py`: current database facade and raw SQL access layer.
- `ingestion.py`: current media ingestion orchestration.
- `handlers.py`: current chat/message handler.
- `pharma_agent.py`: current PharmaAgent orchestration.
- `README.md`, `.env.example`, `pyproject.toml`, `requirements.txt`: GitHub-facing project metadata.

## Organized Folders

- `carecircle/`: future package layout and compatibility wrappers.
- `docs/`: architecture, operations, audits, and verification notes.
- `migrations/`: Supabase/PostgreSQL migration scripts.
- `scripts/verify/`: verification script implementations.
- `tests/fixtures/`: sample media fixtures used for local verification.

## Compatibility Policy

Root verification scripts and root migrated modules remain as small wrappers until all runtime imports have moved to `carecircle.app.*`.

