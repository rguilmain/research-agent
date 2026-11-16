import { describe, expect, it } from 'vitest'
import { readEvents, validateEvent } from './stream'

describe('SSE parser', () => {
  it.each([null, { type: 'unknown' }, { type: 'token', text: 12 },
    { type: 'done', secret: 'key' }, { type: 'step', tool: 'shell', status: 'started' },
    { type: 'sources', items: [{ url: 'javascript:bad', title: 'Bad' }] },
    { type: 'thread', thread_id: 'not-a-uuid' },
    { type: 'thread', thread_id: '00000000-0000-4000-8000-000000000001', extra: true },
  ])('rejects malformed event %#', value => {
    expect(() => validateEvent(value)).toThrow('Invalid research event')
  })
  it('validates usage details as subsets rather than extra totals', () => {
    const usage = { type: 'usage', input_tokens: 100, output_tokens: 30, total_tokens: 130,
      cached_input_tokens: 40, reasoning_tokens: 20, model_calls: 1,
      tool_calls: { tavily_search: 1 }, estimated: false }
    expect(validateEvent(usage)).toEqual(usage)
    expect(() => validateEvent({ ...usage, total_tokens: 190 })).toThrow()
  })
  it('validates server-issued thread ids', () => {
    const event = { type: 'thread', thread_id: '00000000-0000-4000-8000-000000000001' }
    expect(validateEvent(event)).toEqual(event)
  })
  it('handles split frames, CRLF, keepalive comments, and multibyte text', async () => {
    const bytes = new TextEncoder().encode(': heartbeat\r\n\r\ndata: {"type":"token","text":"café"}\r\n\r\ndata: {"type":"done"}\n\n')
    const stream = new ReadableStream<Uint8Array>({ start(controller) {
      for (const byte of bytes) controller.enqueue(new Uint8Array([byte]))
      controller.close()
    } })
    const events = []
    for await (const event of readEvents(stream)) events.push(event)
    expect(events).toEqual([{ type: 'token', text: 'café' }, { type: 'done' }])
  })
  it('rejects incomplete frames', async () => {
    const stream = new ReadableStream<Uint8Array>({ start(controller) {
      controller.enqueue(new TextEncoder().encode('data: {'))
      controller.close()
    } })
    await expect(async () => { for await (const event of readEvents(stream)) void event }).rejects.toThrow('Incomplete')
  })
})