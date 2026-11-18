import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

// The init script bridges CSP violations back to the test through this binding.
declare global {
  interface Window {
    reportCspViolation: (blocked: string) => Promise<void>
  }
}

const DEMO_NOTICE = 'Demo mode'
const ORIGIN = 'http://127.0.0.1:8123'

/** Records console errors and CSP violations for the current page. */
async function watch(page: Page) {
  const consoleErrors: string[] = []
  const violations: string[] = []
  page.on('console', message => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  page.on('pageerror', error => consoleErrors.push(error.message))
  await page.exposeBinding('reportCspViolation', (_source, blocked: string) => {
    violations.push(blocked)
  })
  // Runs before every navigation, so the listener exists for the first page load too.
  await page.addInitScript(() => {
    window.addEventListener('securitypolicyviolation', event => {
      void window.reportCspViolation(`${event.violatedDirective} ${event.blockedURI}`)
    })
  })
  return { consoleErrors, violations }
}

// Without a database the owner-scoped endpoints answer 503. The UI reports that as an
// informational alert, so it is documented behaviour rather than a page failure.
const EXPECTED_503 = 'Failed to load resource: the server responded with a status of 503'

test('loads the built app without console errors, CSP violations or third-party requests', async ({
  page,
}) => {
  const { consoleErrors, violations } = await watch(page)
  const external: string[] = []
  page.on('request', request => {
    if (!request.url().startsWith(ORIGIN)) external.push(request.url())
  })

  await page.goto('/')
  await expect(page).toHaveTitle('Research Agent')
  await expect(page.getByText(DEMO_NOTICE)).toBeVisible()
  // Demo mode with no database renders no Turnstile widget and no avatar images.
  await expect(page.getByText('Human check')).toHaveCount(0)
  expect(consoleErrors.filter(text => !text.includes(EXPECTED_503))).toEqual([])
  expect(violations).toEqual([])
  expect(external).toEqual([])
})

test('streams a demo answer and never presents it as real research', async ({ page }) => {
  await page.goto('/')
  await page.getByLabel('What would you like to research?').fill('What is the capital of France?')
  await page.getByRole('button', { name: 'Research' }).click()
  await expect(page.getByRole('heading', { name: 'Research result' })).toBeVisible()
  await expect(page.getByText(DEMO_NOTICE)).toBeVisible()
  await expect(page.getByText('Research could not complete')).toHaveCount(0)
})

test('enforces the CSP against inline and third-party scripts', async ({ page }) => {
  await page.goto('/')
  await page.evaluate(() =>
    window.addEventListener('securitypolicyviolation', event => {
      document.title = `csp:${event.violatedDirective}`
    }),
  )
  await page.evaluate(() => {
    const inline = document.createElement('script')
    inline.textContent = 'window.__pwned = true'
    document.body.appendChild(inline)
    const remote = document.createElement('script')
    remote.src = 'https://evil.example/x.js'
    document.body.appendChild(remote)
  })
  await expect(page).toHaveTitle(/csp:script-src(-elem)?$/)
  expect(await page.evaluate(() => 'pwned' in window)).toBe(false)
})

test('switches the theme and remembers the choice across a reload without CSP violations', async ({ page }) => {
  const { consoleErrors, violations } = await watch(page)
  await page.goto('/')
  const bodyBackground = () => page.evaluate(() => getComputedStyle(document.body).backgroundColor)
  const light = await bodyBackground()

  await page.getByRole('button', { name: 'Dark theme' }).click()
  // The computed background proves the scheme really switched, not just the button state.
  await expect.poll(bodyBackground).not.toBe(light)
  const dark = await bodyBackground()
  // The 'data' selector writes the scheme onto <html>, so the CSS actually switches.
  await expect(page.locator('html')).toHaveAttribute('data-mui-color-scheme', 'dark')
  await expect(page.getByRole('button', { name: 'Dark theme' })).toHaveAttribute('aria-pressed', 'true')

  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('data-mui-color-scheme', 'dark')
  await expect(page.getByRole('button', { name: 'Dark theme' })).toHaveAttribute('aria-pressed', 'true')
  await expect.poll(bodyBackground).toBe(dark)
  expect(violations).toEqual([])
  expect(consoleErrors.filter(text => !text.includes(EXPECTED_503))).toEqual([])
})

test('opens the privacy and data handling dialog from the footer', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Privacy and data handling' }).click()
  const dialog = page.getByRole('dialog', { name: 'Privacy and data handling' })
  await expect(dialog).toBeVisible()
  await expect(dialog.getByText(/Anonymous threads expire 24 hours/)).toBeVisible()
  await dialog.getByRole('button', { name: 'Close' }).click()
  await expect(dialog).toBeHidden()
})

test('serves deep links and keeps API paths out of the shell', async ({ page, request }) => {
  await page.goto('/threads/abc')
  await expect(page.getByRole('heading', { name: 'Research Agent' })).toBeVisible()
  expect((await request.get('/docs')).status()).toBe(404)
  expect((await request.get('/api/v1/nope')).status()).toBe(404)
  expect((await request.get('/healthz')).status()).toBe(200)
})

test('sends the expected document security headers', async ({ page }) => {
  const response = await page.goto('/')
  expect(response).not.toBeNull()
  const headers = response?.headers() ?? {}
  expect(headers['content-security-policy']).toContain('challenges.cloudflare.com')
  expect(headers['x-frame-options']).toBe('DENY')
  expect(headers['x-content-type-options']).toBe('nosniff')
})
