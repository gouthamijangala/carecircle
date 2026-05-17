# Security Policy

CareCircle handles sensitive caregiver and medical-adjacent data. Treat all local data, uploaded files, phone numbers, and API keys as private.

## Secrets

- Do not commit `.env`.
- Use `.env.example` for documentation only.
- Rotate any key that was accidentally exposed in logs, screenshots, or commits.

## Reporting Security Issues

For now, report security issues privately to the project maintainer. Do not open public issues containing credentials, patient data, medical documents, or phone numbers.

## Safe Defaults

- Notification dispatch should remain audit-only in development unless explicitly configured.
- Uploaded media and generated logs are ignored by Git.
- Production deployments should use managed secret storage instead of committed environment files.

