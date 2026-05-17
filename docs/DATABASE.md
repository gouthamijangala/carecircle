# Database

CareCircle currently uses raw SQL through `db.py`. Keep database access centralized during restructuring.

## Migration Policy

- Put new SQL in `migrations/`.
- Prefer additive migrations.
- Avoid destructive changes without a rollback plan.
- Keep Supabase/PostgreSQL constraints aligned with code-level state machines.

## Current High-Use Tables

- `patients`
- `profiles`
- `incoming_messages`
- `pending_tasks`
- `media_uploads`
- `medications`
- `lab_reports`
- `alerts`
- `agent_approvals`
- `care_appointments`
- `care_notes`
- `caregiver_observations`

