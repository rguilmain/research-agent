# Fly.io deployment (not enabled yet)

Do not deploy this foundation publicly. Complete docs/PLAN.md launch gates first.

**Nothing in the phases below has been executed.** This is the M9 runbook, written for review.
Do not run a command until the owner has approved the paid infrastructure (Fly Managed Postgres
and the Machines) and the launch gates have been signed off.

## Current state — read this first

- App `research-agent-rosy-dawn-8813`, org `personal`, region **`iad`** (Ashburn), hostname
  `research-agent-rosy-dawn-8813.fly.dev`. It has no Machines and no secrets.
- The original app was created by `fly launch` and deployed from the unsafe `fly.toml`
  (commit `49e60db`), which set no `APP_ENV`. The running image therefore used the settings
  default (`APP_ENV=development`, `DEMO_MODE=true`). A probe of `/api/v1/config` returned
  `{"demo":true,"login_enabled":false,"version":"0.1.0","turnstile_site_key":null}`, and
  `fly secrets list` was empty: a public, unauthenticated demo endpoint with no database, no
  Turnstile and no provider keys.
- That exposure was closed on 2026-11-19 with `fly scale count 0`, and the app was then
  destroyed and recreated clean in `iad`, so no leftover configuration from the bad deploy
  remains.
- `fly.toml` and the deploy workflow were corrected on the branch `feat/fly-deploy-gating`
  (PR #21): `APP_ENV=production`, a `/healthz` liveness check, a `research-agent-migrate`
  release command, a manual approval-gated workflow and offline guard tests.
- **Managed Postgres is not available in `ewr`.** The app moved to `iad` because the database
  must sit in the app's region. MPG regions are iad, ord, dfw, lax, sjc, yyz, ams, fra, lhr,
  sin, syd, nrt and gru.

Two lessons to carry forward: `fly launch` deploys the current `fly.toml` immediately, so review
it before the first launch; and scale-to-zero means an exposed app is one request away from
waking, not merely "stopped".

### Provisioning progress

| Step | State |
| --- | --- |
| Destroy the unsafe app | Done (2026-11-19) |
| Recreate the app clean in `iad` | Done |
| `fly.toml` region and fail-closed settings | Done (PR #21) |
| GitHub `production` environment | Done |
| Required reviewer on the environment | Done (`rguilmain`) |
| Managed Postgres cluster | **Not created — owner deferred** |
| Fly app secrets | Not set |
| `FLY_API_TOKEN` environment secret | Not set |
| First deploy | Not run |
| Backup/restore test, acceptance tests | Not run |

The deploy workflow is safe to leave in place: it is `workflow_dispatch` only and its job would
fail closed while `APP_ENV=production` is unstartable, so nothing can deploy by accident.


## Rules

- Never put credentials in `fly.toml`, Docker build args, images, or Actions logs.
- Use a separate GitHub OAuth app per environment and register the exact same-origin
  `/auth/github/callback` URL.
- Verify Turnstile hostname/action server-side.
- Production deployment from GitHub Actions must use a protected environment with approval and
  an app-scoped Fly deploy token, and must run only after successful CI.
- Test backups/restores, quotas and budget reservations under concurrency, crash recovery of
  reservations, proxy header trust, cancelled/abandoned runs, ownership and deletion before
  launch.

## Secrets the app needs

`APP_ENV=production` is set in `fly.toml`, so startup is refused until every readiness item
holds. The values come from the owner's separate production provider projects. Set them with
`fly secrets set`, never in a file that is committed. Names only; never paste a value into chat,
a commit, or a log.

| Secret | Notes |
| --- | --- |
| `DATABASE_URL` | Fly Managed Postgres connection string. Required by the release command too. |
| `OPENAI_API_KEY` | Production project key. |
| `TAVILY_API_KEY` | Production project key. |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | Production OAuth App; callback `https://research-agent-rosy-dawn-8813.fly.dev/auth/github/callback`. |
| `SESSION_SECRET` | At least 32 characters; generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. |
| `TURNSTILE_SITE_KEY` / `TURNSTILE_SECRET_KEY` | Real Cloudflare keys; production readiness rejects the public test keys. |
| `PUBLIC_BASE_URL` | `https://research-agent-rosy-dawn-8813.fly.dev` (https, non-loopback, no trailing slash). |

`TRUST_PROXY_HEADERS=true` is also required in production, because Fly sets `Fly-Client-IP` and
the rate limits key on the client address. This has not been verified against a real Fly ingress
(see the M5a notes); confirm the header arrives before trusting it.

## Phase 1 — GitHub environment and approval gate (no Fly cost)

Done on 2026-11-19. The `production` environment exists with `rguilmain` as a required reviewer:

```powershell
gh api --method PUT 'repos/rguilmain/research-agent/environments/production'
gh api --method PUT 'repos/rguilmain/research-agent/environments/production' `
  -f 'reviewers[][type]=User' -F 'reviewers[][id]=3230432'
```

Remaining: store the deploy token **on the environment**, not the repository, so it is released
only to the approved job. Do this when the first deploy is imminent:

```powershell
fly tokens create deploy --app research-agent-rosy-dawn-8813
# Paste the printed token when prompted; gh never echoes it back.
gh secret set FLY_API_TOKEN --env production --repo rguilmain/research-agent
```

The `reviewers` field requires the reviewer to be a repository collaborator or org member.
Confirm the API response lists the reviewer before relying on the gate.


## Phase 2 — Managed Postgres (paid; deferred by owner)

Not created yet. When approved, confirm the current syntax with `fly mpg create --help` first.
The shape is:

```powershell
fly mpg create --name research-agent-db --region iad --plan <plan> --pg-major-version 17
fly mpg attach <cluster-id> --app research-agent-rosy-dawn-8813
```

`fly mpg attach` adds a secret named `DATABASE_URL` by default (override with
`--variable-name`). If it does not, copy the connection string from
`fly mpg status <cluster-id>`. Managed Postgres is a separate product from the older unmanaged
`fly postgres`; do not mix them.

Plan options and prices (Fly's pricing page, November 2026):

| Plan | Size | Per month |
| --- | --- | --- |
| Basic | shared-2x, 1 GB | $38 |
| Starter | shared-2x, 2 GB | $72 |
| Launch | performance-2x, 8 GB | $282 |
| Scale | performance-4x, 32 GB | $962 |
| Performance | performance-8x, 64 GB | $1,922 |

Storage is billed at $0.28 per GB per month. `--pg-major-version` accepts 16 (the default) or 17,
and a cluster cannot be upgraded in place, so choose deliberately.

The application is already compatible with MPG's bundled connection pooling: `db.py` sets
`prepare_threshold=0` (no server-side prepared statements), the run-admission lock is
transaction-scoped, and the maintenance lock is held on a single pooled connection for the
duration of the pass. Confirm after attaching by checking that `/readyz` reports ok and that a
live run acquires its lock.

Also check:

- Backups: confirm the cluster's backup schedule and retention, and keep the restore
  instructions with this runbook.

## Phase 3 — Secrets and a fail-closed check

```powershell
fly secrets set --app research-agent-rosy-dawn-8813 `
  OPENAI_API_KEY='<value>' TAVILY_API_KEY='<value>' `
  GITHUB_CLIENT_ID='<value>' GITHUB_CLIENT_SECRET='<value>' `
  SESSION_SECRET='<value>' PUBLIC_BASE_URL='https://research-agent-rosy-dawn-8813.fly.dev' `
  TURNSTILE_SITE_KEY='<value>' TURNSTILE_SECRET_KEY='<value>' `
  TRUST_PROXY_HEADERS='true'
```

Set `DATABASE_URL` last, after the others, so no intermediate state runs the release command
without it. `fly secrets set` restarts Machines, so expect a deploy attempt.

Prove the refusal still works, without touching the real app, by exercising the same code path
`fly.toml` activates:

```powershell
Set-Location C:\Users\richg\dev\research-agent\backend
$env:APP_ENV='production'; $env:DATABASE_URL='postgresql://u:p@h/db'
uv run research-agent
# Expect: "Production startup is blocked until the security launch gates pass: ..."
```

A local refusal plus a green deploy is the evidence that the gate is live.

## Phase 4 — First deploy (approval-gated)

Merging PR #21 does **not** deploy. Run the workflow by hand and approve it:

```powershell
gh workflow run fly-deploy.yml --repo rguilmain/research-agent --ref main
gh run list --workflow fly-deploy.yml --repo rguilmain/research-agent --limit 3
```

Expect the release command to run migrations and set up the checkpointer first, then the new
version to start, then the `/healthz` check to pass. Because `min_machines_running = 0`, the
Machine stops again once the check window closes; that is intended.

Verify, in order:

1. `https://research-agent-rosy-dawn-8813.fly.dev/healthz` returns `{"status":"ok"}`.
2. `/api/v1/config` returns `"demo":false` and a non-null `turnstile_site_key`. A `demo:true`
   response means the deploy is still wrong; stop and run `fly scale count 0` immediately.
3. `/readyz` returns ok, which proves `DATABASE_URL` reached the app and the pool opened.
4. Sign-in round trip with the production OAuth App.
5. The anonymous path: the Turnstile widget appears, the question form stays hidden until it
   passes, and a first question streams a live answer.
6. `fly logs` shows no key-bearing URLs, no prompts and no cookies (the logging rules in
   AGENTS.md).

## Phase 5 — Gates before the block can be removed

The production refusal is deliberately hard-coded in `config.py`. Lifting it is a reviewed code
change, and only after:

- A backup is taken and restored into a scratch database, with the restored row counts checked.
- The launch acceptance tests run against this environment: ownership returns 404 for another
  user's thread, CSRF is enforced, quotas and the budget refuse over-limit requests, proxy header
  trust is confirmed, cancelled and abandoned runs recover, and deletion is verified.
- `CLEANUP_INTERVAL_MINUTES` is revisited: with scale-to-zero the scheduler runs only while a
  Machine is awake, so timely cleanup needs a separate trigger (a scheduled Machine, or a
  `research-agent-cleanup --max-batches N` job) before launch.

## Rollback

- Take it offline: `fly scale count 0 --app research-agent-rosy-dawn-8813`.
- Restore the previous image: `fly releases --app research-agent-rosy-dawn-8813`, then
  `fly deploy --image <previous-image>`.
- A failed release command aborts the deploy before traffic moves; read `fly logs` for the
  migration error and fix forward.

