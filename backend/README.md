# Research Agent backend

From this directory: `uv sync --locked --group dev`, copy `.env.example` to
`.env`, then `uv run research-agent`. API docs: http://localhost:8000/docs.

Demo mode is enabled by default and never calls a provider. For real research,
set `DEMO_MODE=false`, `OPENAI_API_KEY`, and `TAVILY_API_KEY`. Wolfram Alpha is
optional. Live calls cost money. Bind only to loopback during development.

Production mode is deliberately blocked. See ../docs/PLAN.md for launch gates.
