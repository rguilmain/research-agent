import { describe, expect, it } from 'vitest'
import { ApiError, describeFailure } from './errors'

describe('describeFailure', () => {
  it('explains a rate limit refusal', () => {
    expect(describeFailure(429)).toBe('Too many requests. Please try again in a moment.')
  })

  it('keeps the generic message for other statuses', () => {
    expect(describeFailure(503)).toBe('Request failed (503)')
  })

  it('carries the status on ApiError', () => {
    expect(new ApiError(429).status).toBe(429)
  })
})