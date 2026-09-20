/**
 * Handing somebody an account: the address they sign in with, and the password.
 *
 * The invitation beside it is the better door wherever it can be used, and
 * this dialog says so rather than pretending otherwise — a link somebody
 * redeems themselves means nobody but them ever knows their password. What it
 * cannot do is help somebody standing next to you with no mailbox you can
 * reach, or somebody who has lost the password they already set, and those are
 * the two cases this is for.
 *
 * So the warning is the point of the layout rather than a footnote: whoever
 * fills this in knows the password afterwards, and the person it belongs to
 * should change it. On somebody who already had one it is a reset, and the
 * dialog says what that costs them — every other session they left open.
 */

import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type Person } from '../api/client'
import { Field, Modal, ModalBody } from './Modal'
import { Button, ErrorBanner } from './ui'

/** Mirrors `MIN_PASSWORD_LENGTH` on the server, which is the one that refuses. */
const MIN_PASSWORD = 12

export function AccountDialog({
  person,
  onSaved,
  onDone,
  onClose,
}: {
  person: Person
  onSaved: (name: string) => void
  onDone: () => Promise<void>
  onClose: () => void
}) {
  const resetting = person.has_account
  const [email, setEmail] = useState(person.email ?? '')
  const [password, setPassword] = useState('')

  const save = useMutation({
    mutationFn: () => api.setAccount(person.id, email.trim(), password),
    onSuccess: async () => {
      await onDone()
      onSaved(person.name)
      onClose()
    },
  })

  const complete = email.trim().length > 0 && password.length >= MIN_PASSWORD

  return (
    <Modal
      title={resetting ? `Reset ${person.name}'s password` : `Give ${person.name} an account`}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="go" disabled={!complete || save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? 'Saving…' : resetting ? 'Reset password' : 'Create account'}
          </Button>
        </>
      }
    >
      <ModalBody>
        {save.error ? <ErrorBanner>{save.error.message}</ErrorBanner> : null}
        <p>
          {resetting
            ? 'This replaces the password they have and signs them out everywhere else. Yours is the one session that survives it.'
            : 'They can sign in with these as soon as you save. Prefer Invite where you can reach them: a link they open themselves is a password only they ever know.'}{' '}
          Tell them to change it under Account — until they do, you know it too.
        </p>
        <Field label="Email" required hint="What they sign in with.">
          <input
            type="email"
            value={email}
            autoComplete="off"
            onChange={(event) => setEmail(event.target.value)}
          />
        </Field>
        {/* Readable rather than dotted: whoever types this is about to read
            it out or paste it into a message, and a masked box only invites
            the typo they would then hand over. */}
        <Field
          label="Password"
          required
          hint={`At least ${MIN_PASSWORD} characters. Length is what buys you anything here, not punctuation.`}
        >
          <input
            type="text"
            value={password}
            autoComplete="off"
            onChange={(event) => setPassword(event.target.value)}
          />
        </Field>
      </ModalBody>
    </Modal>
  )
}
