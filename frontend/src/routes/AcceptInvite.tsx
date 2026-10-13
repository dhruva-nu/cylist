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

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { withBase } from '../api/basePath'
import { MIN_PASSWORD_LENGTH, api } from '../api/client'
import { queryKeys } from '../api/queries'
import { Button, ErrorBanner, ErrorNotice } from '../components/ui'
import styles from './SignIn.module.css'

export function AcceptInvite({ token }: { token: string }) {
  const queryClient = useQueryClient()
  const [password, setPassword] = useState('')
  const [repeated, setRepeated] = useState('')

  const tooShort = password.length > 0 && password.length < MIN_PASSWORD_LENGTH
  const mismatched = repeated.length > 0 && repeated !== password
  const ready = password.length >= MIN_PASSWORD_LENGTH && repeated === password

  const accept = useMutation({
    mutationFn: async () => {
      await api.acceptInvite(token, password)
      await queryClient.invalidateQueries({ queryKey: queryKeys.identity })
      // Drop the token out of the address bar on the way in. It is spent now,
      // but a used credential sitting in browser history and in whatever reads
      // it is not something to leave behind on purpose. The deployment's root
      // rather than the server's, so a dev slot lands back inside its slot.
      window.history.replaceState(null, '', withBase('/'))
    },
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    accept.mutate()
  }

  return (
    <div className={styles.centred}>
      <form className={styles.card} onSubmit={submit}>
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
        <ErrorNotice error={accept.error} />

        <Button variant="go" type="submit" disabled={accept.isPending || !ready}>
          {accept.isPending ? 'Setting it…' : 'Set password and sign in'}
        </Button>
      </form>
    </div>
  )
}
