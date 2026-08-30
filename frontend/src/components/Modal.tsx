/**
 * A dialog that behaves like one: Escape closes it, a click on the backdrop
 * closes it, focus moves into it on open and returns to whatever opened it on
 * close, and the page behind it does not scroll.
 */

import { useEffect, useRef, type ReactNode } from 'react'
import { Button } from './ui'
import styles from './Modal.module.css'

export function Modal({
  title,
  onClose,
  children,
  footer,
}: {
  title: string
  onClose: () => void
  children: ReactNode
  footer: ReactNode
}) {
  const panel = useRef<HTMLDivElement>(null)
  const opener = useRef<Element | null>(null)

  useEffect(() => {
    opener.current = document.activeElement
    const { overflow } = document.body.style
    document.body.style.overflow = 'hidden'

    panel.current?.querySelector<HTMLElement>('input, select, textarea, button')?.focus()

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)

    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = overflow
      if (opener.current instanceof HTMLElement) opener.current.focus()
    }
  }, [onClose])

  return (
    <div
      className={styles.scrim}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div ref={panel} className={styles.modal} role="dialog" aria-modal="true" aria-label={title}>
        <div className={styles.header}>
          <h2>{title}</h2>
          <Button variant="ghost" small onClick={onClose} aria-label="Close">
            ✕
          </Button>
        </div>
        {children}
        <div className={styles.footer}>{footer}</div>
      </div>
    </div>
  )
}

export function ModalBody({ children }: { children: ReactNode }) {
  return <div className={styles.body}>{children}</div>
}

export function Field({
  label,
  required = false,
  hint,
  children,
}: {
  label: string
  required?: boolean
  hint?: string
  children: ReactNode
}) {
  return (
    <div className={styles.field}>
      <label>
        {label}
        {required ? <span className={styles.required}> *</span> : null}
      </label>
      {children}
      {hint ? <span className={styles.hint}>{hint}</span> : null}
    </div>
  )
}

export function FieldPair({ children }: { children: ReactNode }) {
  return <div className={styles.pair}>{children}</div>
}

export const modalStyles = styles
