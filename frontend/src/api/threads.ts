import { validateEvent } from './stream'

export type Source = { url: string; title: string }
export type Turn = { role: 'user' | 'assistant'; text: string; sources: Source[] }
export type Thread = { id: string; title: string; model_id: string; demo: boolean;
  created_at: string; updated_at: string; expires_at: string | null }
export type History = { items: Turn[]; status: string; can_continue: boolean; demo: boolean; truncated: boolean }
const record = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const text = (v: unknown, max: number): v is string => typeof v === 'string' && v.length > 0 && v.length <= max
const uuid = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(v)
const date = (v: unknown): v is string => text(v, 100) && Number.isFinite(Date.parse(v))

export class ApiError extends Error {
  readonly status: number
  constructor(status: number) { super(`Request failed (${status})`); this.status = status }
}

export function validateThread(v: unknown): Thread {
  if (!record(v) || !uuid(v.id) || !text(v.title, 200) || !text(v.model_id, 100) ||
      typeof v.demo !== 'boolean' || !date(v.created_at) || !date(v.updated_at) ||
      (v.expires_at !== null && !date(v.expires_at))) throw new Error('Invalid thread response')
  return { id: v.id, title: v.title, model_id: v.model_id, demo: v.demo,
    created_at: v.created_at, updated_at: v.updated_at, expires_at: v.expires_at }
}

export function validateHistory(v: unknown): History {
  if (!record(v) || !Array.isArray(v.items) || v.items.length > 100 ||
      !['idle', 'running', 'completed', 'failed', 'cancelled', 'abandoned'].includes(String(v.status)) ||
      typeof v.can_continue !== 'boolean' || typeof v.demo !== 'boolean' || typeof v.truncated !== 'boolean' ||
      (v.can_continue && (v.demo || v.status !== 'completed'))) throw new Error('Invalid history response')
  const items: Turn[] = v.items.map((item: unknown) => {
    if (!record(item) || !['user', 'assistant'].includes(String(item.role)) || !text(item.text, 32000)) {
      throw new Error('Invalid history message')
    }
    const event = validateEvent({ type: 'sources', items: item.sources })
    if (event.type !== 'sources') throw new Error('Invalid history sources')
    return { role: item.role as Turn['role'], text: item.text, sources: event.items }
  })
  return { items, status: String(v.status), can_continue: v.can_continue, demo: v.demo, truncated: v.truncated }
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  const response = await fetch(`/api/v1/threads${path}`, init)
  if (!response.ok) throw new ApiError(response.status)
  return response
}
const path = (id: string) => {
  if (!uuid(id)) throw new Error('Invalid thread ID')
  return `/${id}`
}

export async function listThreads(offset = 0, signal?: AbortSignal): Promise<Thread[]> {
  const value: unknown = await (await request(`?limit=25&offset=${offset}`, { signal })).json()
  if (!record(value) || !Array.isArray(value.items) || value.items.length > 25) throw new Error('Invalid thread list')
  return value.items.map(validateThread)
}
export async function getThread(id: string, signal?: AbortSignal): Promise<Thread> {
  return validateThread(await (await request(path(id), { signal })).json())
}
export async function getMessages(id: string, signal?: AbortSignal): Promise<History> {
  return validateHistory(await (await request(`${path(id)}/messages`, { signal })).json())
}
export async function renameThread(id: string, title: string): Promise<Thread> {
  return validateThread(await (await request(path(id), { method: 'PATCH',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title }) })).json())
}
export async function deleteThread(id: string): Promise<void> {
  await request(path(id), { method: 'DELETE' })
}