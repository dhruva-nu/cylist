/**
 * Handing somebody an invitation link.
 *
 * Cylist sends no email, so the link comes back to whoever asked for it to
 * pass on however they already talk to this person. That is a deliberate
 * stopping point rather than a missing feature — a secret that travels by a
 * channel the sender picked is a secret somebody watched leave — but it does
 * mean this dialog is the only moment the token exists anywhere but the
 * recipient's hands. Hence the shape: mint on open, show once, say plainly
 * that it will not be shown again.
 *
 * Re-inviting somebody replaces the link they were sent. The dialog says so,
 * because the failure it prevents is silent: two links out, one of them dead,
 * and nobody knowing which.
 */

import { useMutation } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api, type InviteIssued, type Person } from '../api/client'
import { Modal, ModalBody } from './Modal'
import { Button, ErrorBanner } from './ui'
import styles from './InviteDialog.module.css'

export function InviteDialog({
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
  const [copied, setCopied] = useState(false)

  const invite = useMutation({
    mutationFn: (): Promise<InviteIssued> => api.invitePerson(person.id),
    onSuccess: async () => {
      await onDone()
      onSaved(person.name)
    },
  })

  const withdraw = useMutation({
    mutationFn: () => api.withdrawInvite(person.id),
    onSuccess: async () => {
      await onDone()
      onClose()
    },
  })

  const issued = invite.data
  const link = issued?.url ?? ''

  async function copy() {
    try {
      await navigator.clipboard.writeText(link)
      setCopied(true)
    } catch {
      // Clipboard access is refused often enough — an insecure origin, a
      // browser setting — that failing loudly would be wrong. The link is on
      // screen and selectable either way, which is the fallback.
      setCopied(false)
    }
  }

  return (
    <Modal
      title={issued ? 'Send them this link' : `Invite ${person.name}`}
      onClose={onClose}
      footer={
        issued ? (
          <Button variant="go" onClick={onClose}>
            Done
          </Button>
        ) : (
          <>
            <Button onClick={onClose}>Cancel</Button>
            {person.invite_is_pending ? (
              <Button danger disabled={withdraw.isPending} onClick={() => withdraw.mutate()}>
                {withdraw.isPending ? 'Withdrawing…' : 'Withdraw invitation'}
              </Button>
            ) : null}
            <Button variant="go" disabled={invite.isPending} onClick={() => invite.mutate()}>
              {invite.isPending ? 'Making a link…' : 'Create invitation'}
            </Button>
          </>
        )
      }
    >
      <ModalBody>
        {invite.error ? <ErrorBanner>{invite.error.message}</ErrorBanner> : null}
        {withdraw.error ? <ErrorBanner>{withdraw.error.message}</ErrorBanner> : null}

        {issued ? (
          <>
            <p className={styles.lead}>
              {person.name} sets their own password from this link and is signed in straight away.
              It works once, and stops working <ExpiresIn at={issued.expires_at} />.
            </p>
            <div className={styles.linkRow}>
              <code className={styles.link}>{link}</code>
              <Button small onClick={() => void copy()}>
                {copied ? 'Copied' : 'Copy'}
              </Button>
            </div>
            <p className={styles.warning}>
              This is the only time it is shown. Close this and it is gone — make another if you
              lose it, which will replace this one.
            </p>
          </>
        ) : (
          <>
            <p className={styles.lead}>
              {person.email ? (
                <>
                  They will sign in as <b>{person.email}</b> with a password only they ever see.
                </>
              ) : (
                <>
                  {person.name} has no email address yet, and that is what they would sign in with.
                  Add one under Edit first.
                </>
              )}
            </p>
            {person.invite_is_pending ? (
              <p className={styles.warning}>
                They already have an invitation outstanding. Making another one replaces it, and the
                link they were sent stops working.
              </p>
            ) : null}
          </>
        )}
      </ModalBody>
    </Modal>
  )
}

/** "in 7 days", from the moment the server said the link dies. */
function ExpiresIn({ at }: { at: string }) {
  const [now, setNow] = useState(() => Date.now())

  // A dialog can sit open while somebody goes to find the right chat window.
  // Once a minute is plenty to keep "in 7 days" from becoming a lie.
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60_000)
    return () => window.clearInterval(timer)
  }, [])

  const days = Math.max(0, Math.round((new Date(at).getTime() - now) / 86_400_000))
  return <>{days <= 1 ? 'within a day' : `in ${days} days`}</>
}
