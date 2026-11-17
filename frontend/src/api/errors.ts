/** Shared HTTP failure type so identity and thread modules never import each other. */
export class ApiError extends Error {
  readonly status: number
  constructor(status: number) {
    super(`Request failed (${status})`)
    this.status = status
  }
}

/** User-facing text for a failed response; 429 means admission control refused us. */
export function describeFailure(status: number): string {
  return status === 429
    ? 'Too many requests. Please try again in a moment.'
    : `Request failed (${status})`
}
