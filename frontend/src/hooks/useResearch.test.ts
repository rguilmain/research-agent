import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { loadIdentity, resetIdentity } from '../api/session'
import { useResearch } from './useResearch'

const id = '00000000-0000-4000-8000-000000000001'
function response(events: unknown[]) {
  return new Response(new ReadableStream<Uint8Array>({ start(controller) {
    controller.enqueue(new TextEncoder().encode(events.map(e => `data: ${JSON.stringify(e)}\n\n`).join('')))
    controller.close()
  } }), { status: 200 })
}
const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200 })
const thread = { id, title: 'Saved question', model_id: 'model-a', demo: false,
  created_at: '2026-11-16T00:00:00Z', updated_at: '2026-11-16T00:00:00Z', expires_at: null }
const history = { items: [{ role: 'user', text: 'Saved question', sources: [] },
  { role: 'assistant', text: 'Saved answer', sources: [{ url: 'https://example.org', title: 'Evidence' }] }],
  status: 'completed', can_continue: true, demo: false, truncated: false }

afterEach(() => { vi.unstubAllGlobals(); resetIdentity() })

describe('thread continuity', () => {
  it('reuses the server thread, resets explicitly, and separates models', async () => {
    const fetch = vi.fn().mockImplementation(() => Promise.resolve(response([
      { type: 'thread', thread_id: id }, { type: 'done' },
    ])))
    vi.stubGlobal('fetch', fetch)
    const { result } = renderHook(() => useResearch())
    const ask = async (model = 'model-a') => {
      await act(async () => { await result.current.research('question', model) })
    }
    await ask()
    expect(JSON.parse(fetch.mock.calls[0][1].body)).not.toHaveProperty('thread_id')
    await ask()
    expect(JSON.parse(fetch.mock.calls[1][1].body).thread_id).toBe(id)
    await ask('model-b')
    expect(JSON.parse(fetch.mock.calls[2][1].body)).not.toHaveProperty('thread_id')
    act(() => result.current.reset())
    await ask('model-b')
    expect(JSON.parse(fetch.mock.calls[3][1].body)).not.toHaveProperty('thread_id')
  })

  it('opens saved history, exposes turns, and blocks continuation when not allowed', async () => {
    const fetch = vi.fn().mockResolvedValue(json(history))
    vi.stubGlobal('fetch', fetch)
    const { result } = renderHook(() => useResearch())
    await act(async () => { await result.current.openThread(thread) })
    expect(result.current.turns).toHaveLength(2)
    expect(result.current.answer).toBe('Saved answer')
    expect(result.current.sources[0].url).toBe('https://example.org')
    expect(result.current.canContinue).toBe(true)
    fetch.mockReset()
    fetch.mockResolvedValueOnce(json({ ...history, status: 'cancelled', can_continue: false }))
    await act(async () => { await result.current.openThread(thread) })
    expect(result.current.canContinue).toBe(false)
    expect(result.current.notice).toContain('cancelled')
    await act(async () => { await result.current.research('blocked', 'model-a') })
    expect(fetch).toHaveBeenCalledTimes(1)
    act(() => result.current.reset())
    expect(result.current.turns).toEqual([])
    expect(result.current.activeThread).toBeNull()
    expect(result.current.canContinue).toBe(true)
    fetch.mockResolvedValueOnce(response([{ type: 'done' }]))
    await act(async () => { await result.current.research('allowed', 'model-a') })
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('reports an unreadable history without leaving stale turns', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: 404 })))
    const { result } = renderHook(() => useResearch())
    await act(async () => { await result.current.openThread(thread) })
    await waitFor(() => expect(result.current.error).toContain('404'))
    expect(result.current.turns).toEqual([])
    expect(result.current.busy).toBe(false)
  })

  it('sends the session CSRF token with the research request', async () => {
    const me = { kind: 'anonymous', user: null, csrf_token: 'csrf-value', login_enabled: false }
    const fetch = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(me), { status: 200 }))
      .mockResolvedValueOnce(response([{ type: 'done' }]))
    vi.stubGlobal('fetch', fetch)
    await loadIdentity()
    const { result } = renderHook(() => useResearch())
    await act(async () => { await result.current.research('question', 'model-a') })
    const headers = fetch.mock.calls[1][1].headers as Record<string, string>
    expect(headers['X-CSRF-Token']).toBe('csrf-value')
    expect(headers['Content-Type']).toBe('application/json')
  })

  it('discards interrupted threads instead of automatically replaying them', async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(response([{ type: 'thread', thread_id: id }]))
      .mockResolvedValueOnce(response([{ type: 'done' }]))
    vi.stubGlobal('fetch', fetch)
    const { result } = renderHook(() => useResearch())
    await act(async () => { await result.current.research('question', 'model') })
    expect(result.current.error).toContain('unexpectedly')
    await act(async () => { await result.current.research('retry', 'model') })
    expect(JSON.parse(fetch.mock.calls[1][1].body)).not.toHaveProperty('thread_id')
  })

  it('records the remaining daily allowance from the quota event', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response([
      { type: 'thread', thread_id: id }, { type: 'quota', limit: 3, remaining: 1 },
      { type: 'token', text: 'Answer' }, { type: 'done' },
    ])))
    const { result } = renderHook(() => useResearch())
    expect(result.current.quota).toBeNull()
    await act(async () => { await result.current.research('question', 'model-a') })
    expect(result.current.quota).toEqual({ limit: 3, remaining: 1 })
    expect(result.current.error).toBe('')
  })

  it('explains a daily quota refusal using the problem code', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ type: 'about:blank', title: 'Daily quota exceeded', status: 429,
        code: 'quota_exceeded', request_id: 'r' }), { status: 429 })))
    const { result } = renderHook(() => useResearch())
    await act(async () => { await result.current.research('question', 'model-a') })
    await waitFor(() => expect(result.current.error).toContain('Daily question limit reached'))
  })
})