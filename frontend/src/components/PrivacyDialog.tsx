import { Box, Button, Dialog, DialogActions, DialogContent, DialogTitle, Typography } from '@mui/material'

type Props = { open: boolean; onClose: () => void }

// Every statement below matches docs/ARCHITECTURE.md and the current settings defaults.
// Do not add guarantees (encryption, backups, legal retention) that are not implemented.
const FACTS = [
  'Questions and answers are stored as private conversation data, not as log lines.',
  'Anonymous threads expire 24 hours after creation; signed-in threads are retained for 30 days by default. Access stops at expiry and the scheduled cleanup pass removes the stored rows.',
  'Threads with no research runs are removed after a short grace period (default 1 hour).',
  'Deleting a thread removes it from your history immediately; the stored rows are purged by the retention cleanup.',
  'Request logs record request IDs and error classes, never queries, session cookies or provider keys.',
  'Tracing is opt-in because it can export conversation content.',
]

export default function PrivacyDialog({ open, onClose }: Props) {
  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth aria-labelledby="privacy-title">
      <DialogTitle id="privacy-title">Privacy and data handling</DialogTitle>
      <DialogContent>
        <Box component="ul" sx={{ pl: 2, m: 0 }}>
          {FACTS.map(fact => <Typography component="li" variant="body2" key={fact} sx={{ mb: 1 }}>
            {fact}
          </Typography>)}
        </Box>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
          Not implemented yet: encryption at rest, a formal retention policy, verified identity and
          deployment backup/deletion guarantees. These are launch work and are not promised by this build.
        </Typography>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  )
}
