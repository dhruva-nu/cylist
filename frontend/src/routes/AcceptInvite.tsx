/**
 * Setting a password from an invitation link.
 *
 * Reachable without a session, because the whole point is that whoever holds
 * the link has no credential yet — the link is the credential. It is
 * single-use and expires, so the two things that can go wrong here are "this
 * link has already been used" and "this link is too old", and the server
 * answers both with the same sentence on purpose.
 *
 * Accepting signs you in, rather than returning you to a sign-in screen to
 * type the password you have just this second chosen.
 */

import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { ApiError, MIN_PASSWORD_LENGTH, api } from '../api/client'
import { Button, ErrorBanner } from '../components/ui'
import styles from './SignIn.module.css'

export function AcceptInvite({ token }: { token: string }) {
  const queryClient = useQueryClient()
  const [password, setPassword] = useState('')
  const [repeated, setRepeated] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const tooShort = password.length > 0 && password.length < MIN_PASSWORD_LENGTH
  const mismatched = repeated.length > 0 && repeated !== password
  const ready = password.length >= MIN_PASSWORD_LENGTH && repeated === password

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.acceptInvite(token, password)
      await queryClient.invalidateQueries({ queryKey: ['me'] })
      // Drop the token out of the address bar on the way in. It is spent now,
      // but a used credential sitting in browser history and in whatever reads
      // it is not something to leave behind on purpose.
      window.history.replaceState(null, '', '/')
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Something went wrong.')
      setBusy(false)
    }
  }

  return (
    <div className={styles.centred}>
      <form className={styles.card} onSubmit={(event) => void submit(event)}>
        <h1 className={styles.wordmark}>
          <span className={styles.mark}>C</span> Cylist
        </h1>
        <p className={styles.muted}>Choose a password. You will sign in with it from now on.</p>

        <label className={styles.label} htmlFor="password">
          Password
        </label>
        <input
          id="password"
          className={styles.input}
          type="password"
          autoComplete="new-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          required
        />

        <label className={styles.label} htmlFor="repeat">
          Repeat it
        </label>
        <input
          id="repeat"
          className={styles.input}
          type="password"
          autoComplete="new-password"
          value={repeated}
          onChange={(event) => setRepeated(event.target.value)}
          required
        />

        {tooShort ? (
          <p className={styles.muted}>
            At least {MIN_PASSWORD_LENGTH} characters. Length is what makes a password hard to
            guess; nothing here asks for a symbol.
          </p>
        ) : null}
        {mismatched ? <ErrorBanner>Those two do not match.</ErrorBanner> : null}
        {error ? <ErrorBanner>{error}</ErrorBanner> : null}

        <Button variant="go" type="submit" disabled={busy || !ready}>
          {busy ? 'Setting it…' : 'Set password and sign in'}
        </Button>
      </form>
    </div>
  )
}
