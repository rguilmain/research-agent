import { render, screen, waitFor } from '@testing-library/react'
import { act } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import HumanCheck from './HumanCheck'
import type { TurnstileApi } from '../api/turnstile'

type Options = Parameters<TurnstileApi['render']>[1]

const WINDOW = '2026-11-17T16:00:00+00:00'

function stubWidget() {
  const reset = vi.fn<(widgetId: string) => void>()
  const remove = vi.fn<(widgetId: string) => void>()
  const widget: { id: string; options: Options | null;
    reset: typeof reset; remove: typeof remove } = {
    id: 'widget-1', options: null, reset, remove,
  }
  const api: TurnstileApi = {
    render: (_element, options) => { widget.options = options; return widget.id },
    reset,
    remove,
  }
  window.turnstile = api
  return widget
}

afterEach(() => {
  vi.unstubAllGlobals()
  delete window.turnstile
})

describe('HumanCheck', () => {
  it('renders the widget for the research action', async () => {
    const widget = stubWidget()
    render(<HumanCheck siteKey="1x00000000000000000000AA" onVerified={vi.fn()} />)
    await waitFor(() => expect(widget.options).not.toBeNull())
    expect(widget.options?.sitekey).toBe('1x00000000000000000000AA')
    expect(widget.options?.action).toBe('research')
  })

  it('verifies a solved token and reports success', async () => {
    const widget = stubWidget()
    const fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ human_verified_until: WINDOW }), { status: 200 }))
    vi.stubGlobal('fetch', fetch)
    const onVerified = vi.fn()
    render(<HumanCheck siteKey="1x00000000000000000000AA" onVerified={onVerified} />)
    await waitFor(() => expect(widget.options).not.toBeNull())
    await act(async () => { widget.options!.callback('XXXX.DUMMY.TOKEN.XXXX') })
    await waitFor(() => expect(onVerified).toHaveBeenCalledTimes(1))
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ token: 'XXXX.DUMMY.TOKEN.XXXX' })
  })

  it('shows a retry message and resets when the server refuses', async () => {
    const widget = stubWidget()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ code: 'turnstile_failed' }), { status: 403 })))
    const onVerified = vi.fn()
    render(<HumanCheck siteKey="1x00000000000000000000AA" onVerified={onVerified} />)
    await waitFor(() => expect(widget.options).not.toBeNull())
    await act(async () => { widget.options!.callback('bad-token') })
    await waitFor(() => expect(screen.getByRole('status').textContent).toContain('Human check failed'))
    expect(widget.reset).toHaveBeenCalledWith('widget-1')
    expect(onVerified).not.toHaveBeenCalled()
  })

  it('reports a widget error and removes the widget on unmount', async () => {
    const widget = stubWidget()
    const view = render(<HumanCheck siteKey="1x00000000000000000000AA" onVerified={vi.fn()} />)
    await waitFor(() => expect(widget.options).not.toBeNull())
    await act(async () => { widget.options!['error-callback']() })
    expect(screen.getByRole('status').textContent).toContain('Human check failed')
    view.unmount()
    expect(widget.remove).toHaveBeenCalledWith('widget-1')
  })
})
