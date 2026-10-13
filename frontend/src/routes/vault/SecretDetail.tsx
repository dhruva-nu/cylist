/**
 * One credential.
 *
 * The rule the whole vault is built around holds here: a plaintext secret is
 * never in the page until somebody asks for it. The tree carries metadata
 * only; `Reveal` calls `/vault/nodes/{id}/reveal`, which the server logs, and
 * the value it returns lives in that one request's result and nowhere else.
 * Hiding it again resets the request, and the screen keys this component by
 * node, so moving the selection throws it away too.
 */

import { useMutation } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api, type VaultNode } from '../../api/client'
import { Button, ErrorNotice } from '../../components/ui'
import styles from './ProjectVault.module.css'

const MASK = '••••••••••••'

/** How long a Copy button says "Copied" before it goes back to being a button. */
const COPIED_FOR_MS = 2000

type Copyable = 'username' | 'secret'

export function SecretDetail({
  node,
  announce,
  mayReveal,
}: {
  node: VaultNode
  announce: (message: string) => void
  /** Whether the reader's role allows reading the stored value. */
  mayReveal: boolean
}) {
  const [copied, setCopied] = useState<Copyable | null>(null)

  useEffect(() => {
    if (!copied) return
    const timer = setTimeout(() => setCopied(null), COPIED_FOR_MS)
    return () => clearTimeout(timer)
  }, [copied])

  const reveal = useMutation({
    mutationFn: () => api.revealSecret(node.id),
    // Dropped from react-query's cache the moment nothing is showing it,
    // rather than kept for the five minutes a finished request otherwise
    // lingers — a credential has no business outliving the screen it was on.
    gcTime: 0,
    onSuccess: () => {
      // What is announced is that it is on screen, never the value itself: a
      // live region is read aloud, and a credential read aloud is a credential
      // told to the room.
      announce(`The secret for ${node.name} is now showing on screen.`)
    },
  })
  const revealed = reveal.data?.value ?? null

  /** Copying the secret reveals it, so it goes through the same logged
   * endpoint — taking a credential to the clipboard is reading it. */
  async function textToCopy(what: Copyable): Promise<string> {
    if (what === 'username') return node.secret?.username ?? ''
    if (revealed !== null) return revealed
    return (await api.revealSecret(node.id)).value
  }

  const copy = useMutation({
    mutationFn: async (what: Copyable) => {
      await navigator.clipboard.writeText(await textToCopy(what))
      return what
    },
    onSuccess: (what) => {
      setCopied(what)
      announce(`${what === 'username' ? 'Username' : 'Secret'} copied to the clipboard.`)
    },
  })

  /** Reveal, or put it away again. Hiding is a state change nobody watching
   * the screen can miss and nobody listening would otherwise hear. */
  function toggleReveal() {
    if (revealed === null) {
      reveal.mutate()
      return
    }
    reveal.reset()
    announce(`The secret for ${node.name} is hidden again.`)
  }

  const secret = node.secret
  if (!secret) return <p className={styles.note}>This entry has no stored value.</p>

  return (
    <>
      <ErrorNotice error={reveal.error} />
      <ErrorNotice error={copy.error} />

      <div className={styles.kv}>
        {secret.username ? (
          <>
            <span className={styles.key}>Username</span>
            <span className={styles.value}>
              <span className={styles.mono}>{secret.username}</span>
              <CopyButton what="username" copied={copied} onCopy={() => copy.mutate('username')} />
            </span>
          </>
        ) : null}

        <span className={styles.key}>Secret</span>
        <span className={styles.value}>
          {/* Masked, the dots are decoration standing in for something absent,
              so they are hidden and the state is spelled out instead — twelve
              bullet characters read one at a time tell nobody anything. */}
          <span className={styles.masked}>
            {revealed === null ? (
              <>
                <span className="visually-hidden">Secret, hidden</span>
                <span aria-hidden="true">{MASK}</span>
              </>
            ) : (
              <>
                <span className="visually-hidden">Secret, showing: </span>
                {revealed}
              </>
            )}
          </span>
          {/* Copying a credential is reading it — the copy goes through the
              same logged endpoint — so both controls answer to the same
              permission, and a role without it gets neither. */}
          {mayReveal ? (
            <>
              <Button variant="ghost" small disabled={reveal.isPending} onClick={toggleReveal}>
                {revealButtonLabel(revealed, reveal.isPending)}
              </Button>
              <CopyButton
                what="secret"
                copied={copied}
                disabled={copy.isPending}
                onCopy={() => copy.mutate('secret')}
              />
            </>
          ) : null}
        </span>

        {secret.url ? (
          <>
            <span className={styles.key}>URL</span>
            <span className={styles.value}>
              <a className={styles.mono} href={secret.url} target="_blank" rel="noreferrer">
                {secret.url}
              </a>
            </span>
          </>
        ) : null}

        {secret.notes ? (
          <>
            <span className={styles.key}>Notes</span>
            <span className={styles.value}>
              <p className={styles.note}>{secret.notes}</p>
            </span>
          </>
        ) : null}

        <span className={styles.key}>Updated</span>
        <span className={`${styles.value} ${styles.updated}`}>
          {new Date(secret.updated_at).toLocaleString()}
        </span>
      </div>
    </>
  )
}

/** A Copy button that says, for a moment, that it has. */
function CopyButton({
  what,
  copied,
  disabled = false,
  onCopy,
}: {
  what: Copyable
  copied: Copyable | null
  disabled?: boolean
  onCopy: () => void
}) {
  const noun = what === 'username' ? 'Username' : 'Secret'
  const done = copied === what

  return (
    <Button
      variant="ghost"
      small
      disabled={disabled}
      aria-label={done ? `${noun} copied` : `Copy ${noun.toLowerCase()}`}
      onClick={onCopy}
    >
      {done ? 'Copied' : 'Copy'}
    </Button>
  )
}

/** What the reveal button says: put it away, wait for it, or ask for it. */
function revealButtonLabel(revealed: string | null, revealing: boolean): string {
  if (revealed) return 'Hide'
  if (revealing) return 'Revealing…'
  return 'Reveal'
}
