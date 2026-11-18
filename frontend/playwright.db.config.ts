import { existsSync } from 'node:fs'
import { resolve } from 'node:path'

import { defineConfig } from '@playwright/test'

const dist = resolve(import.meta.dirname, 'dist')
const origin = 'http://127.0.0.1:8125'
const stub = 'http://127.0.0.1:8124'

// These tests run against a real PostgreSQL-backed process in demo mode, so the anonymous
// session, Turnstile verification, daily quota and saved-history paths are exercised for real.
// Cloudflare is replaced by a local stub; nothing leaves the machine.
if (!existsSync(resolve(dist, 'index.html'))) {
  throw new Error('Run `npm run build` before the database browser tests: frontend/dist is missing.')
}

export default defineConfig({
  testDir: './e2e-db',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL: origin,
    // Locally an installed Edge can be used; CI downloads Chromium instead.
    channel: process.env.E2E_CHANNEL || undefined,
  },
  webServer: [
    {
      command: 'node e2e-db/siteverify-stub.mjs',
      url: `${stub}/healthz`,
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      // Creates and migrates the dedicated *_e2e database, then serves the built UI and API.
      command: 'node e2e-db/start-backend.mjs',
      url: `${origin}/healthz`,
      reuseExistingServer: false,
      timeout: 180_000,
      env: {
        // Explicit environment: a developer's backend/.env must not change these tests.
        APP_ENV: 'development',
        DEMO_MODE: 'true',
        HOST: '127.0.0.1',
        PORT: '8125',
        PUBLIC_BASE_URL: origin,
        STATIC_DIR: dist,
        SESSION_SECRET: 'browser-test-session-secret-value-40-chars-long',
        // Cloudflare's documented always-pass test keys, aimed at the local stub.
        TURNSTILE_SITE_KEY: '1x00000000000000000000AA',
        TURNSTILE_SECRET_KEY: '1x0000000000000000000000000000000AA',
        TURNSTILE_VERIFY_URL: `${stub}/turnstile/v0/siteverify`,
        // One anonymous question per day, so the refusal is reachable inside one test.
        QUOTA_ANON_DAILY: '1',
        // Generous admission limits, so the quota rule is what refuses rather than a rate limit.
        RATE_LIMIT_ANON_SESSION_PER_HOUR: '200',
        RATE_LIMIT_RESEARCH_PER_HOUR: '200',
        RATE_LIMIT_TURNSTILE_PER_HOUR: '200',
        // Trusted only because this is a loopback-only test process; each test sends its own
        // `fly-client-ip` so the per-address quota buckets are isolated.
        TRUST_PROXY_HEADERS: 'true',
        OPENAI_API_KEY: '',
        TAVILY_API_KEY: '',
        GITHUB_CLIENT_ID: '',
        GITHUB_CLIENT_SECRET: '',
      },
    },
  ],
})
