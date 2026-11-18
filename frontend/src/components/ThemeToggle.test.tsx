import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ThemeProvider } from '@mui/material'
import { afterEach, describe, expect, it } from 'vitest'
import ThemeToggle from './ThemeToggle'
import { MODE_STORAGE_KEY, theme } from '../theme'

function renderToggle() {
  return render(
    <ThemeProvider theme={theme} defaultMode="system" modeStorageKey={MODE_STORAGE_KEY}>
      <ThemeToggle />
    </ThemeProvider>,
  )
}

afterEach(() => { window.localStorage.clear() })

describe('ThemeToggle', () => {
  it('offers the three modes and starts on the system mode', () => {
    renderToggle()
    expect(screen.getByRole('group', { name: 'Color theme' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'System theme' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Light theme' })).toHaveAttribute('aria-pressed', 'false')
  })

  // The <html> attribute is verified in the browser suite (e2e/smoke.spec.ts); jsdom does not
  // resolve the color scheme, so these tests cover the control state and persistence only.
  it('persists the chosen mode so a reload keeps it', async () => {
    const view = renderToggle()
    fireEvent.click(screen.getByRole('button', { name: 'Dark theme' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Dark theme' }))
      .toHaveAttribute('aria-pressed', 'true'))
    expect(window.localStorage.getItem(MODE_STORAGE_KEY)).toBe('dark')
    view.unmount()
    renderToggle()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Dark theme' }))
      .toHaveAttribute('aria-pressed', 'true'))
  })
})
