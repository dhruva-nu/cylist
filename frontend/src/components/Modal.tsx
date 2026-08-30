/**
 * A dialog that behaves like one: Escape closes it, a click on the backdrop
 * closes it, focus moves into it on open, cycles inside it while it is there,
 * and returns to whatever opened it on close; the page behind it does not
 * scroll.
 */

import { useEffect, useRef, type ReactNode } from 'react'
import { Button } from './ui'
import styles from './Modal.module.css'

const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

/** The tabbable elements inside the panel, in the order Tab visits them. */
function tabbableIn(panel: HTMLElement): HTMLElement[] {
  return [...panel.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
    // Anything laid out; a hidden control is one Tab would skip anyway.
    (element) => element.getClientRects().length > 0,
  )
}

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

    const open = panel.current
    // The panel itself as a last resort: a dialog with nothing to focus would
    // otherwise leave focus on the page behind it, where Tab has nothing to
    // come back to.
    if (open) (tabbableIn(open)[0] ?? open).focus()

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        onClose()
        return
      }
      if (event.key !== 'Tab' || open === null) return

      // Tab is trapped rather than merely discouraged. `aria-modal` hides the
      // rest of the page from assistive technology but does nothing for the
      // keyboard, so without this Tab walks out into a page the user cannot
      // see and cannot get back from.
      const stops = tabbableIn(open)
      const first = stops.at(0)
      const last = stops.at(-1)
      if (first === undefined || last === undefined) {
        event.preventDefault()
        open.focus()
        return
      }

      const active = document.activeElement
      if (event.shiftKey && (active === first || !open.contains(active))) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && (active === last || !open.contains(active))) {
        event.preventDefault()
        first.focus()
      }
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
      <div
        ref={panel}
        className={styles.modal}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
      >
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
