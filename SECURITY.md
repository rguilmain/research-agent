# Security

Under active construction; **not ready for public deployment**.
Production startup is intentionally blocked until persistent quotas, Turnstile admission,
budget reservations, abuse limits and launch security acceptance tests are complete.

Identity (M4a, development only): GitHub OAuth with state and PKCE, opaque session tokens
stored only as HMAC hashes, HttpOnly SameSite=Lax cookies (`__Host-` and Secure over
HTTPS), per-session CSRF tokens plus Origin checks on every unsafe request, same-origin
relative redirect targets, and query strings removed from access logs. GitHub access
tokens are used once to read the profile and never stored.

Known gap: any cookieless browser can create anonymous session rows via `GET /api/v1/me`;
there is no rate limit yet, and cleanup is manual and batched.

Do not include credentials or private conversations in public issues.
Use GitHub's private vulnerability reporting if enabled, or contact the maintainer
through their GitHub profile before disclosing a vulnerability.

Never publish .env files. Use separate provider projects, OAuth Apps and keys for
development and production. Rotate keys that might have been built into container images.
Provider billing alerts are not hard application spending controls.
