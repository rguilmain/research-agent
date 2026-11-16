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
    Verified locally; CI wiring added but not run remotely. Production DB remains M9.
  - [ ] M3b: history presentation/resume UX, retention scheduling and operational hardening.
    Interrupted threads intentionally require a new thread; no automatic tool replay.
- [ ] M4: server-side GitHub OAuth (state, PKCE if supported), opaque hashed sessions,
  secure cookies, CSRF, restricted redirects, session rotation/logout, anonymous thread claim.
- [ ] M5: Turnstile server validation, hashed-IP and cookie quotas, per-user quotas,
  shared SQL rate limits and concurrency limits, global budget reservations and settlement.
- [ ] M6: multi-turn UX, saved history/sidebar, model access tiers, quota badge,
  sign-in, Turnstile, persisted theme, accessibility and privacy/data deletion screens.
- [ ] M7: same-origin production static serving, non-root multi-stage Docker image,
  container build/smoke/security tests. Do not remove production startup block yet.
- [ ] M8: Postgres integration tests, Playwright offline full-stack tests,
  dependency/security checks, branch protection and approved deployment workflow.
- [ ] M9: Fly.io app + Managed Postgres, release migrations/checkpointer setup,
  secrets, health checks, HTTPS, backups/restore test, staging security smoke test.
- [ ] M10: launch documentation/screenshot, threat model, privacy/retention policy,
  runbook and release. Remove production block ONLY after security acceptance tests pass.
- [ ] M11: update rag-app README to point to the completed replacement.

## Next agent: begin here

1. Run all AGENTS.md checks and inspect the CI run.
2. M1b and non-durable M2b controls verified offline; live access/reasoning/pricing remain
   unverified. Do not spend provider credits without keys/approval.
3. Introduce PostgreSQL and migrations before authentication or quotas.
   M3a implementation is now in the working tree; verify recorded local checks below.
4. Implement auth and budget controls together with adversarial/concurrency tests.
5. Keep README truthful; a demo foundation is not a production-ready application.

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

Current: GET /healthz, /readyz, /api/v1/config, /api/v1/models; POST /api/v1/research;
GET/POST /api/v1/threads; GET/PATCH/DELETE /api/v1/threads/{id} (metadata, local identity).
Planned: GET /api/v1/me; GET /auth/github/login and /auth/github/callback;
POST /auth/logout. OAuth callback should be on the same public origin.

SSE: thread, run_started, step, token, sources, usage, quota, done, error. Current implementation
supports these except quota. Never stream raw chain-of-thought or secret
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

### M3a implementation — November 16, 2026

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