import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useResearch } from './useResearch'

const id = '00000000-0000-4000-8000-000000000001'
function response(events: unknown[]) {
  return new Response(new ReadableStream<Uint8Array>({ start(controller) {
    controller.enqueue(new TextEncoder().encode(events.map(e => `data: ${JSON.stringify(e)}\n\n`).join('')))
    controller.close()
  } }), { status: 200 })
}

afterEach(() => vi.unstubAllGlobals())

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
})