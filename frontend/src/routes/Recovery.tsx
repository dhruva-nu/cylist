/**
 * Getting back in without a password or a passkey (CYLIST-204).
 *
 * Cylist sends no email, so there is no reset link. There are two ways back:
 *
 * - **From a device that is still signed in.** This device asks and is shown a
 *   short code; somebody holding a signed-in session (the web app, the
 *   Android app, or a CLI token) approves it under *Passkeys → Approve a new
 *   device*. This screen polls until that happens and is then signed in.
 * - **From the server's operator**, who runs a command on the deployment and
 *   hands over a one-time code.
 *
 * Either way every other session and API token of the person ends, and the
 * next thing to do is add a passkey to this device.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api } from '../api/client'
import { handlesOwnErrors } from '../api/mutationErrors'
import { queryKeys } from '../api/queries'
import { Button, ErrorNotice } from '../components/ui'
import styles from './SignIn.module.css'

const POLL_MS = 3000

type Mode = 'choose' | 'device' | 'operator'

export function Recovery({ onBack }: { onBack: () => void }) {
  const [mode, setMode] = useState<Mode>('choose')

  return (
    <div className={styles.centred}>
      <div className={styles.card}>
        <h1 className={styles.wordmark}>
          <span className={styles.mark}>C</span> Get back in
        </h1>
        {mode === 'choose' ? (
          <>
            <p className={styles.muted}>
              There is no password reset email. Pick whichever you can do.
            </p>
            <Button variant="go" onClick={() => setMode('device')}>
              Approve from a device that is still signed in
            </Button>
            <Button onClick={() => setMode('operator')}>I have a code from my operator</Button>
          </>
        ) : null}
        {mode === 'device' ? <FromDevice /> : null}
        {mode === 'operator' ? <FromOperator /> : null}
        <button
          type="button"
          className={styles.link}
          onClick={mode === 'choose' ? onBack : () => setMode('choose')}
        >
          Back
        </button>
      </div>
    </div>
  )
}

function FromDevice() {
  const queryClient = useQueryClient()
  const [email, setEmail] = useState('')

  const start = useMutation({
    meta: handlesOwnErrors,
    mutationFn: () => api.startRecovery(email.trim()),
  })
  const started = start.data

  // Asking again is how a pending request is polled: it answers `pending`
  // until a signed-in device approves, then signs this one in exactly once.
  const claim = useQuery({
    queryKey: queryKeys.recoveryClaim(started?.request_id),
    enabled: started !== undefined,
    retry: false,
    // One claim signs in and spends the request; asking again would be refused.
    refetchOnWindowFocus: false,
    staleTime: Infinity,
    refetchInterval: (query) => (query.state.data?.state === 'signed_in' ? false : POLL_MS),
    queryFn: async () => {
      const result = await api.claimRecovery(started!.request_id, started!.secret)
      if (result.state === 'signed_in') {
        await queryClient.invalidateQueries({ queryKey: queryKeys.identity })
      }
      return result
    },
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    start.mutate()
  }

  if (started) {
    return (
      <>
        <p className={styles.muted}>
          On a device where you are still signed in, open <b>Passkeys</b> on the home screen, choose{' '}
          <b>Approve a new device</b> and type this code. It works once, for ten minutes.
        </p>
        <p className={styles.code} aria-label="Recovery code">
          {started.code}
        </p>
        <ErrorNotice error={claim.error} />
        <p className={styles.muted}>Waiting for approval…</p>
      </>
    )
  }

  return (
    <form onSubmit={submit} style={{ display: 'contents' }}>
      <label className={styles.label} htmlFor="recovery-email">
        Your email
      </label>
      <input
        id="recovery-email"
        className={styles.input}
        type="email"
        autoComplete="username"
        value={email}
        onChange={(event) => setEmail(event.target.value)}
        required
      />
      <ErrorNotice error={start.error} />
      <Button variant="go" type="submit" disabled={start.isPending || email.trim() === ''}>
        {start.isPending ? 'Asking…' : 'Show me a code'}
      </Button>
    </form>
  )
}

function FromOperator() {
  const queryClient = useQueryClient()
  const [code, setCode] = useState('')

  const redeem = useMutation({
    meta: handlesOwnErrors,
    mutationFn: async () => {
      await api.redeemOperatorCode(code)
      await queryClient.invalidateQueries({ queryKey: queryKeys.identity })
    },
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    redeem.mutate()
  }

  return (
    <form onSubmit={submit} style={{ display: 'contents' }}>
      <p className={styles.muted}>
        Whoever runs this server can issue a single-use code after checking it is you.
      </p>
      <label className={styles.label} htmlFor="operator-code">
        Code
      </label>
      <input
        id="operator-code"
        className={styles.input}
        autoComplete="off"
        value={code}
        onChange={(event) => setCode(event.target.value)}
        required
      />
      <ErrorNotice error={redeem.error} />
      <Button variant="go" type="submit" disabled={redeem.isPending || code.trim() === ''}>
        {redeem.isPending ? 'Checking…' : 'Sign in'}
      </Button>
    </form>
  )
}
