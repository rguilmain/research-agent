# Research Agent

A new web-search research assistant replacing the `rag-app` prototype.
Built with **FastAPI, LangChain, React, and MUI**. This is web research, not
retrieval over private documents.

**Status: development foundation, not ready for public deployment.**
Demo streaming works without API keys. The live provider integration is implemented
but has not been tested with paid credentials. Production startup deliberately fails
until the public-launch security gates in [docs/PLAN.md](docs/PLAN.md) are complete.

## Quickstart

Requirements: Python 3.12, uv, Node 22.14+, npm. Start two terminals:

```powershell
Set-Location C:\Users\richg\dev\research-agent\backend
uv sync --locked --group dev
Copy-Item .env.example .env
uv run research-agent
```

```powershell
Set-Location C:\Users\richg\dev\research-agent\frontend
npm ci
npm run dev
```

Open http://localhost:5173. API docs: http://localhost:8000/docs.
The page prominently identifies demo mode; no search or paid model call happens.

For live **local development only**, set `DEMO_MODE=false`, `OPENAI_API_KEY`,
`TAVILY_API_KEY`, and `DATABASE_URL` in `backend/.env`. Wolfram Alpha is optional.
Run the database migrations below before starting the live application.
Do not paste keys into chat, commit them, or bind this unfinished API publicly.

### Optional local persistence (required for the live application)

Demo mode still starts without a database. To enable owner-scoped thread metadata,
durable run records and live-agent checkpoints, start the local-only PostgreSQL service:

```powershell
Set-Location C:\Users\richg\dev\research-agent
docker compose up -d --wait db
Set-Location C:\Users\richg\dev\research-agent\backend
$env:DATABASE_URL='postgresql://research_dev:local-development-only@127.0.0.1:54329/research_agent'
uv run research-agent-migrate
uv run research-agent
```

The credentials above are **only for the loopback-bound development container**.
Migrations are explicit and repeatable; application startup never creates tables.
`uv run research-agent-cleanup` removes up to 100 expired/deleted threads per invocation
and reconciles up to 100 stale runs. It is not scheduled automatically.

**Identity (development only):** the browser gets an anonymous session cookie from
`GET /api/v1/me`; threads are owned by that session. Optional GitHub sign-in (set
`GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET` and `SESSION_SECRET` in `backend/.env`, with
callback `<PUBLIC_BASE_URL>/auth/github/callback`) claims the anonymous session's threads.
When a database is configured, anonymous session creation, research starts and sign-in
attempts are rate limited per hashed client address (429 with `Retry-After`), and daily
questions are quota limited per session and per hashed address; demo mode
without a database is not limited. Anonymous callers get `QUOTA_ANON_DAILY` weighted units
per UTC day and may use only Luna; signed-in callers get `QUOTA_USER_DAILY` (weights come
from the catalog: Luna 1, Terra 3, Sol 5). Over-quota requests get 429 with
`code: quota_exceeded`, and each accepted run reports the remaining allowance in a `quota`
event. Anonymous callers must also pass Cloudflare Turnstile: set `TURNSTILE_SITE_KEY` and
`TURNSTILE_SECRET_KEY` in `backend/.env` (Cloudflare's public always-pass test keys are
`1x00000000000000000000AA` and `1x0000000000000000000000000000000AA`) and the page shows one
widget instead of the question form until the check passes. Verification lasts
`TURNSTILE_VERIFIED_MINUTES` (default and maximum 60) per session; signed-in callers are never
challenged. Budget reservations are not
implemented, so still do not expose this application to other users. Real prompts,
assistant/tool messages
and agent state are persisted for live conversations; demo output is never checkpointed.
Anonymous threads expire after 24 hours; physical deletion requires the cleanup command.
Saved live threads appear in a sidebar that lists, renames, deletes and reopens them; it
hides itself when no database is configured. Demo threads are listed but have no stored
transcript. Interrupted threads are not resumed automatically; use a new thread.

## Models

`DEFAULT_MODEL=gpt-6-luna`. The server-owned allowlist lives in
`backend/src/research_agent/models.toml`; the UI selects among enabled models
per request. Additional models are disabled until account access is verified.
Prices in the catalog are estimates, **not an implemented budget enforcement mechanism**.
Do not enable expensive models before the budget-reservation milestone.

## What works

- Validated settings and model allowlisting; lifespan-managed async HTTP client.
- POST research endpoint with incremental SSE text, tool activity, and source events.
- Heartbeats and per-response event IDs; timeout/disconnect cleanup even during provider silence.
- Async read-only Tavily, Wikipedia, and optional Wolfram tools; bounded agent calls.
- Run-local duplicate prevention, bounded tool inputs/outputs/concurrency, cumulative
  context safety limits, token usage totals and runtime-validated stream events.
- Offline Responses/Tavily HTTP integration fixtures exercising the SDK and agent loop
  (synthetic fixtures, not recordings or verification of live provider access).
- Cancellable frontend, safe Markdown, no remote tracking images, responsive MUI UI.
- Offline backend/frontend tests, linting, type checks, committed lockfiles, CI.
- PostgreSQL migrations, owner-scoped thread CRUD, cross-process per-thread run exclusion,
  durable usage/status records, checkpointed live multi-turn research, and retention cleanup.
- Saved-history browsing: an owner-scoped transcript endpoint, a visible thread sidebar with
  rename/delete, reopening saved threads, live query-derived titles, and automatic cleanup of
  empty threads left by failed run admission.
- Shared PostgreSQL admission limits: per-hashed-address anonymous session and sign-in starts,
  plus per-principal and per-address research starts, refused early with 429 and `Retry-After`.
- Daily weighted question quotas per session, per hashed address and per principal, charged
  only when they fit, refunded when admission fails, and reported in a `quota` stream event.
- Cloudflare Turnstile human verification for anonymous callers, validated server-side with
  fail-closed handling and a bounded per-session window (signed-in users are not challenged).

## Planned launch features

Hybrid access: anonymous users pass Turnstile and get a small daily quota; GitHub
users get more quota and saved-history browsing. PostgreSQL already stores development
threads and runs, development sessions/GitHub sign-in exist, and shared rate limits, daily
quotas and Turnstile are implemented; atomic cost reservations are **not implemented**.
Deployment target: Fly.io. See [the milestone checklist](docs/PLAN.md).

## Checks

See [AGENTS.md](AGENTS.md) for all commands and [SECURITY.md](SECURITY.md) for
security policy. TypeScript 6 is intentionally pinned because the installed
typescript-eslint release rejects TypeScript 7 (see docs/ARCHITECTURE.md).

PostgreSQL tests refuse to run against databases not ending in `_test`, and truncate
their test tables. Create a **dedicated** local test database, then from the backend:

```powershell
Set-Location C:\Users\richg\dev\research-agent
docker compose exec -T db createdb -U research_dev research_agent_test
Set-Location C:\Users\richg\dev\research-agent\backend
$env:TEST_DATABASE_URL='postgresql://research_dev:local-development-only@127.0.0.1:54329/research_agent_test'
$env:REQUIRE_PG='1'
uv run pytest
```

Without `TEST_DATABASE_URL`, only the PostgreSQL-marked tests skip. CI sets `REQUIRE_PG=1`
and supplies a PostgreSQL service, so missing database configuration fails the suite.

With Docker running, `pwsh -File scripts/smoke-docker.ps1` builds the image and
checks non-root execution, packaged model catalog, absence of `.env` under `/app`,
HTTP health, and a complete demo stream. Its temporary container is removed afterwards.
This does not verify the unfinished production security controls or live providers.

No keys from the old prototype were copied. The old repository remains unchanged.

### Optional account visibility check

After configuring your own key locally, from `C:\Users\richg\dev\research-agent\backend`
run `uv run research-agent-check-models`. This explicitly contacts OpenAI's read-only
model metadata endpoints; it does not generate tokens or enable models automatically.
Visibility alone does not verify Responses/reasoning support, generation access or prices.
The command has only been tested with mocked HTTP; no live check has been performed.