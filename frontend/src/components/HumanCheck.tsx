import { useEffect, useRef, useState } from 'react'
import { Box, Paper, Typography } from '@mui/material'
import { loadTurnstile, verifyHuman } from '../api/turnstile'

type Props = { siteKey: string; onVerified: () => void }
type Status = 'loading' | 'ready' | 'verifying' | 'error'

const FAILED = 'Human check failed. Please try again or sign in.'

/** Anonymous callers complete this once per session before they can ask questions. */
export default function HumanCheck({ siteKey, onVerified }: Props) {
  const container = useRef<HTMLDivElement | null>(null)
  const [status, setStatus] = useState<Status>('loading')
  // The widget must survive parent re-renders: a fresh callback prop would otherwise
  // tear the widget down and discard a challenge the visitor was still solving.
  const onVerifiedRef = useRef(onVerified)

  useEffect(() => {
    onVerifiedRef.current = onVerified
  })

  useEffect(() => {
    let active = true
    let widgetId: string | null = null
    loadTurnstile()
      .then(api => {
        if (!active || !container.current) return
        widgetId = api.render(container.current, {
          sitekey: siteKey,
          action: 'research',
          theme: 'auto',
          callback: (token: string) => {
            setStatus('verifying')
            void verifyHuman(token)
              .then(() => { if (active) { setStatus('ready'); onVerifiedRef.current() } })
              .catch(() => { if (!active) return; setStatus('error'); if (widgetId) api.reset(widgetId) })
          },
          'error-callback': () => { if (active) setStatus('error') },
          'expired-callback': () => { if (widgetId) api.reset(widgetId) },
        })
        if (active) setStatus('ready')
      })
      .catch(() => { if (active) setStatus('error') })
    return () => {
      active = false
      if (widgetId && window.turnstile) window.turnstile.remove(widgetId)
    }
  }, [siteKey])

  return (
    <Paper variant="outlined" sx={{ p: 2, mt: 3 }}>
      <Typography variant="body2">
        Confirm you are human to ask questions without signing in.
      </Typography>
      <Box ref={container} sx={{ mt: 1 }} />
      <Typography role="status" variant="body2" color="text.secondary">
        {status === 'loading' && 'Loading the human check…'}
        {status === 'verifying' && 'Checking your response…'}
        {status === 'error' && FAILED}
      </Typography>
    </Paper>
  )
}
