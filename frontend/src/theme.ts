import { createTheme } from '@mui/material'

/**
 * localStorage key for the light/dark/system choice. Namespaced so the theme choice
 * never collides with another app served from the same origin during development.
 */
export const MODE_STORAGE_KEY = 'research-agent-mode'

export const theme = createTheme({
  // CSS variables are required for setMode to switch schemes. The named data attribute writes
  // [data-mui-color-scheme="dark"] on <html>; the default 'media' selector ignores setMode, so
  // a manual light/dark toggle would have no visual effect.
  cssVariables: { colorSchemeSelector: 'data-mui-color-scheme' },
  colorSchemes: { light: true, dark: true },
  palette: { primary: { main: '#1A5F7A' }, secondary: { main: '#159895' } },
  shape: { borderRadius: 12 },
  typography: { fontFamily: 'Inter, system-ui, sans-serif', button: { textTransform: 'none' } },
})
