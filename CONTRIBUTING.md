# Contributing

See AGENTS.md for commands and docs/PLAN.md for milestones.
Make focused changes with offline tests. Public-facing features require adversarial
tests, not just happy paths. Never include credentials in fixtures or screenshots.

## Workflow

`main` is protected, so every change goes through a pull request:

1. Branch from `main` (for example `git switch -c feat/short-name`).
2. Commit with a conventional message and push the branch.
3. Open a pull request and wait for CI. These checks are required: `backend` (includes the
   locked-dependency audit with `pip-audit`), `frontend`, `docker-smoke` and `e2e`.
4. Merge only once all four pass. Do not push directly to `main`.

Repository administrators can still bypass protection to recover from a broken check, so use
that only for recovery and say why in docs/PLAN.md. Any dependency advisory fails the build:
fix it, or pin a safe version, rather than allow-listing it. Dependabot raises version updates
weekly for `uv`, `npm`, `github-actions` and `docker`.