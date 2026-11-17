import { ApiError } from './errors'

export type User = { login: string; avatar_url: string | null }
export type Me = {
  kind: 'anonymous' | 'user' | 'none'
  user: User | null
  csrf_token: string | null
  login_enabled: boolean
  turnstile_required: boolean
  human_verified_until: string | null
}

const record = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v)
const text = (v: unknown, max: number): v is string =>
  typeof v === 'string' && v.length > 0 && v.length <= max
// GitHub logins are 1-39 characters of alphanumerics or single hyphens.
const login = (v: unknown): v is string => text(v, 39) && /^[A-Za-z0-9]+(-[A-Za-z0-9]+)*$/.test(v)
const avatar = (v: unknown): v is string | null => {
  if (v === null) return true
  if (!text(v, 2000)) return false
  try {
    const url = new URL(v)
    return url.protocol === 'https:' && !!url.hostname && !url.username && !url.password
  } catch {
    return false
  }
}
/** The verification window is an ISO timestamp the server issued, not a client value. */
const verifiedUntil = (v: unknown): v is string | null =>
  v === null || (text(v, 64) && !Number.isNaN(Date.parse(v)))

export function validateMe(value: unknown): Me {
  if (
    !record(value) ||
    !['anonymous', 'user', 'none'].includes(String(value.kind)) ||
    typeof value.login_enabled !== 'boolean' ||
    typeof value.turnstile_required !== 'boolean' ||
    !verifiedUntil(value.human_verified_until) ||
    (value.csrf_token !== null && !text(value.csrf_token, 200))
  ) {
    throw new Error('Invalid identity response')
  }
  let user: User | null = null
  if (value.user !== null) {
    if (!record(value.user) || !login(value.user.login) || !avatar(value.user.avatar_url)) {
      throw new Error('Invalid identity response')
    }
    user = { login: value.user.login, avatar_url: value.user.avatar_url }
  }
  // A signed-in session without a user object would render as anonymous.
  if (value.kind === 'user' && user === null) throw new Error('Invalid identity response')
  return {
    kind: value.kind as Me['kind'],
    user,
    csrf_token: value.csrf_token,
    login_enabled: value.login_enabled,
    turnstile_required: value.turnstile_required,
    human_verified_until: value.human_verified_until,
  }
}

// The CSRF token is bound to the session cookie, so it is cached in module scope
// and refreshed by every ``/api/v1/me`` response.
let csrf: string | null = null
let identity: Me | null = null
let loading: Promise<Me> | null = null

export function csrfHeaders(): Record<string, string> {
  return csrf ? { 'X-CSRF-Token': csrf } : {}
}

export function cachedIdentity(): Me | null {
  return identity
}

export async function loadIdentity(signal?: AbortSignal): Promise<Me> {
  const response = await fetch('/api/v1/me', { signal })
  if (!response.ok) throw new ApiError(response.status)
  const value = validateMe(await response.json())
  identity = value
  csrf = value.csrf_token
  return value
}

/** Reuse the cached identity; a failed load is not cached so the next call retries. */
export function ensureIdentity(): Promise<Me> {
  if (identity) return Promise.resolve(identity)
  loading ??= loadIdentity().finally(() => {
    loading = null
  })
  return loading
}

export async function signOut(): Promise<void> {
  const response = await fetch('/auth/logout', { method: 'POST', headers: csrfHeaders() })
  if (!response.ok) throw new ApiError(response.status)
  identity = null
  csrf = null
}

export function loginUrl(nextPath: string): string {
  return `/auth/github/login?next=${encodeURIComponent(nextPath)}`
}

/** Drop cached state; only tests and sign-out need this. */
export function resetIdentity(): void {
  identity = null
  csrf = null
  loading = null
}
