// Prepares the dedicated browser-test database and then serves the built UI plus API in the
// foreground, so Playwright supervises one process and the ordering is deterministic.
import { spawn, spawnSync } from 'node:child_process'
import { resolve } from 'node:path'

const repoRoot = resolve(import.meta.dirname, '..', '..')
const backend = resolve(repoRoot, 'backend')
const frontend = resolve(repoRoot, 'frontend')

/** The dedicated database; never the development one, so tests cannot corrupt real data. */
const database =
  process.env.E2E_DATABASE_URL ??
  'postgresql://research_dev:local-development-only@127.0.0.1:54329/research_agent_e2e'

if (!(database.split('?')[0].endsWith('_e2e') || database.includes('_e2e'))) {
  console.error('E2E_DATABASE_URL must point at a dedicated *_e2e database.')
  process.exit(1)
}

function run(command, args, cwd) {
  return spawnSync(command, args, {
    cwd,
    env: { ...process.env, DATABASE_URL: database },
    encoding: 'utf8',
    shell: true,
  })
}

// 1. Create the database when the local compose service is up. The CI service container already
//    defines it, so a failure here is expected and harmless.
run('docker', ['compose', 'exec', '-T', 'db', 'createdb', '-U', 'research_dev', 'research_agent_e2e'], repoRoot)

// 2. Migrations and checkpoint tables: the application refuses to start without them.
const migrated = run('uv', ['run', '--project', backend, 'research-agent-migrate'], frontend)
if (migrated.status !== 0) {
  console.error(migrated.stdout ?? '')
  console.error(migrated.stderr ?? '')
  console.error(
    'Could not prepare the browser-test database. Start the local database with ' +
      '`docker compose up -d --wait db`, or set E2E_DATABASE_URL to a reachable *_e2e database.',
  )
  process.exit(migrated.status ?? 1)
}

// 3. Clear leftover sessions, threads, quotas and checkpoints so repeated local runs start from
//    the same state. Without this the fixed test addresses would already have spent their daily
//    allowance, and stale threads would collide with the history assertions.
const reset = run(
  'uv',
  ['run', '--project', backend, 'python', resolve(import.meta.dirname, 'reset-database.py')],
  frontend,
)
if (reset.status !== 0) {
  console.error(reset.stdout ?? '')
  console.error(reset.stderr ?? '')
  process.exit(reset.status ?? 1)
}

// 4. Serve in the foreground. Playwright waits for /healthz before running any test.
const server = spawn('uv', ['run', '--project', backend, 'research-agent'], {
  cwd: frontend,
  env: { ...process.env, DATABASE_URL: database },
  stdio: 'inherit',
  shell: true,
})
for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => server.kill())
}
server.on('exit', code => process.exit(code ?? 0))
