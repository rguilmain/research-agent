import { expect } from '@playwright/test'
import type { Page } from '@playwright/test'

export const HUMAN_CHECK = 'Confirm you are human to ask questions without signing in.'
export const DEMO_NOTICE = 'Demo mode — no paid APIs or real web searches.'
export const QUOTA_EXCEEDED =
  'Daily question limit reached. Sign in for a larger allowance, or try again tomorrow.'
export const DEMO_THREAD_NOTICE = 'Demo thread — answers are not saved.'

/** Serves a script in place of Cloudflare's loader; it verifies at once with a dummy token. */
export async function stubTurnstile(page: Page) {
  await page.route('https://challenges.cloudflare.com/**', route =>
    route.fulfill({
      status: 200,
      contentType: 'application/javascript',
      body: `window.turnstile = {
  render: function (element, options) {
    setTimeout(function () { options.callback('XXXX.DUMMY.TOKEN.XXXX'); }, 0);
    return 'widget-1';
  },
  reset: function () {},
  remove: function () {},
};`,
    }),
  )
}

/**
 * Opens the app and waits until the stubbed human check has unlocked the form.
 *
 * `clientIp` is sent as the trusted ingress header so each test gets its own anonymous
 * per-address quota and rate-limit bucket instead of sharing the loopback address.
 */
export async function openVerified(page: Page, clientIp: string, path = '/') {
  await page.setExtraHTTPHeaders({ 'fly-client-ip': clientIp })
  await stubTurnstile(page)
  await page.goto(path)
  await expect(page.getByText(HUMAN_CHECK)).toBeVisible()
  // Verification removes the challenge panel, which is the real "form is usable" signal.
  await expect(page.getByText(HUMAN_CHECK)).toHaveCount(0)
}

/** Asks one question and waits for the streamed answer to finish rendering. */
export async function ask(page: Page, question: string) {
  await page.getByLabel('What would you like to research?').fill(question)
  await page.getByRole('button', { name: 'Research' }).click()
  await expect(page.getByRole('heading', { name: 'Research result' })).toBeVisible()
}

/** The history sidebar entry for a thread title (excludes the Rename/Delete buttons). */
export function historyEntry(page: Page, title: string) {
  return page
    .getByRole('navigation', { name: 'Thread history' })
    .getByRole('button', { name: new RegExp(`^${title.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`) })
}
