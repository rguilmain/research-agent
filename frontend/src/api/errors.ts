/** Shared HTTP failure type so identity and thread modules never import each other. */
export class ApiError extends Error {
  readonly status: number
  constructor(status: number) {
    super(`Request failed (${status})`)
    this.status = status
  }
}

/** User-facing text for a failed response; 429 means admission control refused us. */
export function describeFailure(status: number, code?: string): string {
  switch (code) {
    case 'quota_exceeded':
      return 'Daily question limit reached. Sign in for a larger allowance, or try again tomorrow.'
    case 'turnstile_required':
      return 'Please confirm you are human, then ask again.'
    case 'turnstile_failed':
      return 'Human check failed. Please try again.'
    case 'turnstile_unavailable':
      return 'Human verification is temporarily unavailable. Please try again shortly.'
    case 'budget_exhausted':
      return 'The daily research budget has been reached. Please try again tomorrow.'
    case 'too_many_active_runs':
      return 'Too many research runs are in progress. Please wait a moment and try again.'
    default:
      break
  }
  return status === 429
    ? 'Too many requests. Please try again in a moment.'
    : `Request failed (${status})`
}

/** Machine-readable problem code from an error body, when the service sent one. */
export async function problemCode(response: Response): Promise<string | undefined> {
  try {
    const body: unknown = await response.json()
    if (typeof body === 'object' && body !== null && 'code' in body && typeof body.code === 'string') {
      return body.code
    }
  } catch { /* a non-JSON error body simply has no code */ }
  return undefined
}
