/**
 * Browser tests against a real PostgreSQL-backed process in demo mode.
 *
 * These cover what the demo-only suite cannot: the anonymous session, server-side Turnstile
 * verification (Cloudflare replaced by a local stub), the daily quota refusal, and the saved
 * history sidebar. No provider keys are used and nothing leaves the machine.
 */
import { expect, test } from '@playwright/test'

import {
  ask,
  DEMO_NOTICE,
  DEMO_THREAD_NOTICE,
  historyEntry,
  HUMAN_CHECK,
  openVerified,
  ORIGIN,
  QUOTA_EXCEEDED,
  stubTurnstile,
} from './support'

const QUESTION = 'What is the capital of France?'

test('verifies the human check, then answers a demo question', async ({ page }) => {
  // openVerified proves the widget rendered and the server accepted the token.
  await openVerified(page, '198.51.100.11')
  await expect(page.getByText(DEMO_NOTICE)).toBeVisible()

  await ask(page, QUESTION)

  // The accepted run reports the remaining allowance from the server.
  await expect(page.getByText('Daily allowance: 0 of 1 left')).toBeVisible()
  // Demo output must never look like real research.
  await expect(page.getByText(DEMO_NOTICE)).toBeVisible()
  await expect(page.getByText('Research could not complete')).toHaveCount(0)
})

test('lists the saved thread, renames it and reopens it', async ({ page }) => {
  await openVerified(page, '198.51.100.12')
  await ask(page, QUESTION)

  const entry = historyEntry(page, QUESTION)
  await expect(entry).toBeVisible()

  // Renaming exercises the owner-scoped PATCH path through the real sidebar dialog.
  await page.getByRole('button', { name: `Rename ${QUESTION}` }).click()
  await page.getByLabel('Thread title').fill('Capital of France')
  await page.getByRole('button', { name: 'Save' }).click()
  const renamed = historyEntry(page, 'Capital of France')
  await expect(renamed).toBeVisible()

  // Reopening loads that thread's stored metadata. A demo thread deliberately has no
  // checkpoint, so its transcript is empty and the server reports status completed.
  await renamed.click()
  await expect(page.getByText(DEMO_THREAD_NOTICE)).toBeVisible()
  await expect(page.getByText(`Thread completed. Start a new thread; interrupted work is never replayed.`))
    .toHaveCount(0)
})

test('refuses a second question once the daily allowance is spent', async ({ page }) => {
  await openVerified(page, '198.51.100.13')
  await ask(page, QUESTION)
  await expect(page.getByText('Daily allowance: 0 of 1 left')).toBeVisible()

  // The second question is refused by the shared daily quota, not by a rate limit.
  await page.getByLabel('What would you like to research?').fill('And the capital of Italy?')
  await page.getByRole('button', { name: 'Research' }).click()
  await expect(page.getByText(QUOTA_EXCEEDED)).toBeVisible()
})

test('refuses an unverified anonymous research start server-side', async ({ page }) => {
  // The widget renders but never reports success, so this session stays unverified.
  await page.setExtraHTTPHeaders({ 'fly-client-ip': '198.51.100.14' })
  await stubTurnstile(page, { verify: false })
  await page.goto('/')
  await expect(page.getByText(HUMAN_CHECK)).toBeVisible()

  // With a question typed and a model selected, only the human check keeps the form disabled.
  await page.getByLabel('What would you like to research?').fill('Unverified question')
  await expect(page.getByRole('button', { name: 'Research' })).toBeDisabled()

  // A tampered client cannot bypass it either: the server refuses the run outright.
  const me = await page.request.get('/api/v1/me')
  const { csrf_token: csrf } = await me.json()
  const refused = await page.request.post('/api/v1/research', {
    data: { query: 'Unverified question' },
    headers: { origin: ORIGIN, 'x-csrf-token': csrf },
  })
  expect(refused.status()).toBe(403)
  expect((await refused.json()).code).toBe('turnstile_required')
})
