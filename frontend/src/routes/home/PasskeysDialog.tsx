/**
 * Passkeys: signing in with a fingerprint, face or PIN (CYLIST-204).
 *
 * The device keeps a key behind its own unlock and Cylist stores only the
 * public half, so nothing here is a secret worth stealing from the server and
 * no biometric ever leaves the phone. Passwords still work beside them.
 *
 * Two passkeys are encouraged, not required: after the first is added the
 * dialog says so, because one lost phone with one passkey is the situation
 * that sends somebody to the operator. The same dialog is where a signed-in
 * device approves a new one that is getting back in.
 */

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, passkeysSupported, wasCancelled, type Passkey } from '../../api/client'
import { handlesOwnErrors } from '../../api/mutationErrors'
import { queryKeys, usePasskeys } from '../../api/queries'
import { Modal, ModalBody } from '../../components/Modal'
import { Button, EmptyState, ErrorBanner, ErrorNotice } from '../../components/ui'
import { formatStamp } from '../../components/format'
import styles from './PasskeysDialog.module.css'

export function PasskeysDialog({
  onSaved,
  onClose,
}: {
  onSaved: (message: string) => void
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const list = usePasskeys()
  const [name, setName] = useState('')
  const [another, setAnother] = useState(false)
  const supported = passkeysSupported()

  const refresh = () => queryClient.invalidateQueries({ queryKey: queryKeys.passkeys })

  const add = useMutation({
    meta: handlesOwnErrors,
    mutationFn: async () => {
      try {
        return await api.addPasskey(name.trim())
      } catch (error) {
        if (wasCancelled(error)) return null
        throw error
      }
    },
    onSuccess: async (registered) => {
      if (registered === null) return
      setName('')
      setAnother(registered.recommend_another)
      await refresh()
      onSaved(`Added the passkey ${registered.passkey.name}.`)
    },
  })

  const passkeys = list.data ?? []

  return (
    <Modal title="Passkeys" onClose={onClose} footer={<Button onClick={onClose}>Done</Button>}>
      <ModalBody>
        <p>
          Sign in with your fingerprint, face or screen lock. Cylist keeps only a public key, never
          your fingerprint. Your password still works.
        </p>

        {list.error ? <ErrorNotice error={list.error} /> : null}
        {passkeys.length === 0 && !list.isPending ? (
          <EmptyState>No passkeys yet.</EmptyState>
        ) : null}
        <ul className={styles.list}>
          {passkeys.map((passkey) => (
            <PasskeyRow key={passkey.id} passkey={passkey} onChanged={refresh} onSaved={onSaved} />
          ))}
        </ul>

        {another ? (
          <p className={styles.nudge} role="status">
            Add a second one now, from another phone or laptop. If you lose your only passkey you
            will have to ask whoever runs this server to let you back in.
          </p>
        ) : null}

        {supported ? (
          <div className={styles.add}>
            <input
              aria-label="Name for this passkey"
              placeholder="Name it, e.g. OnePlus phone"
              value={name}
              maxLength={80}
              onChange={(event) => setName(event.target.value)}
            />
            <Button variant="go" onClick={() => add.mutate()} disabled={add.isPending}>
              {add.isPending ? 'Waiting for your device…' : 'Add a passkey'}
            </Button>
          </div>
        ) : (
          <ErrorBanner>This browser cannot make passkeys.</ErrorBanner>
        )}
        <ErrorNotice error={add.error} />

        <ApproveDevice onSaved={onSaved} />
      </ModalBody>
    </Modal>
  )
}

function PasskeyRow({
  passkey,
  onChanged,
  onSaved,
}: {
  passkey: Passkey
  onChanged: () => Promise<void>
  onSaved: (message: string) => void
}) {
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(passkey.name)

  const rename = useMutation({
    meta: handlesOwnErrors,
    mutationFn: () => api.renamePasskey(passkey.id, name.trim()),
    onSuccess: async () => {
      setEditing(false)
      await onChanged()
    },
  })
  const remove = useMutation({
    meta: handlesOwnErrors,
    mutationFn: () => api.removePasskey(passkey.id),
    onSuccess: async () => {
      await onChanged()
      onSaved(`Removed the passkey ${passkey.name}.`)
    },
  })

  return (
    <li className={styles.row}>
      {editing ? (
        <input
          aria-label="Passkey name"
          value={name}
          maxLength={80}
          autoFocus
          onChange={(event) => setName(event.target.value)}
        />
      ) : (
        <span className={styles.name}>
          <b>{passkey.name}</b>
          <span className={styles.meta}>
            Added {formatStamp(passkey.created_at)} ·{' '}
            {passkey.last_used_at ? `last used ${formatStamp(passkey.last_used_at)}` : 'never used'}
          </span>
        </span>
      )}
      {editing ? (
        <Button
          small
          variant="go"
          disabled={rename.isPending || name.trim() === ''}
          onClick={() => rename.mutate()}
        >
          Save
        </Button>
      ) : (
        <Button small onClick={() => setEditing(true)}>
          Rename
        </Button>
      )}
      <Button
        small
        danger
        disabled={remove.isPending}
        onClick={() => {
          if (
            window.confirm(
              `Remove the passkey ${passkey.name}? You will not be able to sign in with it.`,
            )
          ) {
            remove.mutate()
          }
        }}
      >
        Remove
      </Button>
      <ErrorNotice error={rename.error ?? remove.error} />
    </li>
  )
}

/**
 * The other half of getting back in: a new device shows a code, and this
 * signed-in one lets it in. Only for a code on a screen in your own hand —
 * whoever you approve becomes you, and every other session of yours ends.
 */
function ApproveDevice({ onSaved }: { onSaved: (message: string) => void }) {
  const [code, setCode] = useState('')
  const approve = useMutation({
    meta: handlesOwnErrors,
    mutationFn: () => api.approveRecovery(code),
    onSuccess: () => {
      setCode('')
      onSaved('Approved. The new device is signed in, and you have been signed out here.')
    },
  })

  return (
    <section className={styles.approve}>
      <h3>Approve a new device</h3>
      <p className={styles.meta}>
        Type the code the new device is showing. Only do this for a device you are holding: it signs
        in as you and ends every other session and API token you have, this one included.
      </p>
      <div className={styles.add}>
        <input
          aria-label="Code from the new device"
          placeholder="ABCD-EFGH"
          autoComplete="off"
          value={code}
          onChange={(event) => setCode(event.target.value)}
        />
        <Button onClick={() => approve.mutate()} disabled={approve.isPending || code.trim() === ''}>
          Approve
        </Button>
      </div>
      <ErrorNotice error={approve.error} />
    </section>
  )
}
