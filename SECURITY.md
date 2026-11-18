# Security

Under active construction; **not ready for public deployment**.
Production startup is intentionally blocked: a production configuration must satisfy every
readiness check below, and two security gates are still unimplemented (deployment backup and
restore have not been tested, and the launch security acceptance tests have not been run
against a production-like environment).

Identity (M4a, development only): GitHub OAuth with state and PKCE, opaque session tokens
stored only as HMAC hashes, HttpOnly SameSite=Lax cookies (`__Host-` and Secure over
HTTPS), per-session CSRF tokens plus Origin checks on every unsafe request, same-origin
relative redirect targets, and query strings removed from access logs. GitHub access
tokens are used once to read the profile and never stored.

Known gap: any cookieless browser can create anonymous session rows via `GET /api/v1/me`.
Anonymous admission is bounded by shared per-address rate limits, a per-session daily quota, a
Cloudflare Turnstile check and the global daily budget, but a distributed attacker with many
addresses is not fully stopped, and a process without a database (development only) is not
limited at all. Expired sessions, threads and counters are removed by a scheduled cleanup pass.

Do not include credentials or private conversations in public issues.
Use GitHub's private vulnerability reporting if enabled, or contact the maintainer
through their GitHub profile before disclosing a vulnerability.

## Production readiness checks

`research-agent` refuses to start with `APP_ENV=production` until every one of these holds.
A production configuration must have: `DEMO_MODE=false`, a `DATABASE_URL`, `OPENAI_API_KEY`
and `TAVILY_API_KEY`, real `GITHUB_CLIENT_ID`/`GITHUB_CLIENT_SECRET`, a `SESSION_SECRET` of at
least 32 characters, an `https` `PUBLIC_BASE_URL` that names the public host rather than a
loopback address, real (non-test) Turnstile keys, a default model that is enabled, and at least
one enabled model that anonymous callers may select. Any secret value is kept out of the error
message. The two gates below are unimplemented, so the refusal stands regardless of
configuration; removing them is a reviewed code change in `research_agent/config.py`.

- Deployment backup and restore have not been tested.
- The launch security acceptance tests have not been run against a production-like environment.

Never publish .env files. Use separate provider projects, OAuth Apps and keys for
development and production. Rotate keys that might have been built into container images.
Provider billing alerts are not hard application spending controls.
