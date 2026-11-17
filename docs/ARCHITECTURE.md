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
a billing guarantee or monetary reservation. Atomic budget reservations remain M5.
SDK retries remain bounded to one; future budget reservations must cover both attempts.
Strict Pydantic SSE payload validation and corresponding browser runtime checks reject
unknown fields/types/tools, unsafe source URLs, invalid usage totals and oversized frames.

PostgreSQL persistence exists for local development; authentication, cost enforcement
and public deployment do not.
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
404. This is NOT authentication: all development requests share one fixed anonymous
identity until M4. No client-provided owner ID is accepted. Production remains blocked.
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
NOT transaction-mode proxies. Pool capacity bounds this foundation; shared admission,
explicit global concurrency controls, and budgets remain M5. Failed/cancelled/abandoned
threads reject new runs rather than replaying potentially unfinished tool operations.

Model middleware writes estimated in-flight usage before provider dispatch and observed
final usage afterwards. Tool admission counts are persisted too; concurrent writes are
serialized per run. Terminal status is saved before `done`. Disconnect bookkeeping is
shielded from AnyIO cancellation. Recovery marks stale runs (>5 minutes, batch 100) as
abandoned only if their thread lock is free. A worker retaining a lock is never reclaimed.
SDK retries remain bounded but are NOT separately accounted/reserved; monetary crash
reconciliation is not solved by this token ledger and remains a production blocker.

Anonymous thread expiry defaults to 24 hours from creation; user-kind retention defaults
to 30 days. Access expires immediately, while physical deletion requires the explicit
cleanup command. Soft-deleted threads are purged after the retention interval (anonymous
expiry may purge earlier). Threads with no runs at all are also purged after a short
`EMPTY_THREAD_TTL_HOURS` grace period, which removes threads left behind by failed run
admission. Cleanup skips active/locked threads, deletes checkpoint state first, then
metadata/runs; a crash between these steps is safe to retry. Cleanup is not scheduled and
must be run repeatedly for backlogs; `--max-batches` (1-50) drains more than 100 per
invocation. Conversation/tool/checkpoint content
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