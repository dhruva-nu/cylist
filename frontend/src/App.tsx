/**
 * Phase 0 shell.
 *
 * Signing in and reading back the identity is the smallest thing that proves
 * the whole chain works: browser -> Vite proxy -> FastAPI -> Postgres, with
 * the session cookie surviving the round trip. Phase 1 replaces this with the
 * project grid and the real routes.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { ApiError, api } from './api/client'
import styles from './App.module.css'

export function App() {
  const identity = useQuery({
    queryKey: ['me'],
    queryFn: api.me,
    retry: (failureCount, error) =>
      // Not being signed in is an answer, not a failure worth retrying.
      !(error instanceof ApiError && error.isUnauthenticated) && failureCount < 2,
  })

  if (identity.isPending) {
    return <Centered>Loading…</Centered>
  }

  if (identity.error instanceof ApiError && identity.error.isUnauthenticated) {
    return <SignIn />
  }

  if (identity.error) {
    return <Centered>{identity.error.message}</Centered>
  }

  return <SignedIn label={identity.data.label} scopes={identity.data.scopes} />
}

function Centered({ children }: { children: React.ReactNode }) {
  return <div className={styles.centered}>{children}</div>
}

function SignIn() {
  const queryClient = useQueryClient()
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.signIn(password)
      await queryClient.invalidateQueries({ queryKey: ['me'] })
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Something went wrong.')
      setBusy(false)
    }
  }

  return (
    <Centered>
      <form className={styles.card} onSubmit={(event) => void submit(event)}>
        <h1 className={styles.wordmark}>
          <span className={styles.mark}>C</span> Cylist
        </h1>
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
        {error ? <p className={styles.error}>{error}</p> : null}
        <button className={styles.go} type="submit" disabled={busy || password.length === 0}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </Centered>
  )
}

function SignedIn({ label, scopes }: { label: string; scopes: string[] }) {
  const queryClient = useQueryClient()

  async function signOut() {
    await api.signOut()
    await queryClient.invalidateQueries({ queryKey: ['me'] })
  }

  return (
    <Centered>
      <div className={styles.card}>
        <h1 className={styles.wordmark}>
          <span className={styles.mark}>C</span> Cylist
        </h1>
        <p className={styles.muted}>
          Signed in as <strong>{label}</strong>.
        </p>
        <p className={styles.muted}>
          Scopes: <code className={styles.code}>{scopes.join(' · ')}</code>
        </p>
        <p className={styles.muted}>
          The board, files, vault and people arrive in the next phases.
        </p>
        <button className={styles.plain} type="button" onClick={() => void signOut()}>
          Sign out
        </button>
      </div>
    </Centered>
  )
}
