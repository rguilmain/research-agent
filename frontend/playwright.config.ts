import { existsSync } from 'node:fs'
import { resolve } from 'node:path'

import { defineConfig } from '@playwright/test'

const dist = resolve(import.meta.dirname, 'dist')
const origin = 'http://127.0.0.1:8123'

// The same-origin container serves the built UI, so these tests exercise the real
// security headers and the real frontend/API contract against demo mode only.
if (!existsSync(resolve(dist, 'index.html'))) {
  throw new Error('Run `npm run build` before the browser tests: frontend/dist is missing.')
}

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL: origin,
    // Locally an installed Edge can be used; CI downloads Chromium instead.
    channel: process.env.E2E_CHANNEL || undefined,
  },
  webServer: {
    command: 'uv run --project ../backend research-agent',
    url: `${origin}/healthz`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: {
      // Explicit environment: a developer's backend/.env must not change these tests.
      APP_ENV: 'development',
      DEMO_MODE: 'true',
      HOST: '127.0.0.1',
      PORT: '8123',
      PUBLIC_BASE_URL: origin,
      STATIC_DIR: dist,
      DATABASE_URL: '',
      OPENAI_API_KEY: '',
      TAVILY_API_KEY: '',
      GITHUB_CLIENT_ID: '',
      GITHUB_CLIENT_SECRET: '',
      TURNSTILE_SITE_KEY: '',
      TURNSTILE_SECRET_KEY: '',
    },
  },
})
