/** Opening the first account on a deployment, and changing your own password. */

import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { MIN_PASSWORD_LENGTH, api } from '../../api/client'
import { DialogActions, Field, Modal, ModalBody } from '../../components/Modal'
import { ErrorBanner, ErrorNotice } from '../../components/ui'
import styles from './Home.module.css'

/**
 * Opening the first account on a deployment that has none.
 *
 * Three server calls behind one form, and they are three rather than one on
 * purpose: this is the ordinary invite flow — add a person, invite them,
 * accept the invitation — run end to end by the one person who is both the
 * sender and the recipient. Giving the bootstrap session an endpoint of its
 * own that skipped it would be a second way to mint an account, and the
 * second way is the one that does not get audited.
 *
 * The invitation token never leaves this component; it is made and spent in
 * the same submit.
 */
export function FirstAccountDialog({
  onDone,
  onClose,
}: {
  onDone: () => Promise<void>
  onClose: () => void
}) {
  const [form, setForm] = useState({ name: '', title: '', email: '', password: '' })

  const openAccount = useMutation({
    mutationFn: async () => {
      const person = await api.createPerson({
        name: form.name.trim(),
        kind: 'team',
        title: form.title.trim(),
        responsibilities: 'Runs this Cylist.',
        email: form.email.trim(),
      })
      const invitation = await api.invitePerson(person.id)
      // Accepting replaces the bootstrap cookie with this person's session,
      // which is why nothing after this point needs the old one.
      await api.acceptInvite(invitation.token, form.password)
    },
    onSuccess: async () => {
      await onDone()
      onClose()
    },
  })

  const ready =
    form.name.trim() !== '' &&
    form.title.trim() !== '' &&
    form.email.trim() !== '' &&
    form.password.length >= MIN_PASSWORD_LENGTH

  return (
    <Modal
      title="Open your account"
      onClose={onClose}
      footer={
        <DialogActions
          submitLabel="Open account"
          pendingLabel="Opening…"
          pending={openAccount.isPending}
          disabled={!ready}
          onSubmit={() => openAccount.mutate()}
          onCancel={onClose}
        />
      }
    >
      <ModalBody>
        <ErrorNotice error={openAccount.error} />
        <Field label="Name" required>
          <input
            value={form.name}
            onChange={(event) => setForm({ ...form, name: event.target.value })}
          />
        </Field>
        <Field label="Who are you?" required>
          <input
            value={form.title}
            onChange={(event) => setForm({ ...form, title: event.target.value })}
            placeholder="Tech lead"
          />
        </Field>
        <Field label="Email" required hint="What you will sign in with from now on.">
          <input
            type="email"
            autoComplete="username"
            value={form.email}
            onChange={(event) => setForm({ ...form, email: event.target.value })}
          />
        </Field>
        <Field label="Password" required hint={`At least ${MIN_PASSWORD_LENGTH} characters.`}>
          <input
            type="password"
            autoComplete="new-password"
            value={form.password}
            onChange={(event) => setForm({ ...form, password: event.target.value })}
          />
        </Field>
      </ModalBody>
    </Modal>
  )
}

/**
 * Changing your own password.
 *
 * Proving the current one is what makes this a password change rather than a
 * password reset, and a reset is not something a session should be able to do
 * — an unattended browser would be one.
 *
 * Every other credential you hold goes with it: other browsers, and the tokens
 * your agents are using. That is the point of changing a password under
 * suspicion, so it is said out loud rather than discovered when a laptop
 * elsewhere stops working. The session doing it is re-issued, so this browser
 * stays signed in.
 */
export function ChangePasswordDialog({
  onSaved,
  onClose,
}: {
  onSaved: () => void
  onClose: () => void
}) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [repeated, setRepeated] = useState('')

  const change = useMutation({
    mutationFn: () => api.changePassword(current, next),
    onSuccess: () => {
      onSaved()
      onClose()
    },
  })

  const mismatched = repeated.length > 0 && repeated !== next
  const ready = current.length > 0 && next.length >= MIN_PASSWORD_LENGTH && repeated === next

  return (
    <Modal
      title="Change your password"
      onClose={onClose}
      footer={
        <DialogActions
          submitLabel="Change password"
          pendingLabel="Changing…"
          pending={change.isPending}
          disabled={!ready}
          onSubmit={() => change.mutate()}
          onCancel={onClose}
        />
      }
    >
      <ModalBody>
        <ErrorNotice error={change.error} />
        <Field label="Current password" required>
          <input
            type="password"
            autoComplete="current-password"
            value={current}
            onChange={(event) => setCurrent(event.target.value)}
          />
        </Field>
        <Field
          label="New password"
          required
          hint={`At least ${MIN_PASSWORD_LENGTH} characters. Length, not symbols.`}
        >
          <input
            type="password"
            autoComplete="new-password"
            value={next}
            onChange={(event) => setNext(event.target.value)}
          />
        </Field>
        <Field label="Repeat it" required>
          <input
            type="password"
            autoComplete="new-password"
            value={repeated}
            onChange={(event) => setRepeated(event.target.value)}
          />
        </Field>
        {mismatched ? <ErrorBanner>Those two do not match.</ErrorBanner> : null}
        <p className={styles.meNote}>
          This browser stays signed in. Every other session you have, and every agent token you
          minted, stops working.
        </p>
      </ModalBody>
    </Modal>
  )
}
