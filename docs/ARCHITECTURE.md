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
reasoning are subsets. Missing usage is flagged and estimated conservatively. Failed or
cancelled runs do not yet persist partial usage/reservations; durable recovery is M3/M5.
SDK retries remain bounded to one; future budget reservations must cover both attempts.
Strict Pydantic SSE payload validation and corresponding browser runtime checks reject
unknown fields/types/tools, unsafe source URLs, invalid usage totals and oversized frames.

No persistence, authentication, cost enforcement, or public deployment exists yet.
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
   Cache agents per model initially; shared checkpointer/thread locking comes later.
3. TypeScript 6, not 7: npm rejected TS7 because typescript-eslint 8.72.2 requires
   TypeScript >=4.8.4 <6.1.0. No peer-dependency overrides were used for TypeScript.
4. MUI 9 uses sx instead of removed direct system-style props.
5. SSE over POST/fetch, not EventSource: JSON body, cancellation, future CSRF header.
6. No Redis initially. Authoritative quotas/rate limits must nevertheless be shared
   in Postgres and work across instances; in-memory buckets are only an extra layer.
7. Secret-bearing exceptions never reach tool messages or logs; log class/request ID.
8. The root Dockerfile is a development foundation until static serving/security
   gates are complete. Do not mistake a successful image build for launch readiness.

## Known dependency warning

The 2026-11-16 npm audit reports seven moderate findings across the dev-only minimatch /
typescript-eslint chain (not seven distinct root advisories). Production dependency audit
is clean (zero findings with --omit=dev). Do not
apply npm's suggested incompatible downgrade or force a major minimatch override
without tests; upgrade the upstream lint dependency when its supported fix lands.
The browser bundle also exceeds Vite's default 500 kB warning; lazy-load Markdown
and split UI chunks during the frontend performance milestone.