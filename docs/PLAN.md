# Implementation plan and handoff

## Approved product decisions

- Sibling project: C:\Users\richg\dev\research-agent, public GitHub repo rguilmain/research-agent.
- Web-search research agent; no document upload, vector database, or RAG branding.
- Hybrid access: anonymous Turnstile + small quota; GitHub login + higher quota + saved history.
- Fly.io hosting, PostgreSQL; no paid resources provisioned without approval.
- Default GPT-6 Luna (`DEFAULT_MODEL=gpt-6-luna`), runtime model picker with server allowlist.
- After the replacement is complete, add a notice to rag-app; do not archive it.

## Progress (check only what is verified)

- [x] M0: initialize local repo, Python/React scaffolds, lockfiles, license, agent instructions.
- [x] M1a: settings, model catalog, async tools, create_agent construction, offline tests.
- [x] M1b: fake-model tool-loop tests, mocked Responses/Tavily integration, bounded parallelism,
  duplicate-call prevention, per-tool input/output limits, account-access check command.
  Verified using actual SDK/create_agent loops with synthetic HTTP fixtures (stronger
  than fake-model-only tests). Access-check command verified with mocked HTTP only.
- [x] M2a: streaming API and UI, cancellation control, safe Markdown and structured sources.
- [ ] M2b: verified provider-stream adapter tests, disconnect and timeout tests, heartbeat,
  runtime event validation, stable event ids, run state, input/context limits, usage accounting.
  Completed subset: heartbeat, per-response event IDs (not replay/resume), silent-provider
  disconnect polling, timeout/cancellation cleanup, unexpected-EOF detection, concurrent-run
  tests, and synthetic LangChain message-adapter tests excluding tool/reasoning text.
  Also verified: synthetic Responses/Tavily wire streams through the actual SDK/agent,
  runtime schema validation on server/browser, aggregate usage and per-call/cumulative
  context bounds. Still required: durable run state and partial-usage crash recovery (M3/M5).
- [ ] M3: PostgreSQL (dev/CI/prod), SQLAlchemy + Alembic, LangGraph checkpointer,
  thread APIs, owner checks, retention cleanup. Serialize concurrent writes per thread.
  - [x] M3a: local/CI PostgreSQL, explicit packaged migrations, owner-scoped metadata APIs,
    durable run/usage records, checkpointed live turns, per-thread exclusion, manual cleanup.
    Verified locally and in CI run 43825899521 (b535d8e). Production DB remains M9.
  - [x] M3b: history presentation and retention scheduling. Resume of interrupted threads
    intentionally stays unimplemented; a new thread is required.
    - [x] M3b (partial): saved-history API and sidebar, reopening saved threads,
      query-derived titles, cleanup of newly created empty threads after failed run
      admission, and a bounded multi-batch cleanup option. Covered by the CI PostgreSQL
      suite (see run `44181979216`).
    - [x] M3b-final: in-process scheduled cleanup every `CLEANUP_INTERVAL_MINUTES`
      (default 15, 0 disables) with a session advisory lock (`MAINTENANCE_LOCK`) so only one
      process cleans up at a time; the manual command takes the same lock. Failure isolation
      logs the exception class only and keeps the schedule alive. No new dependencies,
      migrations or endpoints. See the M3b-final session notes.
- [ ] M4: server-side GitHub OAuth (state, PKCE if supported), opaque hashed sessions,
  secure cookies, CSRF, restricted redirects, session rotation/logout, anonymous thread claim.
  - [x] M4a: implemented with migration 0002_identity; landed as commit `2d59afc` with CI run
    `44181979216` observed successful (backend with REQUIRE_PG=1, frontend, Docker smoke).
    The local PostgreSQL suite and Docker smoke had already passed locally; a real GitHub
    OAuth round trip has never been performed. See the M4a session notes.
- [ ] M5: abuse protection and cost control. Done: shared SQL rate limits (M5a), daily
  weighted quotas (M5b-1), Turnstile human verification (M5b-2), the daily budget with
  concurrency caps (M5c) and scheduled cleanup (M3b-final). Remaining launch work is the
  deployment gates.
  - [x] M5a: shared PostgreSQL fixed-window admission limits keyed by hashed client address,
    session principal and login start, returning 429 problem JSON with `Retry-After` before
    any row is created. Landed as commit `5eca4cb`; CI run `44187038198` observed successful
    (backend with REQUIRE_PG=1, frontend, Docker smoke). Turnstile, daily quotas, the `quota`
    SSE event, concurrency caps and budget reservations remain open.
  - [x] M5b-1: daily weighted quotas (`quota_usage` in migration `0004_quotas`), the `quota`
    SSE event, anonymous model tiering, and quota UI. Turnstile moves to M5b-2.
    - Design: a conditional upsert charges units only when they fit, so a refused request
      never consumes quota; anonymous callers are checked per session **and** per hashed
      address; a request refused during admission is refunded.
    - Not covered: no refund when a run fails after admission, reset is UTC midnight, and a
      demo process with a database also charges quota.
  - [x] M5b-2: Cloudflare Turnstile server validation with the browser widget and a bounded
    "verified human" window stored on the anonymous session. Verified offline with respx and
    the documented public test keys only; no real Cloudflare request was made. Landed as
    commit `959bdda`; CI run `44199641545` later observed successful (backend with
    REQUIRE_PG=1, frontend, Docker smoke).
  - [x] M5c: global daily budget with reserve-then-settle accounting, crash expiry, a
    fail-closed admission floor, and shared run concurrency caps. Verified locally with
    synthetic providers and a real PostgreSQL database; no live provider comparison. Landed
    as commit `327357c`; CI run `44206137733` later observed successful.
- [ ] M6: multi-turn UX, saved history/sidebar, model access tiers, quota badge,
  sign-in, Turnstile, persisted theme, accessibility and privacy/data deletion screens.
- [ ] M7: same-origin production static serving, non-root multi-stage Docker image,
  container build/smoke/security tests. Do not remove production startup block yet.
  - [x] M7a: the image builds the frontend and the API serves it same-origin (`STATIC_DIR`),
    with `Content-Security-Policy`, `Permissions-Policy`, COOP and HTTPS-only HSTS on every
    response, immutable caching for hashed assets, `/docs` disabled while serving the UI, and
    container smoke checks for the served shell, CSP and bundle. See the M7a session notes.
    Remaining M7 work: production-only startup readiness checks and container image scanning.
  - [x] M7b: production startup readiness is now a two-part gate. `production_readiness_errors`
    reports every configuration gap (`DEMO_MODE`, `DATABASE_URL`, provider keys, OAuth, session
    secret length, https non-loopback origin, real Turnstile keys, an enabled default model and
    an enabled anonymous-eligible model), and the validator adds two unimplemented security
    gates as an in-code constant so editing the public tuple cannot unblock startup. Container
    image scanning was deliberately not added. See the M7b session notes.
- [ ] M8: Postgres integration tests, Playwright offline full-stack tests,
  dependency/security checks, branch protection and approved deployment workflow.
  - [x] M8a: Playwright browser smoke test against the built UI and the real API in demo mode
    (`frontend/e2e`, `npm run e2e`), plus a new `e2e` CI job. It found and fixed the preview
    origin bug recorded under M7a. See the M8a session notes. Remaining M8 work: dependency
    and security checks, branch protection, and the approved deployment workflow.
- [ ] M9: Fly.io app + Managed Postgres, release migrations/checkpointer setup,
  secrets, health checks, HTTPS, backups/restore test, staging security smoke test.
- [ ] M10: launch documentation/screenshot, threat model, privacy/retention policy,
  runbook and release. Remove production block ONLY after security acceptance tests pass.
- [ ] M11: update rag-app README to point to the completed replacement.

## Next agent: begin here

M4a is landed (commit `2d59afc`, CI run `44181979216` observed successful), M5a is landed
(commit `5eca4cb`, CI run `44187038198` observed successful), and M5b-1/M5b-2/M5c are landed
with CI observed successful (`44191867526` on `7c936dc`, `44199641545` on `959bdda`,
`44206137733` on `327357c`; the three M7a commits landed together as `0445593` with run
`44214853602`, and the preview-origin fix plus the M8a browser test landed as `765c13c` with
run `44217995408` including the new e2e job). M3b includes in-process scheduled cleanup, M7a
serves the built frontend same-origin with security headers, M7b gates production startup on a
readiness checklist plus two unimplemented security gates, and M8a adds a Playwright browser
smoke test.

1. Launch gates before any public deployment: finish M8 (dependency/security checks, branch
   protection, approved deployment workflow), then M9 (production database and Fly.io
   provisioning, with approval) and M10 (go-live checklist). Production startup is gated on
   the M7b readiness checklist plus deployment backup/restore testing and the launch
   acceptance tests; container image scanning is not planned (owner decision).
2. Optional next slice: M6 multi-turn UX polish (persisted theme, quota badge placement,
   accessibility and privacy/data-deletion screens). Resume of interrupted threads stays
   unimplemented.
3. Remaining open Dependabot PR is #11 (jsdom 31), which requires Node >= 22.22.2 versus
   the documented Node 22.14 baseline. Do not merge until the Node baseline is raised
   deliberately; never bypass engines.
4. Keep README truthful; a demo foundation is not a production-ready application.

### M7b production readiness session — November 17, 2026

- Replaced the unconditional production refusal with a gated one. `production_readiness_errors`
  now also checks `DEMO_MODE`, `DATABASE_URL`, both provider keys, `SESSION_SECRET` length,
  a non-loopback `https` `PUBLIC_BASE_URL`, an enabled `DEFAULT_MODEL` and an enabled
  anonymous-eligible model. It deliberately does not require every catalog model to be enabled,
  because the documented rule is that one optional unavailable model must not block launch.
- Two unimplemented gates (deployment backup/restore, launch acceptance tests) are added by the
  validator from a private constant, so reassigning the exported tuple does not unlock
  production; removing them requires a reviewed code change. Production is therefore still
  refused today, and now it also reports exactly why.
- Moved `LOOPBACK_HOSTS` into `config.py` and imported it in `auth.py`, because a readiness
  check needs it and `auth` importing `config` would otherwise create a cycle.
- Corrected stale public claims found during review: `SECURITY.md` no longer says production is
  blocked on scheduled cleanup or that there is no rate limit, and `docs/ARCHITECTURE.md` no
  longer says scheduled cleanup remains. `SECURITY.md` now documents the readiness checklist.
- Tests (offline, no keys) cover each gap, the complete-configuration case, the two-part policy
  including the reassignment attempt, and that no configured secret appears in the block
  message. Two of them build settings with `model_construct` because the validator refuses
  those invalid combinations before readiness can report them.
- Observed locally: ruff check/format clean; pyright 0 errors; offline pytest 224 passed /
  59 skipped; `REQUIRE_PG=1` pytest 283 passed. No frontend change, so frontend checks were not
  re-run. Container image scanning was intentionally not added.
- NOT verified: no production-like environment exists, so no production startup has ever
  succeeded; backups, acceptance tests and deployment remain unimplemented. CI has not run on
  this commit.
- Limitations: readiness is a startup-time configuration gate, not a runtime health check; the
  anonymous-eligible and default-model checks read the packaged catalog, so editing
  `models.toml` changes them; the refusal message is deliberately terse and lists only gate
  names.

### M8a browser smoke test session — November 17, 2026

- Added Playwright (`@playwright/test` 1.65.1, dev-only; Node requirement `>=20`, compatible
  with the documented 22.14 baseline) with `frontend/playwright.config.ts` and
  `frontend/e2e/smoke.spec.ts`. The config starts the real backend in demo mode on port 8123
  with an explicit environment (`STATIC_DIR` pointing at `dist`, keys and `DATABASE_URL`
  empty), never reuses an existing server, and refuses to run without a build.
- Five tests, all passing locally in an installed Edge via `E2E_CHANNEL=msedge`: clean load
  (no console errors, no CSP violations, no third-party requests), a streamed demo answer
  that never claims to be real research, CSP enforcement (an injected inline script and a
  third-party script are both blocked and `window.__pwned` stays unset), deep links plus 404
  for `/docs` and unknown `/api/` paths, and the expected document security headers.
- The browser run found a real defect that the earlier smoke check hid: the container served
  the UI on its own port while `PUBLIC_BASE_URL` kept the 5173 default, so a browser asking a
  question was rejected with 403. `scripts/smoke-docker.ps1` now picks a free port, sets
  `PUBLIC_BASE_URL` to that origin and posts with it. The browser test passes against the
  fixed configuration.
- Build tooling: Vitest excludes `e2e/**`, `tsconfig.e2e.json` type-checks the specs with DOM
  and node types (`tsc -b` still passes), ESLint ignores `playwright-report` and
  `test-results`, and `npm ci` was verified from a clean `node_modules`.
- CI: a new `e2e` job installs Chromium (`--with-deps`) and runs `npm run e2e`. Both commits
  landed as `765c13c`; run `44217995408` observed successful for backend, frontend,
  docker-smoke and e2e. The follow-up notes commit `c95608d` also passed CI as run `44218310477`.
- Observed locally: browser tests 5 passed; frontend lint, typecheck, 55 unit tests and build
  passed; offline backend pytest 210 passed / 59 skipped; `REQUIRE_PG=1` pytest 270 passed;
  ruff/format/pyright clean; `npm audit --omit=dev` 0 vulnerabilities. `npm audit` (full) now
  reports 6 moderate and 7 high dev-only findings in the existing vite/vitest/typescript-eslint
  chain, all with only semver-major fixes; no forced upgrades were applied.
- NOT verified: the Turnstile widget, GitHub sign-in, live providers and PostgreSQL-backed
  flows were not exercised in a browser; no deployment. The browser run was against an
  installed Edge locally and Chromium in CI, not the container image.
- Limitations: Chromium is the only engine (Firefox/WebKit are out of scope); the tests run the
  backend process rather than the container; demo mode without a database logs an expected
  503 for the owner-scoped history endpoints, which the test tolerates explicitly.

### M7a same-origin static serving session — November 17, 2026

- Implemented: `STATIC_DIR` (rejected unless it contains `index.html`); a new
  `research_agent.web` module with the CSP/permissions constants and `mount_frontend`; the API
  serves Vite's build output with an SPA fallback that never answers for `/api/`, `/auth/`,
  `/assets/` or the reserved health/docs paths; `main.py` adds CSP, `Permissions-Policy`,
  `Cross-Origin-Opener-Policy` and HTTPS-only HSTS to every response, caches hashed `/assets/`
  files immutably, keeps everything else `no-store`, and disables `/docs`, `/redoc` and
  `/openapi.json` while serving the UI (their CDN assets would violate the CSP).
- CSP sources: `script-src`/`frame-src` `https://challenges.cloudflare.com` (documented
  Turnstile requirement), `'unsafe-inline'` styles because MUI/Emotion inject runtime `<style>`
  tags, `img-src` `https://avatars.githubusercontent.com` (the only avatar host the OAuth code
  accepts) plus `data:`, `object-src 'none'`, `base-uri 'none'`, `frame-ancestors 'none'`.
- Docker: the image now has a Node build stage that runs `npm ci` and `npm run build` and
  copies `dist/` to `/app/static` for the non-root runtime user; `scripts/smoke-docker.ps1`
  additionally verifies the served shell, the CSP contents and an immutable hashed bundle.
- Fix from review: `test_scheduled_pass_skips_while_another_process_holds_the_lock` previously
  only checked that a locked pass returned `None`, so it could not fail on a broken lock; it
  now asserts the expired thread row still exists while the lock is held.
- Observed locally: ruff check/format clean; pyright 0 errors; offline pytest 210 passed /
  59 skipped (8 in the new `test_web.py`); `REQUIRE_PG=1` pytest 269 passed; frontend lint,
  typecheck, 55 tests and `npm run build` all passed; `scripts/smoke-docker.ps1` passed. A
  running container served the app shell with the CSP, returned 404 for `/docs` and unknown
  `/api/` paths, and served only an external module script (no inline scripts, so the CSP
  needs no `'unsafe-inline'` for scripts).
- NOT verified: no browser was available, so the CSP was not exercised against a real page
  load and Turnstile was not rendered; CI has not run on this commit yet; the image is still
  not production-ready. No container scan, no live provider call, production block untouched.
- Correction (later the same day): the container served the UI on port 8000 while
  `PUBLIC_BASE_URL` kept its 5173 default, so a real browser posting from `127.0.0.1:8000` was
  rejected with `csrf_failed`; the smoke check hid this by sending a hard-coded 5173 Origin.
  `scripts/smoke-docker.ps1` now allocates a free port, sets `PUBLIC_BASE_URL` to that same
  origin and posts with it, and an offline test asserts both the matching and mismatching cases.
- CI: the three M7a commits were pushed together as `0445593`; run `44214853602` observed
  successful (backend, frontend, docker-smoke).
- Limitations: there is no CSP report endpoint; `style-src 'unsafe-inline'` remains (a nonce
  scheme needs per-request HTML rewriting and Emotion cache configuration); turning off the
  interactive docs means operators must run the process without `STATIC_DIR` to read them.

### M3b-final scheduled cleanup session — November 17, 2026

- Implemented: `CLEANUP_INTERVAL_MINUTES` (default 15, 0 disables, validated 0-1440);
  `Repository.maintenance_lock` (session advisory lock `MAINTENANCE_LOCK`) plus the earlier
  `thread_lock`-style release path; `maintenance.cleanup_batches` shared by the CLI and the
  scheduler; `maintenance.scheduled_cleanup_pass` (returns None when another process holds
  the lock) and `maintenance.periodic_cleanup`; the lifespan starts one cleanup task when a
  database is configured and cancels it before the pool closes (`app.state.cleanup_task`).
- Design choices: no pass runs at startup (startup already calls `recover()`); each pass is
  one bounded batch; a failing pass is logged by exception class only and the schedule keeps
  running (cancellation propagates for shutdown); the CLI takes the same lock and exits with
  a clear message if a pass is already running; no new dependencies, migrations or endpoints.
- Test note: a connection returned to the pool can be handed to the same process again, and a
  PostgreSQL session advisory lock is re-entrant for the session that already holds it, so the
  lock holder now holds an open transaction to pin its connection. Covered by
  `test_maintenance_lock_is_exclusive`, and by `test_scheduled_pass_skips_while_another_process_holds_the_lock`,
  which now asserts the thread still exists while the lock is held (the earlier version only
  checked the pass returned None, so it could have passed with a broken lock).
- Observed locally: ruff check/format clean; pyright 0 errors; offline pytest 202 passed /
  59 skipped (7 in `test_maintenance.py`); `REQUIRE_PG=1` pytest 261 passed (3 new PostgreSQL
  tests); `scripts/smoke-docker.ps1` passed (image safety, packaged migrations, HTTP health,
  demo SSE; the demo container has no database, so no cleanup task starts there).
- CI: landed as commit `b23624a`; run `44210570001` observed successful (backend with
  REQUIRE_PG=1, frontend, docker-smoke). The follow-up notes commit `3fe059c` also passed CI
  as run `44210827935`.
- NOT verified: a multi-worker or multi-machine deployment (the advisory lock is designed for
  it, but only a single process has been exercised outside the CI fixture) and any live
  provider call. No frontend or dependency changes, so frontend checks and `uv sync --locked`
  were not re-run. Production stays blocked.
- Limitations: the schedule lives inside the web process rather than a separate cron; one pass
  drains at most 100 rows per category, so a large backlog needs several intervals or
  `--max-batches` by hand; a pass is skipped (not queued) while another process holds the lock;
  shutdown cancels an in-flight pass, and its retry-safety is inherited from `cleanup`.

### M5c budget and concurrency session — November 17, 2026

- Implemented: migration `0006_budget` (`budget_days` totals plus a `budget_reservations`
  ledger); `budget.py` (`BudgetLedger`, `BudgetStore`, `BudgetExhausted`); catalog money
  helpers (`ceil_usd`, `conservative_model_cost`, `model_call_reservation`) and a tool price
  table in `models.toml`; `Repository.reserve_budget`/`settle_budget`/`settle_open_for_run`/
  `expire_stale_reservations`/`budget_headroom`/`cleanup_budget_reservations`; run concurrency
  caps inside `start_run`; guard-level charging in `ModelGuards`/`ToolGuards`; 503
  `budget_exhausted` and 429 `too_many_active_runs` problems; a `budget_exhausted` stream error.
- Design choices: one conditional upsert raises the day's reserved total only while
  `reserved + spent + amount <= GLOBAL_DAILY_BUDGET_USD`, so parallel workers cannot
  over-reserve; a model call holds the worst case for the prompt estimate, the full output
  allowance and both SDK attempts, then settles the observed cost; missing usage, a failed
  dispatch or a cancellation settles the full hold; a paid tool is charged its flat price even
  when it fails; unpriced tools are never registered and are refused if they are called; the
  admission floor check runs after rate limits and before quotas, so a refused request costs
  nothing; a transaction-scoped advisory lock serializes run admission; only runs with a
  heartbeat inside two minutes count toward the caps; demo mode spends nothing.
- Observed locally: ruff/format/pyright clean; offline pytest 196 passed / 56 skipped
  (18 in `test_budget.py`); `REQUIRE_PG=1` pytest 252 passed (9 in `test_budget_pg.py`);
  frontend lint, typecheck, 55 tests and build passed; `scripts/smoke-docker.ps1` passed.
- NOT verified: any comparison with a real provider invoice, Tavily's per-call price (a local
  unverified estimate), live budget exhaustion, and CI. Production stays blocked.
- Known limitations: these are estimates, not billing guarantees; a retried attempt inside a
  successful call is covered by the hold but settled from the final attempt's usage only; an
  overspend beyond a hold is recorded but cannot be prevented; concurrency depends on
  heartbeat freshness; budget detail rows are pruned after 30 days while day totals remain;
  a call refused for budget is counted in the run's usage ledger as one estimated model call
  (conservative, never under-recorded) even though it was never dispatched.

### M5b-2 Turnstile session — November 17, 2026

- Implemented: migration `0005_human_verification` (`auth_sessions.human_verified_until`);
  `turnstile.py` (fixed `siteverify` endpoint, bounded reads, one retry with the same
  idempotency key, hostname/action checks, redacted failures); `POST /api/v1/turnstile/verify`;
  `Repository.mark_human_verified` (capped at the session expiry, anonymous sessions only);
  `auth.human_verified`; enforcement in `main.research` before rate limits and quota;
  `/me` reports `turnstile_required` and `human_verified_until`; `/api/v1/config` exposes the
  public site key; readiness now requires Alembic revision `0005`.
- Design choices: Turnstile is enabled only when both keys are configured, so every default
  and demo configuration is unaffected; tokens are validated server-side only, and the browser
  never sees the secret; failures (refused token, wrong host/action, timeout, malformed or
  oversized body) all fail closed; a verification lasts `TURNSTILE_VERIFIED_MINUTES` (max 60)
  and never outlives the session cookie; signed-in callers are never challenged.
- Test-key handling: with Cloudflare's documented test secrets the hostname is always
  `example.com` and no `action` is returned, so those two checks are applied only with a real
  secret. Production readiness rejects test secrets and missing keys.
- Frontend: `api/turnstile.ts` (explicit-render loader plus the verified call),
  `components/HumanCheck.tsx` widget with a reset-on-failure path, a `turnstile_required`
  retry flow in `App.tsx`, and new problem-code copy in `api/errors.ts`. No npm dependency
  was added.
- Observed locally: ruff/format/pyright clean; offline pytest 178 passed / 47 skipped;
  `REQUIRE_PG=1` pytest 225 passed; frontend lint, typecheck, 52 tests and build passed
  (bundle 498.45 kB, no warning); `scripts/smoke-docker.ps1` passed with the new
  packaged-migration assertion (non-root, HTTP health, demo SSE).
- NOT verified: a real Cloudflare widget or siteverify call, hostname/action behaviour with a
  real key, and CI. No provider credits were used.
- Blockers/limitations: widget availability depends on Cloudflare; the anonymous client IP is
  sent to Cloudflare as `remoteip`; verification is per session and lasts up to one hour; a
  future CSP must allow `challenges.cloudflare.com`; production stays blocked.

### M5b-1 daily quotas — November 17, 2026

- Implemented: migration `0004_quotas` (`quota_usage` keyed by scope + hashed key + UTC day
  with a `units >= 0` check); `quota.py` (UTC day and reset helpers, per-kind limits, model
  tiering, `consume`/`refund`, `QuotaStore` protocol, `QuotaExceeded`); catalog fields
  `quota_weight` (Luna 1, Terra 3, Sol 5) and `anonymous` (Luna only);
  `Repository.consume_quota`/`refund_quota`/`cleanup_quotas`; a `quota` SSE event; readiness
  now requires Alembic revision `0004`.
- Design choices: a conditional `INSERT ... ON CONFLICT DO UPDATE ... WHERE units + n <=
  limit RETURNING units` charges units only when they fit, so a refused request consumes
  nothing; all keys for one question are charged in a single transaction and roll back
  together; keys are processed in sorted order to avoid deadlocks; anonymous callers are
  limited per session AND per hashed address; a request refused during admission is
  refunded; resets are UTC midnight.
- Enforced before any row is created in `main.research`, after the M5a rate limits and
  before thread creation; over-quota requests get 429 `problem+json`
  (`code: quota_exceeded`) with `Retry-After` in seconds until the next UTC midnight.
  Anonymous callers may select only models with `anonymous = true`.
- Frontend: `api/stream.ts` parses the `quota` event, `api/errors.ts` adds
  `problemCode`/quota copy, `useResearch.ts` exposes the remaining allowance, and `App.tsx`
  shows a small "Daily allowance" chip.
- Observed locally: ruff/format/pyright clean; offline pytest 159 passed / 33 skipped;
  `REQUIRE_PG=1` pytest 192 passed (88% coverage, `quota.py` 100%); frontend lint,
  typecheck, 40 tests and build passed (bundle 495.77 kB, no warning).
- NOT verified: CI, Docker smoke, and any live provider call. Production stays blocked.
- Blockers/limitations: no refund when a run fails after admission; reset is UTC midnight;
  a demo process with a database also charges quota; cleanup remains manual; Turnstile,
  concurrency caps and monetary reservations are still missing.

### M5a rate limiting session — November 17, 2026

- Implemented (commit `5eca4cb`): migration `0003_rate_limits` (`rate_limit_counters` keyed by
  scope + hashed key + aligned hour window, `count >= 0` check); `ratelimit.py` (trusted-proxy
  aware `client_ip`, HMAC `ip_key`/`principal_key`, hour `window_start`, `CounterStore`
  protocol, `enforce`, `RateLimitExceeded`); `Repository.consume_rate_limit` (single
  `INSERT ... ON CONFLICT DO UPDATE ... RETURNING count` upsert, returns `allowed` and
  `retry_after`) and `Repository.cleanup_rate_limits` (bounded, wired into
  `cleanup_identity`); readiness now requires Alembic revision `0003`.
- Enforced before any row is created: anonymous session creation in `auth.session_or_create`
  (`anon_session` per hashed address, because that dependency—not the endpoint—mints the
  session), research starts in `main.research` (`research_session` per principal for every
  caller plus `research_ip` per hashed address for anonymous callers, so clearing cookies
  does not reset the limit), and GitHub login start in `auth_api.github_login`. Over-limit
  requests get 429 `problem+json` (`code: rate_limited`) with `Retry-After`.
- Design choices: fixed aligned hour windows (a boundary burst can briefly exceed the limit);
  counters are incremented before admission so a counted failure still consumes budget;
  keys are HMACs under `SESSION_SECRET`, never raw addresses; `TRUST_PROXY_HEADERS` defaults
  to false and, when enabled, prefers `Fly-Client-IP` and otherwise the rightmost
  `X-Forwarded-For` hop (the only entry a client cannot forge behind one trusted ingress);
  a process without a database is not limited.
- Frontend: `api/errors.ts` `describeFailure` maps 429 to "Too many requests. Please try
  again in a moment.", used by `useResearch.ts` and the identity error path in `App.tsx`;
  new `errors.test.ts`.
- Observed locally: Ruff format/check and pyright clean; offline pytest 144 passed / 25
  skipped; `REQUIRE_PG=1` pytest 169 passed (87% coverage, `ratelimit.py` 100%);
  frontend lint, typecheck, 34 tests and build passed (bundle 495 kB, no warning);
  `scripts/smoke-docker.ps1` passed with the new packaged-migration assertion.
  CI run `44187038198` on `5eca4cb` observed successful (backend with REQUIRE_PG=1,
  frontend, docker-smoke).
- NOT verified: a live Fly ingress (proxy-header trust is untested against real edge
  headers) and any live provider call. No dependency changes, so no `npm ci`/locked re-sync.
- Blockers/limitations: Turnstile, daily quotas, the `quota` SSE event, concurrency caps and
  monetary reservations are still missing; cleanup remains manual; production stays blocked.


### M4a identity session — November 17, 2026

- Implemented (landed as commit `2d59afc`; CI run `44181979216` observed successful):
  migration 0002_identity (principals, hashed sessions, OAuth
  state); `auth.py` (HMAC session/CSRF tokens, Origin checks, cookie names, safe `next`),
  `auth_api.py` (`/api/v1/me`, GitHub login/callback with state + PKCE, logout),
  `github_oauth.py`, access-log query stripping in `logging.py`; thread and research routes
  owned by the session principal, unsafe routes require Origin + CSRF; login atomically
  revokes the anonymous session and claims its live threads; cleanup purges expired
  sessions/OAuth states/empty anonymous principals in bounded batches.
- Frontend: `api/session.ts` (validated `/me`, cached CSRF token, sign-out), CSRF header on
  research/rename/delete, sign-in/out and auth-error UI in `App.tsx`, updated sidebar text.
- Observed locally: Ruff check/format and pyright clean; pytest 130 passed / 19 skipped
  without a database and 149 passed with `REQUIRE_PG=1` against `research_agent_test`;
  frontend lint, typecheck, 31 tests and build passed; `scripts/smoke-docker.ps1` passed
  (image safety, packaged migrations incl. 0002, health, demo SSE with Origin header).
- NOT verified: CI, a real GitHub OAuth round trip (no OAuth App credentials used),
  `npm ci`/`uv sync --locked` re-runs (no dependency changes were made).
- Blockers/limitations: anonymous session rows could be created without limit by any
  cookieless client (addressed by M5a in the following session); cleanup is manual.
  Production stays blocked.
- Process note: the previous agent run looped on `read_files` returning `[outdated]` and on
  repeated check runs; prefer one `Get-Content` read per file and run checks once per change.

### M3b history/sidebar/cleanup session — November 17, 2026

- Dependency triage: merged #6 (Docker uv 0.13.8) and #8 (uv_build >= 0.13.8) together,
  then #5 (uvicorn >= 0.54) and #12 (psycopg >= 3.3.9); main CI passed after each merge
  (observed: 44139913494, 44140109537, 44140305880, plus 44139708672 for #6).
- #13 (SQLAlchemy >= 2.1.8,<2.2) reviewed in an isolated git worktree: locked sync, Ruff,
  pyright and all 90 tests passed against a dedicated `_test` database including the
  migration up/down/up and checkpoint-idempotency test, so it was merged after CI.
- #11 (jsdom 31.0.1) left open and explained on the PR: jsdom 31 requires Node
  `^22.22.2 || ^24.15.0 || >=26`, but local and documented baseline is Node 22.14.0.
- Implemented: `GET /api/v1/threads/{id}/messages` (owner-scoped, 404 for unknown,
  foreign, deleted or expired threads), transcript projection that exposes only user
  questions and each turn's final assistant text with turn-local structured sources,
  query-derived thread titles, deletion of a newly created thread when run admission
  fails (conflict/storage), empty-thread TTL purge, and `research-agent-cleanup
  --max-batches 1..50`.
- Private data is never exposed by the history endpoint: system prompts, reasoning,
  tool-call arguments, raw tool output and full checkpoint channel state are excluded;
  running/unfinished threads return no transcript with `can_continue=false`. Verified by
  unit and API tests asserting the string "SECRET" never reaches the projection.
- Frontend: thread sidebar (permanent desktop / temporary mobile) with list, rename,
  delete confirmation and paging; multi-turn conversation rendering; model locked while a
  saved thread is open; explicit "Start a new thread" state when `can_continue` is false;
  Markdown is now lazily imported, which removed the >500 kB bundle warning.
- Local checks observed: backend `uv sync --locked`, Ruff check/format, pyright and 97
  tests passed with `REQUIRE_PG=1` (93% coverage); frontend `npm ci`, lint, typecheck, 21
  tests and build passed; production `npm audit` clean; `scripts/smoke-docker.ps1` passed
  (non-root, packaged catalog/migrations, no `/app` `.env`, HTTP health, demo SSE).
- No live provider calls, no keys, no paid infrastructure, no deployment. Production
  startup remains blocked. Retention cleanup scheduling and thread resume remain open.
- CI observed successful for this session's commit 7b7b46a in run 44142796263 (backend
  with required PostgreSQL tests, frontend, Docker smoke). Dependency merges each passed
  their own main runs before it.

## Access and model policy to implement

- Anonymous: 3 questions/day, Luna only, verified-human cookie up to 1 hour;
  anonymous chat expires after 24 hours. Enforce both cookie and hashed-IP limits,
  not a combined key that allows bypass by resetting cookies.
- Signed in: 25 weighted units/day, saved history, server-approved model tiers.
  Default weights: Luna 1, Terra 3, Sol 5 (configurable; not cost estimates).
- Global daily budget: $5 configured in deployment, including model AND paid tools.
  Reserve bounded maximum costs atomically BEFORE each paid operation, settle actual
  usage, persist unresolved reservations after crashes, reconcile/expire conservatively.
- Track cached-input/reasoning tokens correctly; never double-count output usage.
  A local estimate is not a provider billing guarantee. Fail closed on missing prices.
- Bound context, output, retries, tool calls, concurrency, search depth, response sizes.
  Account for every model call's cumulative context, not just the initial prompt.
- Optional providers/models stay disabled until integration and pricing are verified.

## API direction

Current: GET /healthz, /readyz, /api/v1/config (includes the public `turnstile_site_key`),
/api/v1/models; POST /api/v1/research; POST /api/v1/turnstile/verify (session-owned);
GET/POST /api/v1/threads; GET/PATCH/DELETE /api/v1/threads/{id} (session-owned);
GET /api/v1/me (creates an anonymous session if absent; returns kind, user, csrf_token,
login_enabled, turnstile_required, human_verified_until);
GET /auth/github/login and /auth/github/callback; POST /auth/logout.
Unsafe routes require Origin plus X-CSRF-Token. The OAuth callback is on the public origin.

SSE: thread, run_started, step, token, sources, usage, quota, done, error. All are now
implemented. Never stream raw chain-of-thought or secret
tool arguments. Frontend must treat EOF without done as failure.

## Security launch gates

Production must reject demo/test bypasses, missing OAuth/Turnstile/session secrets,
insecure origins, and unpriced enabled models. Ownership tests return 404 for other
users' resources. CSRF applies to unsafe cookie-authenticated requests. Use opaque
session tokens (hash at rest), Secure/HttpOnly/SameSite cookies, state replay defenses,
relative next paths only. Trust proxy headers only behind a verified Fly ingress;
in-process rate limiting alone is insufficient across multiple machines.

Treat retrieved content as hostile. Sources must come from actual tool evidence;
filter unsafe URLs, block tracking images, sanitize Markdown. No arbitrary browser,
code execution, local-file tools, or unrestricted URL fetcher in this release.

Persist only required user profile data; never retain GitHub access tokens if not needed.
Log request IDs/error classes, not queries, OAuth codes, cookies, or key-bearing URLs.
Tracing is opt-in because it can export private conversation content.

## External setup / blockers

### M3a landing verification — November 16, 2026

- Committed/pushed M3a as 9cbb7fb; reviewed all 32 staged paths, whitespace and credential
  patterns. No secrets, generated output or unrelated project files were committed.
- Initial CI run 43825663074 failed four Ruff import-order checks although Windows passed:
  an empty local `backend/alembic` directory affected import classification. Explicitly
  classified Alembic as third-party and normalized imports in b535d8e; no runtime changes.
- CI run 43825899521 on b535d8e observed successful for backend, frontend and Docker smoke.
  Logs confirm 90 backend tests (PostgreSQL tests executed, not skipped), 15 frontend tests,
  image safety/packaged migration checks, HTTP health and complete demo SSE.
- Repeated local locked sync, Ruff check/format, pyright and PostgreSQL suite passed
  (90 tests, 93% coverage). Without a database: 76 passed / 14 skipped. npm ci, lint,
  typecheck, 15 tests, build and production dependency audit passed (zero production findings).
  Docker smoke and explicit production startup rejection also passed locally.
- Existing seven moderate dev-only audit findings and >500 kB bundle warning remain.
  A failed run admission can leave a newly created empty thread until retention cleanup;
  no history UI or scheduled cleanup was added. Authentication, quotas, monetary reservations,
  production infrastructure and launch acceptance tests remain blockers.
- Scope ends with landing M3a. No live provider calls, paid infrastructure or deployment.

### M3a implementation — November 16, 2026

Historical implementation-session notes below predate the landing verification above.

- Added SQLAlchemy/Alembic, psycopg 3 and PostgreSQL checkpointer; refreshed `backend/uv.lock`
  without changing the existing LangGraph versions. Local PostgreSQL 17.7 compose service
  binds only to 127.0.0.1:54329 with explicitly development-only credentials.
- Packaged explicit migrations and maintenance commands; startup creates no schema and
  checks migration versions. Production startup remains unconditionally blocked.
- Added owner-scoped metadata CRUD with 404 isolation, server model validation, bounded
  pagination, immutable thread model/mode, per-thread advisory locks, row-locked run
  admission and active-run uniqueness. Local identity is shared, NOT real authentication.
- Live agents persist strict-serialized checkpoints and accept a thread ID. UI keeps its
  server-issued thread in memory, supports New thread, and discards interrupted threads.
  Demo metadata is labeled and demo answers are never checkpointed.
- Model estimates are durable before dispatch; observed final usage replaces estimates.
  Failed/cancelled usage remains flagged, stale unlocked runs become abandoned, cleanup
  removes checkpoint state and metadata. No monetary reservations or retry billing claims.
- Local application migration command ran twice successfully; cleanup command completed.
  Alembic upgrade/downgrade/upgrade and checkpointer setup idempotency tested on a dedicated
  `_test` database. Test suite refuses non-test database names; CI now provisions Postgres
  and requires its tests. CI for these uncommitted changes has NOT been observed.
- A container smoke failure exposed a Uvicorn loop-factory class/instance mismatch; fixed,
  added a regression test, and reran Docker image safety/HTTP health/demo SSE successfully.
- Remaining blockers: GitHub auth/CSRF and real principals (M4), shared quotas/concurrency
  and atomic monetary reservations incl. retries/tools (M5), history UI and scheduled
  cleanup, production infrastructure and all launch security acceptance tests.
- No provider credits used, no secrets copied, no paid infrastructure provisioned, and
  no deployment, commit or push performed in this implementation session.
- Validation observed locally: locked uv sync, ruff check/format and pyright passed;
  90 backend tests passed with PostgreSQL (93% coverage), including actual ASGI disconnect
  cancellation, HTTP 409 concurrent-run rejection and two live-adapter API turns with
  synthetic provider HTTP. Without a DB: 76 passed / 14 skipped. REQUIRE_PG without a URL
  fails collection as intended. Frontend npm ci/lint/types, 15 tests and build passed;
  production npm audit is clean. Existing 7 moderate dev-only findings / bundle warning remain.
- Rebuilt non-root container passed health/demo SSE and packaged catalog safety. Packaged
  migration command also succeeded inside the container against local Postgres; production
  settings rejection explicitly verified. A local compose DB and persistent volume remain
  available; no unrelated containers were changed. Windows executable startup against
  Postgres also passed readiness and a complete persisted demo SSE stream on port 8017;
  that temporary API process was stopped afterwards.

- 2026-11-16 session baseline: clean main at 166e040, origin/main matched; observed
  main CI run 43805907745 successful. All AGENTS.md checks passed locally: 23 backend
  tests (95% coverage), 3 frontend tests; known bundle warning remains. No backend/.env.
- Approved next slice: own fixed-endpoint httpx Tavily adapter (remove langchain-tavily),
  per-run duplicate/concurrency/input/output guards, cumulative context/usage accounting,
  runtime event validation, offline Responses/Tavily wire fixtures, account check command.
  Fixtures will be explicitly synthetic, not recordings of live provider traffic.
- CI cleanup: Node24 action upgrades and a Docker smoke job passed in observed CI
  run 43809170669 (09c55eb). Approved PRs #1, #7, #2, #3, #4 closed.
  Dependabot ignores Python >=3.13, TypeScript >=7 and @types/node >=25 to preserve
  the verified Python 3.12 / TypeScript 6 / current @types/node 24 constraints.
- Tavily adapter implemented using the shared httpx client; server fixes search options,
  trims evidence fields, rejects unsafe citation URLs, caps decoded response bytes and
  redacts HTTP/timeout/malformed errors. Removed langchain-tavily and refreshed uv.lock.
  Nine offline HTTP tests added: 32 backend tests passed, 95% coverage; ruff/types passed.
- Tool guards added with run-local context (no mutable state on cached agents): reject
  invalid/oversized queries before dispatch, normalize case/whitespace for duplicate
  prevention before awaiting, semaphore-bound execution, drop whole evidence entries
  rather than slicing JSON. Defaults approved: 400 input / 6000 output chars, 3 parallel.
  Seven new tests cover duplicate races, separate runs, input, concurrency and output.
- Model middleware now checks every call's context (including system/tools and output
  allowance) and cumulative run input before dispatch. Uses a conservative character
  estimate; this is not a tokenizer or billing guarantee. Actual final-message usage
  counted once per call; cached input/reasoning are subsets, never added twice.
  Missing usage falls back to estimated input + maximum output and is flagged.
  Usage event added; cached pricing remains unverified and cost helper fails closed.
- Observed CI successes: Tavily 43809478023 (6935425), tool guards 43809747335
  (2d97db5), context/usage 43810011317 (003e6ed). All include Docker smoke.
- Strict runtime event schemas added at the SSE boundary and browser parser, including
  usage subset invariants, URL safety, known tools/statuses and payload bounds. Malformed
  service output becomes a redacted error. Local tests: 51 backend, 10 frontend passed.
- Event validation CI 43810292983 (0b4c18f) passed including Docker smoke.
- Synthetic HTTP loop tests verify full Responses tool-call/answer streams, Tavily
  response trimming, parallel/cross-turn duplicates, context rejection on later calls,
  model-call bounds, hidden reasoning, missing usage and redacted failures with a bounded
  retry. Found/fixed recursion-limit overhead introduced by middleware (MAX_MODEL_CALLS=1).
- Read-only model-visibility command implemented and tested offline; does not establish
  generation/reasoning access or pricing, and never enables models. No live probe run.
- Remaining known limits: context estimates are not billing guarantees; retries are not
  separately counted as logical model calls; failed/cancelled usage is not durable yet.
  Production remains blocked. Next substantive milestone is M3, not authentication/quotas.
- Rebuilt development Docker image locally after adapter/guard changes: non-root UID,
  packaged catalog, absence of /app .env, health and complete demo stream all passed.
  npm audit --omit=dev: zero findings; full audit: seven moderate findings on the existing
  dev-only minimatch/typescript-eslint chain. No forced dependency changes made.
- Final implementation checks observed locally: uv sync --locked, ruff check/format,
  pyright, 66 backend tests (96% coverage); npm ci/lint/typecheck, 10 frontend tests,
  build all passed. Known >500 kB bundle warning remains. Implementation CI 43811140992
  (d759ca0) observed successful: backend, frontend and Docker smoke. Node20 deprecation
  warning absent from inspected updated CI logs. Production rejection also verified
  explicitly inside rebuilt container. Six implementation commits pushed to main.

- User is preparing fresh OpenAI/Tavily/Wolfram keys, dev/prod GitHub OAuth Apps,
  and Cloudflare Turnstile. Never copy rag-app's .env.
- gh is authenticated as rguilmain; use C:\Program Files\GitHub CLI\gh.exe if PATH is stale.
- Fly CLI was not detected. No Fly app, database, billing changes, or deployment yet.
- Docker daemon is now running. Image build, non-root UID, packaged catalog,
  absence of /app .env files, production startup rejection, HTTP health, and demo
  SSE completion verified. Repeat with `pwsh -File scripts/smoke-docker.ps1`.
- Baseline after streaming lifecycle changes: 23 backend tests, 95% coverage;
  frontend lint/types and 3 tests passed. No paid provider calls performed.
- Provider account/model access and live streaming have NOT been tested.

## Correcting earlier planning assumptions

- GitHub user login is OAuth; do not assume GitHub Actions OIDC is user authentication.
- GitHub 404 does not prove a globally available name; handle repo creation errors.
- Library versions are determined by lockfiles and successful checks, not copied from
  the earlier speculative version table. TypeScript 7 peer conflict requires TS6 for now.
- Do not treat provider project billing alerts as guaranteed hard spending limits.
- Do not block every model because one optional catalog model becomes unavailable.
- Production cookie __Host- rules need a separate non-prefixed local HTTP cookie name.
