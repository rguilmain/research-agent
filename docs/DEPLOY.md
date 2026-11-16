# Fly.io deployment (not enabled yet)

Do not deploy this foundation publicly. Complete docs/PLAN.md launch gates first.

Once ready: install/sign into flyctl, choose an available app name in region ord,
provision approved Managed Postgres, configure DATABASE_URL and provider/OAuth/
Turnstile/session secrets through Fly secrets, and run migrations as a release command.
Never put credentials in fly.toml, Docker build args, images, or Actions logs.

Use a separate GitHub OAuth app per environment and register the exact same-origin
/auth/github/callback URL. Verify Turnstile hostname/action server-side. Production
deployment from GitHub Actions must use a protected environment with approval and
an app-scoped Fly deploy token, and must run only after successful CI.

Test backups/restores, quotas under concurrency, crash recovery of reservations,
proxy header trust, cancelled/abandoned runs, ownership and deletion before launch.