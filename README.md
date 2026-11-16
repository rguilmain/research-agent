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
and `TAVILY_API_KEY` in `backend/.env`. Wolfram Alpha is optional.
Do not paste keys into chat, commit them, or bind this unfinished API publicly.

## Models

`DEFAULT_MODEL=gpt-6-luna`. The server-owned allowlist lives in
`backend/src/research_agent/models.toml`; the UI selects among enabled models
per request. Additional models are disabled until account access is verified.
Prices in the catalog are estimates, **not an implemented budget enforcement mechanism**.
Do not enable expensive models before the budget-reservation milestone.

## What works

- Validated settings and model allowlisting; lifespan-managed async HTTP client.
- POST research endpoint with incremental SSE text, tool activity, and source events.
- Async read-only Tavily, Wikipedia, and optional Wolfram tools; bounded agent calls.
- Cancellable frontend, safe Markdown, no remote tracking images, responsive MUI UI.
- Offline backend/frontend tests, linting, type checks, committed lockfiles, CI.

## Planned launch features

Hybrid access: anonymous users pass Turnstile and get a small daily quota; GitHub
users get more quota and saved conversations. PostgreSQL will enforce ownership,
quotas, and atomic cost reservations across instances. Deployment target: Fly.io.
These are **not implemented yet**. See [the milestone checklist](docs/PLAN.md).

## Checks

See [AGENTS.md](AGENTS.md) for all commands and [SECURITY.md](SECURITY.md) for
security policy. TypeScript 6 is intentionally pinned because the installed
typescript-eslint release rejects TypeScript 7 (see docs/ARCHITECTURE.md).

No keys from the old prototype were copied. The old repository remains unchanged.