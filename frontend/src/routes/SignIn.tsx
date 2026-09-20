/**
 * Sign-in. The only screen reachable without a session.
 *
 * Two forms, and which one is drawn is not a preference: `GET /setup` says
 * whether this deployment has any accounts in it. Until it does, the only way
 * in is the bootstrap password from `CYLIST_PASSWORD_HASH`, which belongs to
 * nobody and exists to let the first account be opened. After that it is an
 * email and a password like anywhere else, and the bootstrap password stops
 * being accepted at all.
 *
 * Asking the server rather than guessing matters on exactly one day — the
 * first one — and getting it wrong that day means a box asking for an email
 * address that does not exist yet.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { ApiError, api } from '../api/client'
import { Button, ErrorBanner } from '../components/ui'
import styles from './SignIn.module.css'

export function SignIn() {
  const queryClient = useQueryClient()
  const setup = useQuery({ queryKey: ['setup'], queryFn: api.setup })
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // Assume accounts until told otherwise: the email form is right for every
  // deployment but a brand-new one, and a flash of the wrong form is better
  // spent on the rare case than on the usual one.
  const bootstrapping = setup.data ? !setup.data.has_accounts : false

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await (bootstrapping ? api.bootstrapSignIn(password) : api.signIn(email, password))
      await queryClient.invalidateQueries({ queryKey: ['me'] })
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Something went wrong.')
      setBusy(false)
    }
  }

  const ready = password.length > 0 && (bootstrapping || email.trim().length > 0)

  return (
    <div className={styles.centred}>
      <form className={styles.card} onSubmit={(event) => void submit(event)}>
        <h1 className={styles.wordmark}>
          <span className={styles.mark}>C</span> Cylist
        </h1>

        {bootstrapping ? (
          <p className={styles.muted}>
            Nobody here has an account yet. Sign in with the deployment password to open the first
            one.
          </p>
        ) : (
          <>
            <label className={styles.label} htmlFor="email">
              Email
            </label>
            <input
              id="email"
              className={styles.input}
              type="email"
              autoComplete="username"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
          </>
        )}

        <label className={styles.label} htmlFor="password">
          Password
        </label>
        <input
          id="password"
          className={styles.input}
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          required
        />
        {error ? <ErrorBanner>{error}</ErrorBanner> : null}
        <Button variant="go" type="submit" disabled={busy || !ready}>
          {busy ? 'Signing in…' : 'Sign in'}
        </Button>
      </form>
    </div>
  )
}
