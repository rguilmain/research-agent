# Research Agent backend

From this directory: `uv sync --locked --group dev`, copy `.env.example` to
`.env`, then `uv run research-agent`. API docs: http://localhost:8000/docs.

Demo mode is enabled by default and never calls a provider. For real research,
set `DEMO_MODE=false`, `OPENAI_API_KEY`, `TAVILY_API_KEY`, and `DATABASE_URL`. Wolfram Alpha is
optional. Live calls cost money. Bind only to loopback during development.

See the root README for the loopback PostgreSQL compose service. Before enabling
persistence run `uv run research-agent-migrate`; startup performs no DDL and rejects
missing/outdated migrations. Run `uv run research-agent-cleanup` explicitly for bounded
retention cleanup and stale-run recovery. Migrations are packaged in the wheel/container.
Windows uses a selector event loop for psycopg compatibility.

All requests share one development-only identity until authentication is implemented.
Thread ownership checks are tested with separate injected identities, not real sessions.
Live checkpoints contain conversation content; demo responses are never checkpointed.

Production mode is deliberately blocked. See ../docs/PLAN.md for launch gates.
