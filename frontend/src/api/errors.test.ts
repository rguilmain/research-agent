import { describe, expect, it } from 'vitest'
import { ApiError, describeFailure, problemCode } from './errors'

describe('describeFailure', () => {
  it('explains a rate limit refusal', () => {
    expect(describeFailure(429)).toBe('Too many requests. Please try again in a moment.')
  })

  it('explains a daily quota refusal and points at sign-in', () => {
    expect(describeFailure(429, 'quota_exceeded')).toContain('Daily question limit reached')
  })

  it('keeps the generic message for other statuses', () => {
    expect(describeFailure(503)).toBe('Request failed (503)')
  })

  it('carries the status on ApiError', () => {
    expect(new ApiError(429).status).toBe(429)
  })
})

describe('problemCode', () => {
  it('reads the machine-readable code from a problem JSON body', async () => {
    const response = new Response(JSON.stringify({ code: 'quota_exceeded' }), { status: 429 })
    expect(await problemCode(response)).toBe('quota_exceeded')
  })

  it('returns undefined for a body without a code', async () => {
    expect(await problemCode(new Response('not json', { status: 503 }))).toBeUndefined()
    expect(await problemCode(new Response(JSON.stringify({ detail: 'x' }), { status: 403 }))).toBeUndefined()
  })
})