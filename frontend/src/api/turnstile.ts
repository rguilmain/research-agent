import { ApiError } from './errors'
import { csrfHeaders } from './session'

/** Cloudflare's explicit-render loader; no npm dependency is required. */
export const TURNSTILE_SCRIPT =
  'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit'

type RenderOptions = {
  sitekey: string
  action: string
  theme: 'auto'
  callback: (token: string) => void
  'error-callback': () => void
  'expired-callback': () => void
}
export type TurnstileApi = {
  render: (element: HTMLElement, options: RenderOptions) => string
  reset: (widgetId: string) => void
  remove: (widgetId: string) => void
}
declare global {
  interface Window { turnstile?: TurnstileApi }
}

let loading: Promise<TurnstileApi> | null = null

/** Load Cloudflare's script once; a failed load is not cached so a retry can succeed. */
export function loadTurnstile(): Promise<TurnstileApi> {
  if (window.turnstile) return Promise.resolve(window.turnstile)
  loading ??= new Promise<TurnstileApi>((resolve, reject) => {
    const script = document.createElement('script')
    script.src = TURNSTILE_SCRIPT
    script.async = true
    script.onload = () =>
      window.turnstile ? resolve(window.turnstile) : reject(new Error('Turnstile unavailable'))
    script.onerror = () => reject(new Error('Turnstile unavailable'))
    document.head.appendChild(script)
  }).catch(error => {
    loading = null
    throw error
  })
  return loading
}

/** Send a widget token to our server, which is the only caller of siteverify. */
export async function verifyHuman(token: string): Promise<string> {
  const response = await fetch('/api/v1/turnstile/verify', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
    body: JSON.stringify({ token }),
  })
  if (!response.ok) throw new ApiError(response.status)
  const body: unknown = await response.json()
  if (
    typeof body !== 'object' || body === null ||
    !('human_verified_until' in body) || typeof body.human_verified_until !== 'string'
  ) {
    throw new Error('Invalid verification response')
  }
  return body.human_verified_until
}

/** Drop the cached loader promise; only tests need this. */
export function resetTurnstileLoader(): void {
  loading = null
}
