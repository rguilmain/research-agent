import { lazy, Suspense, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Alert, Box, Button, Chip, Container, CssBaseline, FormControl, InputLabel, MenuItem,
  Paper, Select, Stack, TextField, ThemeProvider, Typography, createTheme } from '@mui/material'
import ThreadHistory from './components/ThreadHistory'
import { useResearch } from './hooks/useResearch'

const MarkdownView = lazy(() => import('./components/MarkdownView'))

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
  const { turns, error, busy, steps, research, stop, reset, openThread, activeThread,
    canContinue, notice, historyVersion } = useResearch()
  const selectedModel = activeThread?.model ?? model

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
    if (query.trim() && selectedModel) { void research(query.trim(), selectedModel); setQuery('') }
  }

  return <ThemeProvider theme={theme} defaultMode="system"><CssBaseline />
    <Box sx={{ display: { md: 'flex' } }}>
    <ThreadHistory activeId={activeThread?.id} busy={busy} version={historyVersion}
      onOpen={item => { setQuery(''); void openThread(item) }}
      onDeleted={id => { if (activeThread?.id === id) { reset(); setQuery('') } }} />
    <Container maxWidth="md" sx={{ py: { xs: 3, md: 7 } }}>
      <Stack component="header" direction="row" spacing={2} sx={{ mb: 4, alignItems: 'center' }}>
        <img src="/favicon.svg" alt="" width="48" height="48" />
        <Box sx={{ flex: 1 }}><Typography variant="h4" component="h1" sx={{ fontWeight: 750 }}>Research Agent</Typography>
          <Typography color="text.secondary">Explore the web. Follow the evidence.</Typography></Box>
        <Chip label="Development" variant="outlined" />
      </Stack>
      <Alert severity="warning" sx={{ mb: 3 }}>Not ready for public deployment. All local requests share one identity — not authenticated. Login and quotas are not implemented.</Alert>
      {demo === true && <Alert severity="info" sx={{ mb: 3 }}>Demo mode — no paid APIs or real web searches.</Alert>}
      {connectionError && <Alert severity="error">{connectionError}</Alert>}
      {notice && <Alert severity="info" sx={{ mb: 2 }}>{notice}</Alert>}
      {!canContinue && <Button disabled={busy} onClick={() => { reset(); setQuery('') }}>Start a new thread</Button>}
      {canContinue && <Paper component="form" onSubmit={submit} variant="outlined" sx={{ p: { xs: 2, md: 3 } }}>
        <TextField fullWidth multiline minRows={3} maxRows={8} label="What would you like to research?"
          value={query} onChange={event => setQuery(event.target.value)}
          slotProps={{ htmlInput: { maxLength: 2000 } }}
          helperText={`${query.length}/2000 characters`} />
        <Stack direction="row" spacing={2} sx={{ mt: 2 }}>
          <FormControl size="small" sx={{ minWidth: 180, flex: 1 }}>
            <InputLabel id="model-label">Model</InputLabel>
            <Select labelId="model-label" label="Model" value={selectedModel} disabled={busy || !catalog || !!activeThread}
              onChange={event => setModel(event.target.value)}>
              {activeThread && !catalog?.items.some(item => item.id === selectedModel) &&
                <MenuItem value={selectedModel}>{selectedModel}</MenuItem>}
              {catalog?.items.map(item => <MenuItem key={item.id} value={item.id} disabled={!item.available}>
                {item.label}{!item.available ? ' (not enabled)' : ''}</MenuItem>)}
            </Select>
          </FormControl>
          {busy ? <Button variant="outlined" onClick={stop}>Stop</Button> :
            <Button type="submit" variant="contained" disabled={!query.trim() || !selectedModel}>Research</Button>}
          <Button disabled={busy} onClick={() => { reset(); setQuery('') }}>New thread</Button>
        </Stack>
      </Paper>}
      {busy && <Typography role="status" sx={{ mt: 3 }}>Researching…</Typography>}
      {error && <Alert severity="error" sx={{ mt: 3 }}>{error}</Alert>}
      {!!steps.length && <Box component="details" sx={{ mt: 3 }}><summary>Research activity</summary>
        {steps.map((step, index) => <Typography key={index} variant="body2">{step}</Typography>)}
      </Box>}
      {turns.map((turn, index) => <Paper component="article" key={index} variant="outlined" sx={{ p: 3, mt: 3 }}>
        <Typography component="h2" variant="h6">{turn.role === 'user' ? 'Your question' : 'Research result'}</Typography>
        {turn.role === 'user' ? <Typography sx={{ whiteSpace: 'pre-wrap' }}>{turn.text}</Typography> :
          <Suspense fallback={<Typography>Loading answer…</Typography>}><MarkdownView text={turn.text} /></Suspense>}
        {!!turn.sources.length && <Box component="section">
          <Typography component="h3" variant="subtitle1">Sources</Typography>
          <ul>{turn.sources.map(source => <li key={source.url}><a href={source.url}
            target="_blank" rel="noopener noreferrer nofollow">{source.title}</a></li>)}</ul>
        </Box>}
      </Paper>)}
      <Typography component="footer" variant="body2" color="text.secondary" sx={{ mt: 5 }}>
        AI can make mistakes. Check the sources before relying on an answer. No document uploads or private-document retrieval.
      </Typography>
    </Container>
    </Box>
  </ThemeProvider>
}