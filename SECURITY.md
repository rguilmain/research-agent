# Security

Under active construction; **not ready for public deployment**.
Production startup is intentionally blocked until authentication, persistent quotas,
budget reservations, CSRF protection, and ownership tests are complete.

Do not include credentials or private conversations in public issues.
Use GitHub's private vulnerability reporting if enabled, or contact the maintainer
through their GitHub profile before disclosing a vulnerability.

Never publish .env files. Use separate provider projects and keys for development
and production. Rotate keys that might have been built into container images.
Provider billing alerts are not hard application spending controls.