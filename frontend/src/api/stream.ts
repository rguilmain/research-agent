export type ResearchEvent =
  | { type: 'run_started'; model_id: string; demo: boolean }
  | { type: 'thread'; thread_id: string }
  | { type: 'token'; text: string }
  | { type: 'step'; tool: string; status: 'started' | 'completed' }
  | { type: 'sources'; items: { url: string; title: string }[] }
  | { type: 'error'; code: string; message: string; request_id: string }
  | { type: 'done' }
  | { type: 'quota'; limit: number; remaining: number }
  | { type: 'usage'; input_tokens: number; output_tokens: number; total_tokens: number;
      cached_input_tokens: number; reasoning_tokens: number; model_calls: number;
      tool_calls: Record<string, number>; estimated: boolean }

const tools = ['tavily_search', 'wikipedia', 'wolfram_alpha']
const record = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v)
const text = (v: unknown, max = 200): v is string =>
  typeof v === 'string' && v.length > 0 && v.length <= max
const count = (v: unknown): v is number =>
  typeof v === 'number' && Number.isSafeInteger(v) && v >= 0 && v <= 1_000_000_000
const keys = (v: Record<string, unknown>, names: string[]) =>
  Object.keys(v).length === names.length && names.every(n => Object.hasOwn(v, n))
const safeUrl = (v: unknown) => {
  if (!text(v, 2000) || /\s/.test(v)) return false
  try {
    const u = new URL(v)
    return ['http:', 'https:'].includes(u.protocol) && !!u.hostname && !u.username && !u.password
  } catch { return false }
}

export function validateEvent(value: unknown): ResearchEvent {
  if (!record(value)) throw new Error('Invalid research event')
  const v = value
  let valid = false
  switch (v.type) {
    case 'run_started': valid = keys(v, ['type', 'model_id', 'demo']) && text(v.model_id) && typeof v.demo === 'boolean'; break
    case 'thread': valid = keys(v, ['type', 'thread_id']) && typeof v.thread_id === 'string' && /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(v.thread_id); break
    case 'token': valid = keys(v, ['type', 'text']) && text(v.text, 32000); break
    case 'step': valid = keys(v, ['type', 'tool', 'status']) && tools.includes(String(v.tool)) && ['started', 'completed'].includes(String(v.status)); break
    case 'sources': valid = keys(v, ['type', 'items']) && Array.isArray(v.items) && v.items.length <= 100 && v.items.every(s => record(s) && keys(s, ['url', 'title']) && safeUrl(s.url) && text(s.title)); break
    case 'done': valid = keys(v, ['type']); break
    case 'quota': valid = keys(v, ['type', 'limit', 'remaining']) && count(v.limit) &&
      Number(v.limit) >= 1 && count(v.remaining) && Number(v.remaining) <= Number(v.limit); break
    case 'error': valid = keys(v, ['type', 'code', 'message', 'request_id']) && ['research_failed', 'research_timeout', 'run_limit_exceeded'].includes(String(v.code)) && text(v.message) && text(v.request_id); break
    case 'usage': {
      const counts = ['input_tokens', 'output_tokens', 'total_tokens', 'cached_input_tokens', 'reasoning_tokens', 'model_calls']
      valid = keys(v, ['type', ...counts, 'tool_calls', 'estimated']) && counts.every(k => count(v[k])) &&
        typeof v.estimated === 'boolean' && record(v.tool_calls) &&
        Object.entries(v.tool_calls).every(([k, n]) => tools.includes(k) && count(n)) &&
        Number(v.total_tokens) === Number(v.input_tokens) + Number(v.output_tokens) &&
        Number(v.cached_input_tokens) <= Number(v.input_tokens) && Number(v.reasoning_tokens) <= Number(v.output_tokens)
      break
    }
  }
  if (!valid) throw new Error('Invalid research event')
  return v as ResearchEvent
}

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
        if (frame.length > 1_000_000) throw new Error('Stream frame exceeds size limit')
        buffer = buffer.slice(match.index + match[0].length)
        const data = frame.split(/\r?\n/).filter(line => line.startsWith('data:'))
          .map(line => line.slice(5).trimStart()).join('\n')
        if (data) yield validateEvent(JSON.parse(data))
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