import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import PrivacyDialog from './PrivacyDialog'

describe('PrivacyDialog', () => {
  it('states the implemented retention facts without promising unimplemented ones', () => {
    render(<PrivacyDialog open onClose={vi.fn()} />)
    expect(screen.getByRole('heading', { name: 'Privacy and data handling' })).toBeInTheDocument()
    expect(screen.getByText(/Anonymous threads expire 24 hours/)).toBeInTheDocument()
    expect(screen.getByText(/retained for 30 days by default/)).toBeInTheDocument()
    expect(screen.getByText(/Tracing is opt-in/)).toBeInTheDocument()
    expect(screen.getByText(/Not implemented yet: encryption at rest/)).toBeInTheDocument()
  })

  it('closes on request and renders nothing while closed', () => {
    const onClose = vi.fn()
    const view = render(<PrivacyDialog open={false} onClose={onClose} />)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    view.unmount()
    render(<PrivacyDialog open onClose={onClose} />)
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
