# Architecture and decisions

## Current foundation

React/MUI browser -> Vite same-origin /api proxy -> FastAPI -> ResearchService.
DemoResearch emits clearly labeled offline events; LiveResearch wraps a cached
LangChain create_agent per enabled model. Tools are read-only and async. Wikipedia
uses a small fixed-endpoint HTTP adapter rather than the broad community package.
Wolfram retains the prototype's User-Agent/parsing behavior but redacts failures.

Tavily uses our own fixed-endpoint httpx adapter, not langchain-tavily (whose aiohttp
transport cannot be intercepted by respx). The server fixes basic depth, three results,
and excludes generated answers, images and raw content. Response bytes and evidence
fields are bounded; errors never include provider bodies or credentials.

The SSE lifecycle holds one provider read in flight, emits heartbeats without cancelling
that read, polls disconnects during silence, and explicitly closes the source on timeout,
terminal events, or cancellation. Per-response event IDs support diagnostics only, not
replay/resumption. Only assistant text blocks are emitted, not tool text or reasoning.
Synthetic LangChain messages and synthetic Responses SSE/Tavily HTTP fixtures test the
adapter through the actual OpenAI SDK and create_agent loop. These are NOT captured live
traffic and do not establish model/account availability or live reasoning compatibility.

RunContext is created for every run and passed explicitly through LangGraph runtime;
cached agents/middleware retain no mutable per-run state. Tool guards reject duplicate,
invalid and oversized queries before HTTP, cap concurrency and retain only whole JSON
evidence entries within the output cap. Every tool bounds decoded response bytes.
Model guards check full system/message/tool context plus output allowance before every
call, and accumulate conservative input estimates or larger observed usage across calls.
Final-message usage is counted once, never from streamed chunks/updates; cached input and
reasoning are subsets. Missing usage is flagged and estimated conservatively. With a
database, in-flight conservative usage is persisted before each model dispatch and
replaced by final observed usage. Failures/cancellations retain estimates; this is NOT
a billing guarantee. SDK retries remain bounded to one, and a model reservation covers both
attempts.
Strict Pydantic SSE payload validation and corresponding browser runtime checks reject
unknown fields/types/tools, unsafe source URLs, invalid usage totals and oversized frames.

PostgreSQL persistence exists for local development; development identity, rate limits,
quotas and Turnstile exist; budget reservations, concurrency caps and public deployment
do not.
APP_ENV=production fails validation intentionally. Demo mode is not an auth bypass
for eventual production: production must never allow it.

## Intended production shape

One non-root container serves static frontend and FastAPI behind Fly HTTPS ingress.
Postgres stores sessions, thread metadata, LangGraph checkpoints, usage, quotas,
and transactional budget reservations. GitHub OAuth terminates in the backend;
the browser receives only an opaque session cookie, never provider tokens.
Cloudflare Turnstile protects anonymous admission, not authorization.

## Decisions

1. Python 3.12 and uv; all tool invocations use backend's own environment.
2. GPT-6 Luna configurable default; runtime selection limited to a server catalog.
   Cache agents per model with a shared PostgreSQL checkpointer and per-run runtime context.
3. TypeScript 6, not 7: npm rejected TS7 because typescript-eslint 8.72.2 requires
   TypeScript >=4.8.4 <6.1.0. No peer-dependency overrides were used for TypeScript.
4. MUI 9 uses sx instead of removed direct system-style props.
5. SSE over POST/fetch, not EventSource: JSON body, cancellation, future CSRF header.
6. No Redis initially. Authoritative quotas/rate limits must nevertheless be shared
   in Postgres and work across instances; in-memory buckets are only an extra layer.
7. Secret-bearing exceptions never reach tool messages or logs; log class/request ID.
8. The root Dockerfile is a development foundation until static serving/security
   gates are complete. Do not mistake a successful image build for launch readiness.

## Development persistence (M3a)

SQLAlchemy 2 async sessions own `principals`, `threads` and `runs`. Alembic owns their
schema. `research-agent-migrate` explicitly applies Alembic followed by the installed
checkpointer's idempotent `setup()`; the two schema managers are intentionally separate
because checkpoint migrations include nontransactional index operations. Application
startup checks both schema versions and never creates tables. Migration resources ship
inside the Python package so the same command works in the development container.

Both SQLAlchemy and the checkpointer use psycopg 3. The checkpointer has a separate
autocommit/dict-row pool and a strict serializer with no pickle fallback. Windows entry
points explicitly use a selector event loop. Database URLs are secret settings; pool
exception messages are redacted and SQL parameter logging is disabled.

Every metadata query is owner-scoped; unknown/foreign/deleted/expired resources return
404. The owner is the principal of the request's session; no client-provided owner ID is
accepted. Production remains blocked.

Identity (M4a): `GET /api/v1/me` is the only endpoint that creates a session (anonymous,
expiring with `ANON_THREAD_TTL_HOURS`). Tokens are random; the database stores only an
HMAC under `SESSION_SECRET`, and the CSRF token is a second HMAC of that hash, returned in
`/me` and sent as `X-CSRF-Token`. Unsafe routes (research, thread create/rename/delete,
logout) require an allowed Origin (or `Sec-Fetch-Site: same-origin`) plus that token;
loopback aliases are accepted only outside production. GitHub sign-in uses state and PKCE,
a browser-binding cookie, single-use hashed state rows and a relative `next` path. Login
revokes the anonymous session and moves its live threads to the user principal in one
transaction, then issues a new user session (idle and absolute expiry). Logout revokes
the session. `research-agent-cleanup` also purges expired sessions, OAuth states,
empty anonymous principals and expired rate-limit windows in bounded batches.

Admission limits (M5a): `rate_limit_counters` holds one row per scope, HMAC key and aligned
hour window, incremented by a single upsert so counts stay exact across processes.
`GET /api/v1/me` counts anonymous session creation per hashed client address; research
counts every start per session principal and, for anonymous callers, per hashed address so
clearing cookies does not reset the limit; GitHub login start is counted per address.
Refusals return 429 `problem+json` with `Retry-After` before any thread, run or OAuth state
row is created. Keys are HMACs under `SESSION_SECRET`, so raw addresses are never stored.
`TRUST_PROXY_HEADERS` defaults to false; when enabled the server prefers `Fly-Client-IP` and
otherwise the rightmost `X-Forwarded-For` hop, the only entry a client cannot forge behind a
single trusted ingress. That assumption must be re-verified against the real Fly ingress in
M9. A process without a database is not limited, which is development-only behavior. Known
limits: fixed windows allow a brief boundary burst and address limits do not stop distributed
abuse; scheduled cleanup remains.

Daily quotas (M5b-1): `quota_usage` holds one weighted-unit counter per scope, HMAC key and
UTC day. A conditional upsert (`... WHERE units + n <= limit RETURNING units`) adds units
only when they fit, so a refused question consumes nothing, and all keys for one question are
charged in a single transaction that rolls back together. Anonymous callers are charged per
session **and** per hashed address; signed-in callers per principal. Weight and tier come from
the model catalog (`quota_weight` Luna 1 / Terra 3 / Sol 5; only `anonymous = true` models are
selectable anonymously). Quota is charged after the M5a rate limits and before any thread row;
a request refused during admission is refunded, but a run that fails after admission is not.
Refusals return 429 `problem+json` (`code: quota_exceeded`) with `Retry-After` in seconds until
the next UTC midnight, and an accepted run emits a bounded `quota` SSE event reporting the
remaining allowance. This is a policy limiter, not a monetary reservation; spend is capped
separately by the budget below.

Daily budget and concurrency (M5c): `budget_days` holds one row per UTC day with a reserved
total and a settled total, and `budget_reservations` is a ledger with one row per paid
operation (`model` or `tool`). Before every model dispatch the guard holds a worst case: the
conservative prompt estimate, the full output allowance, and both SDK attempts, priced with an
unknown cached price billed as ordinary input. Before a paid tool call it holds that tool's
flat price from `models.toml`. Only one conditional upsert raises the day's reserved total, and
only while `reserved + spent + amount <= GLOBAL_DAILY_BUDGET_USD`, so parallel workers cannot
over-reserve. Settlement releases the hold and records the charge once; it is idempotent.
Observed usage settles the real cost, while missing usage, a failed dispatch or a cancellation
settles the full hold. A hold whose worker died is charged in full when it expires, and any
hold still open when a run ends is settled by the request itself. A live run is refused with
503 `budget_exhausted` when today's remaining budget could not cover one worst-case call; the
check runs after rate limits and before quotas, so a refused request consumes neither quota nor
money. Tools and models without a verified price are never offered in live mode, and demo mode
records no spend. Run admission additionally enforces `MAX_ACTIVE_RUNS` and
`MAX_ACTIVE_RUNS_PER_PRINCIPAL` while holding a transaction-scoped advisory lock, counting only
runs whose heartbeat is inside two minutes, and refuses over-limit requests with 429
`too_many_active_runs`. These amounts are local estimates and never a provider billing
guarantee: an overspend beyond a hold is recorded but cannot be prevented, and a retried
attempt inside a successful call is covered by the hold but settled from the final attempt's
usage only.

Human verification (M5b-2): `auth_sessions.human_verified_until` records a successful
Turnstile check on an anonymous session. Anonymous research is refused with 403
`turnstile_required` before any rate limit or quota is charged, so an unverified client
consumes nothing; signed-in callers are never challenged, and Turnstile is disabled
entirely unless both keys are configured. `POST /api/v1/turnstile/verify` validates the
token against Cloudflare's fixed siteverify endpoint with a bounded response, a ten-second
timeout and at most one retry reusing the same idempotency key. A refusal, a wrong hostname
or action, a timeout, or a malformed/oversized body all fail closed. With Cloudflare's
documented test secrets the hostname is always `example.com` and no action is returned, so
those two checks apply only to a real secret, and production readiness rejects test secrets.
The window lasts `TURNSTILE_VERIFIED_MINUTES` (maximum 60) and is capped at the session
expiry. No Content-Security-Policy is set yet; a future CSP (M7) must allow
`https://challenges.cloudflare.com` in `script-src` and `frame-src`.
Threads fix their model and demo/live mode at creation. Demo run metadata is explicitly
marked, but demo answers are never checkpointed. GET thread returns metadata only, not
raw checkpoint state, reasoning or tool arguments. GET /threads/{id}/messages projects a
bounded, owner-scoped transcript: user questions plus each turn's final assistant text
blocks, with sources re-derived from that turn's structured tool evidence. Private
fields (system prompts, reasoning, tool arguments, raw tool output, checkpoint channel
state) are never serialized; a running or unfinished thread yields no transcript and
reports can_continue=false. Auto-created threads take their title from the first query.
History rendering remains M6; resume is not implemented.

A dedicated SQL connection holds a session advisory lock per thread while streaming;
row locks and a unique active-run index additionally serialize run admission. Conflicts
return 409 before SSE headers. Locks require direct/session-mode PostgreSQL connections,
NOT transaction-mode proxies. Pool capacity bounds this foundation; run admission also
enforces shared concurrency caps (M5c). Failed/cancelled/abandoned
threads reject new runs rather than replaying potentially unfinished tool operations.

Model middleware writes estimated in-flight usage before provider dispatch and observed
final usage afterwards. Tool admission counts are persisted too; concurrent writes are
serialized per run. Terminal status is saved before `done`. Disconnect bookkeeping is
shielded from AnyIO cancellation. Recovery marks stale runs (>5 minutes, batch 100) as
abandoned only if their thread lock is free. A worker retaining a lock is never reclaimed.
Token usage is a ledger for visibility and context safety, not a monetary record; spend is
tracked separately by the M5c budget tables below.

Anonymous thread expiry defaults to 24 hours from creation; user-kind retention defaults
to 30 days. Access expires immediately, while physical deletion requires the explicit
cleanup command. Soft-deleted threads are purged after the retention interval (anonymous
expiry may purge earlier). Threads with no runs at all are also purged after a short
`EMPTY_THREAD_TTL_HOURS` grace period, which removes threads left behind by failed run
admission. Cleanup skips active/locked threads, deletes checkpoint state first, then
metadata/runs; a crash between these steps is safe to retry. The live application runs one
bounded pass every `CLEANUP_INTERVAL_MINUTES` (default 15, 0 disables) inside its own
lifespan task, and a session advisory lock (`MAINTENANCE_LOCK`) lets only one process clean
up at a time; the manual command takes the same lock, so the scheduler and
`research-agent-cleanup` never overlap. One pass drains at most 100 rows per category, so a
large backlog needs several intervals or `--max-batches` (1-50) by hand. Conversation/tool/checkpoint content
is private retained data, not logs; encryption, formal retention policy, real identity and
deployment backup/deletion guarantees remain launch work. PostgreSQL test databases must
end in `_test`; tests truncate only that dedicated database. CI requires these tests.

## Known dependency warning

The 2026-11-16 npm audit reports seven moderate findings across the dev-only minimatch /
typescript-eslint chain (not seven distinct root advisories). Production dependency audit
is clean (zero findings with --omit=dev). Do not
apply npm's suggested incompatible downgrade or force a major minimatch override
without tests; upgrade the upstream lint dependency when its supported fix lands.
The browser bundle also exceeds Vite's default 500 kB warning; lazy-load Markdown
and split UI chunks during the frontend performance milestone.