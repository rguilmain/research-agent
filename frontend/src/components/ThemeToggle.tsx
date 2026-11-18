import { ToggleButton, ToggleButtonGroup } from '@mui/material'
import { useColorScheme } from '@mui/material/styles'

type Mode = 'system' | 'light' | 'dark'

const MODES: { value: Mode; label: string }[] = [
  { value: 'system', label: 'System' },
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
]

/**
 * Remembers the light/dark/system choice in localStorage through the theme provider,
 * so the choice survives reloads without any server round-trip.
 */
export default function ThemeToggle() {
  const { mode, setMode } = useColorScheme()
  return (
    <ToggleButtonGroup size="small" exclusive value={mode ?? 'system'} aria-label="Color theme"
      onChange={(_event, value: Mode | null) => { if (value) setMode(value) }}>
      {MODES.map(item => (
        <ToggleButton key={item.value} value={item.value} aria-label={`${item.label} theme`}>
          {item.label}
        </ToggleButton>
      ))}
    </ToggleButtonGroup>
  )
}
