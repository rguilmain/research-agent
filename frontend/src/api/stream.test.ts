import { describe, expect, it } from 'vitest'
import { readEvents } from './stream'

describe('SSE parser', () => {
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