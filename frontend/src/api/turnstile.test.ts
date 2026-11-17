import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, describeFailure } from './errors'
import { loadTurnstile, resetTurnstileLoader, TURNSTILE_SCRIPT, verifyHuman } from './turnstile'

const script = () => document.head.querySelector<HTMLScriptElement>(`script[src="${TURNSTILE_SCRIPT}"]`)

afterEach(() => {
  vi.unstubAllGlobals()
  resetTurnstileLoader()
  document.head.querySelectorAll('script').forEach(node => node.remove())
  delete window.turnstile
})

describe('turnstile loader', () => {
  it('reuses an already loaded api', async () => {
    const api = { render: vi.fn(), reset: vi.fn(), remove: vi.fn() }
    window.turnstile = api
    await expect(loadTurnstile()).resolves.toBe(api)
    expect(script()).toBeNull()
  })

  it('injects the script once and shares the pending promise', async () => {
    const first = loadTurnstile()
    const second = loadTurnstile()
    expect(script()).not.toBeNull()
    expect(document.head.querySelectorAll(`script[src="${TURNSTILE_SCRIPT}"]`)).toHaveLength(1)
    const api = { render: vi.fn(), reset: vi.fn(), remove: vi.fn() }
    window.turnstile = api
    script()!.onload!(new Event('load'))
    await expect(first).resolves.toBe(api)
    await expect(second).resolves.toBe(api)
  })

  it('does not cache a failed load', async () => {
    const failed = loadTurnstile()
    script()!.onerror!(new Event('error'))
    await expect(failed).rejects.toThrow('Turnstile unavailable')
    loadTurnstile()
    expect(document.head.querySelectorAll(`script[src="${TURNSTILE_SCRIPT}"]`)).toHaveLength(2)
  })
})

describe('verifyHuman', () => {
  it('posts the token with the CSRF header and returns the window', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ human_verified_until: '2026-11-17T16:00:00+00:00' }), { status: 200 }))
    vi.stubGlobal('fetch', fetch)
    await expect(verifyHuman('XXXX.DUMMY.TOKEN.XXXX')).resolves.toBe('2026-11-17T16:00:00+00:00')
    const [url, init] = fetch.mock.calls[0]
    expect(url).toBe('/api/v1/turnstile/verify')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({ token: 'XXXX.DUMMY.TOKEN.XXXX' })
  })

  it('fails on a refusal and on an unusable body', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ code: 'turnstile_failed' }), { status: 403 })))
    await expect(verifyHuman('token')).rejects.toBeInstanceOf(ApiError)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: 200 })))
    await expect(verifyHuman('token')).rejects.toThrow('Invalid verification response')
  })
})

describe('human-check failure copy', () => {
  it('explains the three turnstile problem codes', () => {
    expect(describeFailure(403, 'turnstile_required')).toContain('confirm you are human')
    expect(describeFailure(403, 'turnstile_failed')).toContain('Human check failed')
    expect(describeFailure(503, 'turnstile_unavailable')).toContain('temporarily unavailable')
  })
})
