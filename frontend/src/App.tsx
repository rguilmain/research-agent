import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Alert, Box, Button, Chip, Container, CssBaseline, FormControl, InputLabel, MenuItem,
  Paper, Select, Stack, TextField, ThemeProvider, Typography, createTheme } from '@mui/material'
import MarkdownView from './components/MarkdownView'
import { useResearch } from './hooks/useResearch'

const theme = createTheme({
  colorSchemes: { light: true, dark: true },
  palette: { primary: { main: '#1A5F7A' }, secondary: { main: '#159895' } },
  shape: { borderRadius: 12 },
  typography: { fontFamily: 'Inter, system-ui, sans-serif', button: { textTransform: 'none' } },
})

type Catalog = { default: string; items: { id: string; label: string; available: boolean }[] }

export default function App() {
  const [query, setQuery] = useState('')
  const [model, setModel] = useState('')
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [connectionError, setConnectionError] = useState('')
  const [demo, setDemo] = useState<boolean | null>(null)
  const { answer, error, busy, sources, steps, research, stop } = useResearch()

  useEffect(() => {
    const abort = new AbortController()
    async function load() {
      try {
        const responses = await Promise.all([
          fetch('/api/v1/models', { signal: abort.signal }),
          fetch('/api/v1/config', { signal: abort.signal }),
        ])
        if (responses.some(response => !response.ok)) throw new Error('API unavailable')
        const [models, config] = await Promise.all(responses.map(response => response.json()))
        setCatalog(models)
        setModel(models.default)
        setDemo(config.demo)
      } catch {
        if (!abort.signal.aborted) setConnectionError('Cannot reach the backend. Start the API on port 8000.')
      }
    }
    void load()
    return () => abort.abort()
  }, [])

  function submit(event: FormEvent) {
    event.preventDefault()
    if (query.trim() && model) void research(query.trim(), model)
  }

  return <ThemeProvider theme={theme} defaultMode="system"><CssBaseline />
    <Container maxWidth="md" sx={{ py: { xs: 3, md: 7 } }}>
      <Stack component="header" direction="row" spacing={2} sx={{ mb: 4, alignItems: 'center' }}>
        <img src="/favicon.svg" alt="" width="48" height="48" />
        <Box sx={{ flex: 1 }}><Typography variant="h4" component="h1" sx={{ fontWeight: 750 }}>Research Agent</Typography>
          <Typography color="text.secondary">Explore the web. Follow the evidence.</Typography></Box>
        <Chip label="Development" variant="outlined" />
      </Stack>
      <Alert severity="warning" sx={{ mb: 3 }}>Not ready for public deployment. Login, quotas, and saved history are in progress.</Alert>
      {demo === true && <Alert severity="info" sx={{ mb: 3 }}>Demo mode — no paid APIs or real web searches.</Alert>}
      {connectionError && <Alert severity="error">{connectionError}</Alert>}
      <Paper component="form" onSubmit={submit} variant="outlined" sx={{ p: { xs: 2, md: 3 } }}>
        <TextField fullWidth multiline minRows={3} maxRows={8} label="What would you like to research?"
          value={query} onChange={event => setQuery(event.target.value)}
          slotProps={{ htmlInput: { maxLength: 2000 } }}
          helperText={`${query.length}/2000 characters`} />
        <Stack direction="row" spacing={2} sx={{ mt: 2 }}>
          <FormControl size="small" sx={{ minWidth: 180, flex: 1 }}>
            <InputLabel id="model-label">Model</InputLabel>
            <Select labelId="model-label" label="Model" value={model} disabled={busy || !catalog}
              onChange={event => setModel(event.target.value)}>
              {catalog?.items.map(item => <MenuItem key={item.id} value={item.id} disabled={!item.available}>
                {item.label}{!item.available ? ' (not enabled)' : ''}</MenuItem>)}
            </Select>
          </FormControl>
          {busy ? <Button variant="outlined" onClick={stop}>Stop</Button> :
            <Button type="submit" variant="contained" disabled={!query.trim() || !model}>Research</Button>}
        </Stack>
      </Paper>
      {busy && <Typography role="status" sx={{ mt: 3 }}>Researching…</Typography>}
      {error && <Alert severity="error" sx={{ mt: 3 }}>{error}</Alert>}
      {!!steps.length && <Box component="details" sx={{ mt: 3 }}><summary>Research activity</summary>
        {steps.map((step, index) => <Typography key={index} variant="body2">{step}</Typography>)}
      </Box>}
      {answer && <Paper component="article" variant="outlined" sx={{ p: 3, mt: 3 }}>
        <Typography component="h2" variant="h6">Research result</Typography>
        <MarkdownView text={answer} />
      </Paper>}
      {!!sources.length && <Box component="section" sx={{ mt: 3 }}>
        <Typography component="h2" variant="h6">Sources</Typography>
        <ul>{sources.map(source => <li key={source.url}><a href={source.url}
          target="_blank" rel="noopener noreferrer nofollow">{source.title}</a></li>)}</ul>
      </Box>}
      <Typography component="footer" variant="body2" color="text.secondary" sx={{ mt: 5 }}>
        AI can make mistakes. Check the sources before relying on an answer. No document uploads or private-document retrieval.
      </Typography>
    </Container>
  </ThemeProvider>
}