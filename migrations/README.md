# Migrations

Store Supabase/PostgreSQL migration scripts here.

Recommended naming:

```text
YYYYMMDD_HHMM_short_description.sql
```

Rules:

- Prefer additive migrations.
- Include verification SQL at the bottom as comments.
- Do not commit secrets or environment-specific connection strings.

