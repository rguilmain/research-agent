# Coding-agent instructions

This is **Research Agent**, a web-search assistant, not document RAG.
Read README.md, docs/PLAN.md, and docs/ARCHITECTURE.md before edits.

- Keep changes scoped; update docs/PLAN.md with observed progress and blockers.
- Never commit secrets, copy another project's .env, or print credentials.
- Python 3.12; use uv and this project's backend environment, not rag-app's.
- Use PowerShell-safe paths. Set-Location into this repository first.
- Commit uv.lock and frontend/package-lock.json. Never use --legacy-peer-deps.
- Tests must run without provider keys; mock paid services at the HTTP boundary.
- Validate model allowlists, thread ownership, quotas, and CSRF server-side.
- Never present demo output as real research or fabricate sources.
- No tool may execute arbitrary code, read local files, or fetch arbitrary user URLs.
- Do not log prompts, credentials, session cookies, or exception URLs containing keys.
- Do not enable production until all security launch gates pass.
- Do not deploy paid infrastructure or archive rag-app without explicit approval.

## Checks

Backend (from backend): `uv sync --locked --group dev`, `uv run ruff check .`,
`uv run ruff format --check .`, `uv run pyright`, `uv run pytest`.
Run: `uv run research-agent`.

Frontend (from frontend): `npm ci`, `npm run lint`, `npm run typecheck`,
`npm test`, `npm run build`. Run: `npm run dev`.

Done means changed files reviewed, checks actually run, and limitations documented.
Never claim deployment, live provider calls, or CI success without observing them.