/** Shared HTTP failure type so identity and thread modules never import each other. */
export class ApiError extends Error {
  readonly status: number
  constructor(status: number) {
    super(`Request failed (${status})`)
    this.status = status
  }
}
