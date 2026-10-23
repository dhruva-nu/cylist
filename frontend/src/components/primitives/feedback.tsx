import type { ReactNode } from 'react'
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
