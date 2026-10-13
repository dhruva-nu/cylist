import { useCallback, useState, type ReactNode } from 'react'
import { errorMessage } from '../../api/client'
import styles from './ui.module.css'

export function Eyebrow({ children }: { children: ReactNode }) {
  return <span className={styles.eyebrow}>{children}</span>
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <div className={styles.empty}>{children}</div>
}

export function ErrorBanner({ children }: { children: ReactNode }) {
  return (
    <p className={styles.banner} role="alert">
      {children}
    </p>
  )
}

/**
 * The banner for a request that failed, or nothing while none has.
 *
 * Every query and mutation keeps its last failure as `error`, null until there
 * is one, so this is the whole of how a screen reports one: hand it the error
 * and it says the server's sentence. Spelled once here rather than as a
 * conditional at every call site, so a failure reads the same on every screen.
 */
export function ErrorNotice({ error }: { error: unknown }) {
  if (error === null || error === undefined) return null
  return <ErrorBanner>{errorMessage(error)}</ErrorBanner>
}

/**
 * Say something to a screen reader that the screen only shows.
 *
 * A card moving column, a secret revealed, an upload finishing: each is
 * obvious to anyone watching and silent to anyone not. Pair the returned
 * `message` with a :func:`LiveRegion`.
 */
export function useAnnouncer() {
  const [message, setMessage] = useState('')

  const announce = useCallback((text: string) => {
    // A live region whose text has not changed is not read again, so doing the
    // same thing twice would announce once. The trailing space makes the
    // string new without changing a word of what is spoken.
    setMessage((current) => (current === text ? `${text}\u00a0` : text))
  }, [])

  return { message, announce }
}

/** Where :func:`useAnnouncer`'s messages are spoken. Renders nothing visible. */
export function LiveRegion({ message }: { message: string }) {
  return (
    <p className="visually-hidden" role="status" aria-live="polite">
      {message}
    </p>
  )
}
