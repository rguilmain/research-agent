import { describe, expect, it, vi, afterEach } from 'vitest'
import { ApiError, deleteThread, getMessages, listThreads, renameThread, validateHistory,
  validateThread } from './threads'
import { loadIdentity, resetIdentity } from './session'

const id = '00000000-0000-4000-8000-000000000001'
const thread = { id, title: 'Question', model_id: 'gpt-6-luna', demo: false,
  created_at: '2026-11-16T00:00:00Z', updated_at: '2026-11-16T00:00:00Z', expires_at: null }
const history = { items: [{ role: 'assistant', text: 'Answer',
  sources: [{ url: 'https://example.org', title: 'Evidence' }] }],
  status: 'completed', can_continue: true, demo: false, truncated: false }

afterEach(() => { vi.unstubAllGlobals(); resetIdentity() })

function stub(value: unknown, status = 200) {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(value), { status }))
  vi.stubGlobal('fetch', fetch)
  return fetch
}

describe('thread API', () => {
  it('accepts server-owned shapes and asks for a bounded page', async () => {
    const fetch = stub({ items: [thread] })
    expect(await listThreads(25)).toEqual([thread])
    expect(fetch.mock.calls[0][0]).toBe('/api/v1/threads?limit=25&offset=25')
    expect(validateThread(thread)).toEqual(thread)
    expect(validateHistory(history)).toEqual(history)
  })

  it('rejects unsafe or inconsistent server output instead of rendering it', () => {
    expect(() => validateThread({ ...thread, demo: 'yes' })).toThrow()
    expect(() => validateThread({ ...thread, expires_at: 'not-a-date' })).toThrow()
    expect(() => validateHistory({ ...history, can_continue: true, status: 'failed' })).toThrow()
    expect(() => validateHistory({ ...history,
      items: [{ role: 'system', text: 'hidden', sources: [] }] })).toThrow()
    expect(() => validateHistory({ ...history,
      items: [{ role: 'assistant', text: 'Answer', sources: [{ url: 'javascript:alert(1)', title: 'x' }] }] }))
      .toThrow()
  })

  it('surfaces status codes and never sends an unvalidated identifier', async () => {
    stub({ detail: 'Storage unavailable' }, 503)
    await expect(getMessages(id)).rejects.toBeInstanceOf(ApiError)
    await expect(getMessages('../etc')).rejects.toThrow('Invalid thread ID')
    await expect(deleteThread('not-a-uuid')).rejects.toThrow('Invalid thread ID')
  })

  it('keeps rename and delete on the versioned path', async () => {
    const fetch = stub(thread)
    await renameThread(id, 'Renamed')
    expect(fetch.mock.calls[0][1]).toMatchObject({ method: 'PATCH' })
    const empty = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', empty)
    await deleteThread(id)
    expect(empty).toHaveBeenCalledWith(`/api/v1/threads/${id}`,
      expect.objectContaining({ method: 'DELETE' }))
  })

  it('sends the session CSRF token on unsafe methods only', async () => {
    const me = { kind: 'anonymous', user: null, csrf_token: 'csrf-value', login_enabled: false,
      turnstile_required: false, human_verified_until: null }
    const fetch = vi.fn().mockImplementation((url: string) => {
      const body = url === '/api/v1/me' ? me : url.includes('?') ? { items: [thread] } : thread
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }))
    })
    vi.stubGlobal('fetch', fetch)
    await loadIdentity()
    await renameThread(id, 'Renamed')
    expect((fetch.mock.calls[1][1].headers as Record<string, string>)['X-CSRF-Token'])
      .toBe('csrf-value')
    await listThreads(0)
    expect(fetch.mock.calls[2][1]).not.toHaveProperty('headers')
  })
})
