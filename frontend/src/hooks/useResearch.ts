import { useEffect, useRef, useState } from 'react'
import { readEvents } from '../api/stream'

export function useResearch() {
  const [answer, setAnswer] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [steps, setSteps] = useState<string[]>([])
  const [sources, setSources] = useState<{ url: string; title: string }[]>([])
  const controller = useRef<AbortController | null>(null)
  const thread = useRef<{ id: string; model: string } | null>(null)
  useEffect(() => () => controller.current?.abort(), [])

  async function research(query: string, model_id: string) {
    if (controller.current) return
    const abort = new AbortController()
    controller.current = abort
    setBusy(true)
    setAnswer('')
    setError('')
    setSteps([])
    setSources([])
    try {
      const response = await fetch('/api/v1/research', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, model_id,
          ...(thread.current?.model === model_id ? { thread_id: thread.current.id } : {}) }), signal: abort.signal,
      })
      if (!response.ok || !response.body) throw new Error(`Request failed (${response.status})`)
      let completed = false
      for await (const event of readEvents(response.body)) {
        switch (event.type) {
          case 'thread': thread.current = { id: event.thread_id, model: model_id }; break
          case 'token': setAnswer(value => value + event.text); break
          case 'step': setSteps(value => [...value, `${event.tool}: ${event.status}`]); break
          case 'sources': setSources(event.items); break
          case 'error': throw new Error(event.message)
          case 'done': completed = true; break
        }
      }
      if (!completed) throw new Error('Research connection ended unexpectedly')
    } catch (cause) {
      thread.current = null
      if (!abort.signal.aborted) setError(cause instanceof Error ? cause.message : 'Research failed')
    } finally {
      controller.current = null
      setBusy(false)
    }
  }
  return { answer, error, busy, steps, sources, research,
    reset: () => { thread.current = null },
    stop: () => { thread.current = null; controller.current?.abort() } }
}