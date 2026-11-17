import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from './errors'
import { cachedIdentity, csrfHeaders, ensureIdentity, loadIdentity, loginUrl, resetIdentity,
  signOut, validateMe } from './session'

const anonymous = { kind: 'anonymous', user: null, csrf_token: 'csrf-value', login_enabled: true }
const signedIn = { kind: 'user', user: { login: 'octocat', avatar_url: 'https://avatars.example/o.png' },
  csrf_token: 'csrf-user', login_enabled: true }

afterEach(() => { vi.unstubAllGlobals(); resetIdentity() })

function stub(value: unknown, status = 200) {
  const body = status === 204 ? null : JSON.stringify(value)
  const fetch = vi.fn().mockResolvedValue(new Response(body, { status }))
  vi.stubGlobal('fetch', fetch)
  return fetch
}

describe('identity API', () => {
  it('accepts server-owned identity shapes', () => {
    expect(validateMe(anonymous)).toEqual(anonymous)
    expect(validateMe(signedIn)).toEqual(signedIn)
    expect(validateMe({ ...signedIn, user: { login: 'octocat', avatar_url: null } }).user)
      .toEqual({ login: 'octocat', avatar_url: null })
  })

  it('rejects identities that could not be rendered safely', () => {
    expect(() => validateMe({ ...anonymous, kind: 'admin' })).toThrow()
    expect(() => validateMe({ ...anonymous, csrf_token: 42 })).toThrow()
    expect(() => validateMe({ ...signedIn, user: null })).toThrow()
    expect(() => validateMe({ ...signedIn, user: { login: 'bad login', avatar_url: null } })).toThrow()
    expect(() => validateMe({ ...signedIn,
      user: { login: 'octocat', avatar_url: 'javascript:alert(1)' } })).toThrow()
    expect(() => validateMe({ ...signedIn,
      user: { login: 'octocat', avatar_url: 'http://insecure.example/a.png' } })).toThrow()
  })

  it('loads /api/v1/me and exposes the CSRF token to unsafe calls', async () => {
    const fetch = stub(signedIn)
    expect(csrfHeaders()).toEqual({})
    const me = await loadIdentity()
    expect(me).toEqual(signedIn)
    expect(fetch.mock.calls[0][0]).toBe('/api/v1/me')
    expect(csrfHeaders()).toEqual({ 'X-CSRF-Token': 'csrf-user' })
    expect(cachedIdentity()).toEqual(signedIn)
  })

  it('surfaces a failed identity request without caching it', async () => {
    stub({ detail: 'Storage unavailable' }, 503)
    await expect(loadIdentity()).rejects.toBeInstanceOf(ApiError)
    expect(cachedIdentity()).toBeNull()
    expect(csrfHeaders()).toEqual({})
  })

  it('reuses a loaded identity instead of calling the server twice', async () => {
    const fetch = stub(anonymous)
    const [first, second] = await Promise.all([ensureIdentity(), ensureIdentity()])
    expect(first).toEqual(anonymous)
    expect(second).toEqual(anonymous)
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(await ensureIdentity()).toEqual(anonymous)
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('retries after a failed ensure and clears the cached token on sign-out', async () => {
    const failed = stub({ detail: 'Storage unavailable' }, 503)
    await expect(ensureIdentity()).rejects.toBeInstanceOf(ApiError)
    vi.unstubAllGlobals()
    const ok = stub(anonymous)
    expect(await ensureIdentity()).toEqual(anonymous)
    expect(failed).toHaveBeenCalledTimes(1)
    expect(ok).toHaveBeenCalledTimes(1)
    vi.unstubAllGlobals()
    const logout = stub(null, 204)
    await signOut()
    expect(logout.mock.calls[0][0]).toBe('/auth/logout')
    expect((logout.mock.calls[0][1].headers as Record<string, string>)['X-CSRF-Token'])
      .toBe('csrf-value')
    expect(cachedIdentity()).toBeNull()
    expect(csrfHeaders()).toEqual({})
  })

  it('reports a rejected sign-out without clearing local state', async () => {
    stub(anonymous)
    await loadIdentity()
    vi.unstubAllGlobals()
    stub({ detail: 'CSRF token missing or invalid' }, 403)
    await expect(signOut()).rejects.toBeInstanceOf(ApiError)
    expect(cachedIdentity()).toEqual(anonymous)
  })

  it('builds a same-origin sign-in path with an encoded next target', () => {
    expect(loginUrl('/')).toBe('/auth/github/login?next=%2F')
    expect(loginUrl('/threads?a=1&b=2')).toBe('/auth/github/login?next=%2Fthreads%3Fa%3D1%26b%3D2')
  })
})
