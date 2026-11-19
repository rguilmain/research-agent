# Fly.io deployment (not enabled yet)

Do not deploy this foundation publicly. Complete docs/PLAN.md launch gates first.

**Nothing in the phases below has been executed.** This is the M9 runbook, written for review.
Do not run a command until the owner has approved the paid infrastructure (Fly Managed Postgres
and the Machines) and the launch gates have been signed off.

## Current state — read this first

- App `research-agent-rosy-dawn-8813` exists in region `ewr`, hostname
  `research-agent-rosy-dawn-8813.fly.dev`. It was created with `fly launch`.
- It was deployed once from the original, unsafe `fly.toml` (commit `49e60db`), which set no
  `APP_ENV`. The running image therefore used the settings default (`APP_ENV=development`,
  `DEMO_MODE=true`). A probe of `/api/v1/config` returned
  `{"demo":true,"login_enabled":false,"version":"0.1.0","turnstile_site_key":null}`, and
  `fly secrets list` was empty: a public, unauthenticated demo endpoint with no database, no
  Turnstile and no provider keys.
- That exposure was closed on 2026-11-19 with `fly scale count 0`, which destroyed both
  Machines. The app now reports `Image -` and the hostname does not serve. The **app object and
  its configuration still exist**; only the Machines were removed.
- `fly.toml` and the deploy workflow have since been corrected on the branch
  `feat/fly-deploy-gating` (PR #21): `APP_ENV=production`, a `/healthz` liveness check, a
  `research-agent-migrate` release command, a manual approval-gated workflow and offline guard
  tests. None of that is deployed.
- No Fly secret, no database and no GitHub `production` environment exist yet.

Two lessons to carry forward: `fly launch` deploys the current `fly.toml` immediately, so review
it before the first launch; and scale-to-zero means an exposed app is one request away from
waking, not merely "stopped".

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

```powershell
Set-Location C:\Users\richg\dev\research-agent
gh api --method PUT 'repos/rguilmain/research-agent/environments/production'
gh api --method PUT 'repos/rguilmain/research-agent/environments/production' `
  -f 'reviewers[][type]=User' -F 'reviewers[][id]=<your-github-user-id>'
```

Then store the deploy token **on the environment**, not the repository, so it is released only
to the approved job:

```powershell
fly tokens create deploy --app research-agent-rosy-dawn-8813
# Paste the printed token when prompted; gh never echoes it back.
gh secret set FLY_API_TOKEN --env production --repo rguilmain/research-agent
```

The `reviewers` field requires the reviewer to be a repository collaborator or org member.
Confirm the API response lists the reviewer before relying on the gate.


## Phase 2 — Managed Postgres (paid; needs approval)

Confirm the current syntax with `fly mpg create --help` first; this interface is newer and moves
quickly. The shape is:

```powershell
fly mpg create --name research-agent-db --region ewr --plan <plan>
fly mpg attach <cluster-id> --app research-agent-rosy-dawn-8813
```

`fly mpg attach` sets `DATABASE_URL` as an app secret. If it does not, copy the connection string
from `fly mpg status <cluster-id>` and set it by hand. Managed Postgres is a separate product
from the older unmanaged `fly postgres`; do not mix them.

Check two things before going further:

- The per-thread locking in `repository.py` needs direct/session-mode connections. Confirm the
  attached URL does not point at a transaction-mode pooler, or the advisory locks will not work.
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

