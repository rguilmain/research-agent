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

Administrators are bound by the same checks (`enforce_admins` is on), so a direct push to
`main` is rejected with `GH006: Protected branch update failed ... 4 of 4 required status
checks are expected`. If a required check is ever stuck (a flaky `e2e` run, or a GitHub
Actions outage), temporarily relax that single setting through the API, complete the recovery,
then set it back to `true`. Never leave the bypass on, and never relax more than one rule at a
time.

```powershell
# Run once with enforce_admins false to recover, then again with true to restore.
$body = @'
{"required_status_checks":{"strict":true,"contexts":["backend","frontend","docker-smoke","e2e"]},"enforce_admins":false,"required_pull_request_reviews":null,"restrictions":null,"required_conversation_resolution":true,"allow_force_pushes":false,"allow_deletions":false}
'@
Set-Content -LiteralPath $env:TEMP\protect.json -Value $body -Encoding ascii -NoNewline
gh api --method PUT repos/rguilmain/research-agent/branches/main/protection --input $env:TEMP\protect.json
Remove-Item -LiteralPath $env:TEMP\protect.json
```

Record any such recovery in docs/PLAN.md, and prefer fixing the check over relaxing the rule.

Any dependency advisory fails the build: fix it, or pin a safe version, rather than
allow-listing it. Dependabot raises version updates weekly for `uv`, `npm`, `github-actions`
and `docker`.