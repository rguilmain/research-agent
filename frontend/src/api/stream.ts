export type ResearchEvent =
  | { type: 'run_started'; model_id: string; demo: boolean }
  | { type: 'token'; text: string }
  | { type: 'step'; tool: string; status: string }
  | { type: 'sources'; items: { url: string; title: string }[] }
  | { type: 'error'; code: string; message: string; request_id: string }
  | { type: 'done' }

// Incremental decoding is required: chunks may split UTF-8 characters or SSE frames.
export async function* readEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<ResearchEvent> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    while (true) {
      const { value, done } = await reader.read()
      buffer += decoder.decode(value, { stream: !done })
      let match: RegExpExecArray | null
      while ((match = /\r?\n\r?\n/.exec(buffer)) !== null) {
        const frame = buffer.slice(0, match.index)
        buffer = buffer.slice(match.index + match[0].length)
        const data = frame.split(/\r?\n/).filter(line => line.startsWith('data:'))
          .map(line => line.slice(5).trimStart()).join('\n')
        if (data) yield JSON.parse(data) as ResearchEvent
      }
      if (done) break
      if (buffer.length > 1_000_000) throw new Error('Stream frame exceeds size limit')
    }
    if (buffer.trim()) throw new Error('Incomplete stream frame')
  } finally {
    await reader.cancel()
    reader.releaseLock()
  }
}