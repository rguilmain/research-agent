import { useEffect, useState } from 'react'
import { Alert, Box, Button, Dialog, DialogActions, DialogContent, DialogTitle, Drawer,
  List, ListItem, ListItemButton, ListItemText, Stack, TextField, Typography } from '@mui/material'
import { ApiError, deleteThread, listThreads, renameThread } from '../api/threads'
import type { Thread } from '../api/threads'

type Props = { activeId?: string; busy: boolean; version: number;
  onOpen: (thread: Thread) => void; onDeleted: (id: string) => void }

// The server pages history 25 rows at a time; one "delete all" click stays bounded.
const BATCH = 25
const MAX_PAGES = 20

export default function ThreadHistory({ activeId, busy, version, onOpen, onDeleted }: Props) {
  const [items, setItems] = useState<Thread[]>([])
  const [available, setAvailable] = useState(false)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  // Action errors stay separate from load errors so a background refresh cannot wipe
  // the outcome of a rename, delete or bulk delete.
  const [error, setError] = useState('')
  const [page, setPage] = useState(0)
  const [refresh, setRefresh] = useState(0)
  const [mobileOpen, setMobileOpen] = useState(false)
  const [editing, setEditing] = useState<{ item: Thread; action: 'rename' | 'delete' } | null>(null)
  const [title, setTitle] = useState('')
  const [saving, setSaving] = useState(false)
  const [clearing, setClearing] = useState(false)
  const [confirmingAll, setConfirmingAll] = useState(false)

  useEffect(() => {
    const abort = new AbortController()
    listThreads(page * 25, abort.signal).then(value => {
      setItems(value)
      setAvailable(true)
      setLoadError('')
    }).catch(cause => {
      if (abort.signal.aborted) return
      if (cause instanceof ApiError && cause.status === 503) setAvailable(false)
      else { setAvailable(true); setLoadError('Cannot load thread history.') }
    }).finally(() => { if (!abort.signal.aborted) setLoading(false) })
    return () => abort.abort()
  }, [page, refresh, version])

  async function save() {
    if (!editing || busy) return
    setSaving(true)
    try {
      if (editing.action === 'rename') await renameThread(editing.item.id, title.trim())
      else { await deleteThread(editing.item.id); onDeleted(editing.item.id) }
      setEditing(null)
      setRefresh(v => v + 1)
    } catch { setError('Thread could not be changed. It may be busy or no longer available.') }
    finally { setSaving(false) }
  }

  /** Deletes saved threads page by page through the owner-checked delete endpoint. */
  async function deleteAll() {
    if (busy || clearing) return
    setClearing(true)
    setError('')
    let deleted = 0
    let failed = 0
    try {
      let offset = 0
      for (let page = 0; page < MAX_PAGES; page++) {
        const batch = await listThreads(offset)
        if (!batch.length) break
        let pageFailed = 0
        for (const item of batch) {
          try { await deleteThread(item.id); deleted += 1; onDeleted(item.id) }
          catch { pageFailed += 1 }
        }
        failed += pageFailed
        // Successful deletions shift the remaining rows down, so only failed rows
        // still occupy a slot in the next page.
        offset += pageFailed
        if (batch.length < BATCH) break
      }
      if (failed) {
        setError(`Deleted ${deleted} threads; ${failed} could not be deleted. They may be busy or already gone.`)
      }
    } catch {
      setError('History could not be listed. Nothing else was deleted.')
    } finally {
      setClearing(false)
      setConfirmingAll(false)
      setPage(0)
      setRefresh(v => v + 1)
    }
  }

  if (!available) return null
  const content = <Box component="nav" aria-label="Thread history" sx={{ width: 270, p: 2 }}>
    <Typography variant="h6">Thread history</Typography>
    <Typography variant="caption">Threads belong to this browser session; sign in to keep them.</Typography>
    {loadError && <Alert severity="error">{loadError}</Alert>}
    {error && <Alert severity="error" onClose={() => setError('')}>{error}</Alert>}
    {loading && <Typography role="status">Loading history…</Typography>}
    {!loading && !items.length && <Typography>No saved threads.</Typography>}
    <List>{items.map(item => <ListItem key={item.id} disablePadding sx={{ display: 'block' }}>
      <ListItemButton selected={item.id === activeId} disabled={busy || loading}
        onClick={() => { onOpen(item); setMobileOpen(false) }}>
        <ListItemText primary={item.title} secondary={`${item.demo ? 'Demo · ' : ''}${item.model_id}`}
          slotProps={{ primary: { sx: { overflowWrap: 'anywhere' } } }} />
      </ListItemButton>
      <Stack direction="row">
        <Button size="small" disabled={busy || saving || clearing} aria-label={`Rename ${item.title}`}
          onClick={() => { setEditing({ item, action: 'rename' }); setTitle(item.title) }}>Rename</Button>
        <Button size="small" disabled={busy || saving || clearing} aria-label={`Delete ${item.title}`}
          onClick={() => setEditing({ item, action: 'delete' })}>Delete</Button>
      </Stack>
    </ListItem>)}</List>
    <Stack direction="row">
      <Button disabled={busy || loading || page === 0} onClick={() => setPage(v => v - 1)}>Previous</Button>
      <Button disabled={busy || loading || items.length < 25} onClick={() => setPage(v => v + 1)}>Next</Button>
      <Button disabled={loading} onClick={() => setRefresh(v => v + 1)}>Refresh</Button>
    </Stack>
    {!!items.length && <Button size="small" color="error" disabled={busy || loading || clearing}
      onClick={() => setConfirmingAll(true)}>Delete all history</Button>}
  </Box>
  return <>
    <Button sx={{ display: { md: 'none' }, m: 2 }} onClick={() => setMobileOpen(true)}>History</Button>
    <Drawer variant="permanent" sx={{ display: { xs: 'none', md: 'block' }, width: 270,
      flexShrink: 0, '& .MuiDrawer-paper': { width: 270 } }}>{content}</Drawer>
    <Drawer open={mobileOpen} onClose={() => setMobileOpen(false)}
      sx={{ display: { md: 'none' } }}>{content}</Drawer>
    <Dialog open={!!editing} onClose={() => { if (!saving) setEditing(null) }}>
      <DialogTitle>{editing?.action === 'rename' ? 'Rename thread' : 'Delete thread?'}</DialogTitle>
      <DialogContent>{editing?.action === 'rename' ?
        <TextField autoFocus label="Thread title" value={title} onChange={e => setTitle(e.target.value)}
          slotProps={{ htmlInput: { maxLength: 200 } }} sx={{ mt: 1 }} /> :
        <Typography>This removes the thread from history. Physical deletion follows the retention policy.</Typography>}
      </DialogContent>
      <DialogActions>
        <Button disabled={saving} onClick={() => setEditing(null)}>Cancel</Button>
        <Button disabled={busy || saving || (editing?.action === 'rename' && !title.trim())}
          onClick={() => void save()}>{editing?.action === 'rename' ? 'Save' : 'Confirm delete'}</Button>
      </DialogActions>
    </Dialog>
    <Dialog open={confirmingAll} onClose={() => { if (!clearing) setConfirmingAll(false) }}>
      <DialogTitle>Delete all saved threads?</DialogTitle>
      <DialogContent>
        <Typography>Every saved thread in this session or account is removed from history.
          Physical deletion follows the retention policy.</Typography>
        {clearing && <Typography role="status" sx={{ mt: 2 }}>Deleting saved threads…</Typography>}
      </DialogContent>
      <DialogActions>
        <Button disabled={clearing} onClick={() => setConfirmingAll(false)}>Cancel</Button>
        <Button color="error" disabled={busy || clearing} onClick={() => void deleteAll()}>Delete all</Button>
      </DialogActions>
    </Dialog>
  </>
}