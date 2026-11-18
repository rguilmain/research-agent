import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ThreadHistory from './ThreadHistory'
import { deleteThread, listThreads } from '../api/threads'
import type { Thread } from '../api/threads'

vi.mock('../api/threads', async importOriginal => {
  const actual = await importOriginal<typeof import('../api/threads')>()
  return { ...actual, listThreads: vi.fn(), deleteThread: vi.fn(), renameThread: vi.fn() }
})

const first: Thread = { id: '00000000-0000-4000-8000-000000000001', title: 'First question',
  model_id: 'gpt-6-luna', demo: false, created_at: '2026-11-17T00:00:00Z',
  updated_at: '2026-11-17T00:00:00Z', expires_at: null }
const second: Thread = { ...first, id: '00000000-0000-4000-8000-000000000002',
  title: 'Second question' }

function props() {
  return { busy: false, version: 0, onOpen: vi.fn(), onDeleted: vi.fn() }
}

beforeEach(() => {
  vi.mocked(listThreads).mockResolvedValue([])
  vi.mocked(deleteThread).mockResolvedValue(undefined)
})

afterEach(() => { vi.clearAllMocks() })

describe('ThreadHistory delete all', () => {
  it('deletes every saved thread only after the confirmation', async () => {
    vi.mocked(listThreads).mockResolvedValue([first, second])
    const onDeleted = vi.fn()
    render(<ThreadHistory {...props()} onDeleted={onDeleted} />)
    await screen.findByText('First question')

    fireEvent.click(screen.getByRole('button', { name: 'Delete all history' }))
    expect(screen.getByRole('heading', { name: 'Delete all saved threads?' })).toBeInTheDocument()
    expect(deleteThread).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Delete all' }))
    await waitFor(() => expect(deleteThread).toHaveBeenCalledTimes(2))
    expect(vi.mocked(deleteThread).mock.calls[0][0]).toBe(first.id)
    expect(vi.mocked(deleteThread).mock.calls[1][0]).toBe(second.id)
    expect(onDeleted).toHaveBeenCalledTimes(2)
  })

  it('keeps going after a failed delete and reports the shortfall', async () => {
    vi.mocked(listThreads).mockResolvedValue([first, second])
    vi.mocked(deleteThread).mockImplementation(async (id: string) => {
      if (id === second.id) throw new Error('busy')
    })
    const onDeleted = vi.fn()
    render(<ThreadHistory {...props()} onDeleted={onDeleted} />)
    await screen.findByText('First question')

    fireEvent.click(screen.getByRole('button', { name: 'Delete all history' }))
    fireEvent.click(screen.getByRole('button', { name: 'Delete all' }))

    expect(await screen.findByText(/1 could not be deleted/)).toBeInTheDocument()
    expect(onDeleted).toHaveBeenCalledTimes(1)
    expect(onDeleted).toHaveBeenCalledWith(first.id)
  })

  it('offers no bulk delete when nothing is saved', async () => {
    render(<ThreadHistory {...props()} />)
    expect(await screen.findByText('No saved threads.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Delete all history' })).not.toBeInTheDocument()
  })
})
