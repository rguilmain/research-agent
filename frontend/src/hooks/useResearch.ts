import { useEffect, useRef, useState } from 'react'
import { describeFailure, problemCode } from '../api/errors'
import { csrfHeaders } from '../api/session'
import { readEvents } from '../api/stream'
import { getMessages } from '../api/threads'
import type { Source, Thread, Turn } from '../api/threads'

export function useResearch() {
  const [turns, setTurns] = useState<Turn[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [steps, setSteps] = useState<string[]>([])
  const [quota, setQuota] = useState<{ limit: number; remaining: number } | null>(null)
  const [failureCode, setFailureCode] = useState<string | undefined>(undefined)
  const [activeThread, setActiveThread] = useState<{ id: string; model: string } | null>(null)
  const [canContinue, setCanContinue] = useState(true)
  const [notice, setNotice] = useState('')
  const [historyVersion, setHistoryVersion] = useState(0)
  const controller = useRef<AbortController | null>(null)
  const thread = useRef<{ id: string; model: string } | null>(null)
  const allowed = useRef(true)
  useEffect(() => () => controller.current?.abort(), [])

  function continuation(value: boolean) { allowed.current = value; setCanContinue(value) }
  function select(value: { id: string; model: string } | null) {
    thread.current = value
    setActiveThread(value)
  }
  function reset() {
    if (controller.current) return
    select(null)
    setTurns([])
    setSteps([])
    setError('')
    setNotice('')
    continuation(true)
  }

  async function openThread(item: Thread) {
    if (controller.current) return
    const abort = new AbortController()
    controller.current = abort
    setBusy(true)
    select({ id: item.id, model: item.model_id })
    continuation(false)
    setTurns([])
    setSteps([])
    setError('')
    setNotice('')
    try {
      const history = await getMessages(item.id, abort.signal)
      setTurns(history.items)
      continuation(history.can_continue)
      setNotice(history.demo ? 'Demo thread — answers are not saved.' :
        !history.can_continue ? `Thread ${history.status}. Start a new thread; interrupted work is never replayed.` :
        history.truncated ? 'Showing a bounded excerpt of this conversation.' : '')
    } catch (cause) {
      if (!abort.signal.aborted) setError(cause instanceof Error ? cause.message : 'Cannot open history')
    } finally { controller.current = null; setBusy(false) }
  }

  async function research(query: string, model_id: string) {
    if (controller.current || !allowed.current) return
    const abort = new AbortController()
    controller.current = abort
    setBusy(true)
    setError('')
    setSteps([])
    setNotice('')
    setFailureCode(undefined)
    const continuing = thread.current?.model === model_id
    setTurns(value => [...(continuing ? value : []), { role: 'user', text: query, sources: [] },
      { role: 'assistant', text: '', sources: [] }])
    const updateAnswer = (text?: string, sources?: Source[]) => setTurns(value => value.map((turn, index) =>
      index === value.length - 1 ? { ...turn, text: turn.text + (text ?? ''), sources: sources ?? turn.sources } : turn))
    try {
      const response = await fetch('/api/v1/research', {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
        body: JSON.stringify({ query, model_id,
          ...(continuing ? { thread_id: thread.current!.id } : {}) }), signal: abort.signal,
      })
      if (!response.ok || !response.body) {
        const code = response.ok ? undefined : await problemCode(response)
        setFailureCode(code)
        throw new Error(describeFailure(response.status, code))
      }
      let completed = false
      for await (const event of readEvents(response.body)) {
        switch (event.type) {
          case 'thread': select({ id: event.thread_id, model: model_id }); setHistoryVersion(v => v + 1); break
          case 'token': updateAnswer(event.text); break
          case 'step': setSteps(value => [...value, `${event.tool}: ${event.status}`]); break
          case 'sources': updateAnswer(undefined, event.items); break
          case 'quota': setQuota({ limit: event.limit, remaining: event.remaining }); break
          case 'error': throw new Error(event.message)
          case 'done': completed = true; break
        }
      }
      if (!completed) throw new Error('Research connection ended unexpectedly')
    } catch (cause) {
      select(null)
      continuation(true)
      setNotice('Research interrupted. The next question starts a new thread.')
      // Never leave a partial answer looking final: reset declared state, keep the error.
      setTurns([])
      setSteps([])
      if (!abort.signal.aborted) setError(cause instanceof Error ? cause.message : 'Research failed')
    } finally {
      controller.current = null
      setBusy(false)
      setHistoryVersion(v => v + 1)
    }
  }
  const last = turns.at(-1)
  return { turns, answer: last?.role === 'assistant' ? last.text : '', sources: last?.sources ?? [],
    error, busy, steps, quota, failureCode, research, reset, openThread, activeThread, canContinue,
    notice, historyVersion, stop: () => { controller.current?.abort() } }
}