/**
 * A dialog that behaves like one: Escape closes it, a click on the backdrop
 * closes it, focus moves into it on open, cycles inside it while it is there,
 * and returns to whatever opened it on close; the page behind it does not
 * scroll.
 *
 * Dialogs nest. A card opened from the day report is one over the other, and
 * only the innermost of them answers the keyboard — see `stack` below.
 */

import { useEffect, useRef, type ReactNode } from 'react'
import { Button } from './ui'
import styles from './Modal.module.css'

/**
 * Every dialog currently open, innermost last.
 *
 * Each one listens on the document rather than on its own panel, because a
 * focus trap has to see the Tab that would take focus out of it — and a
 * listener on the document is a listener every other open dialog also has.
 * Left alone, Escape closed the whole stack at once, and the outer trap kept
 * hauling Tab back out of the inner dialog it knows nothing about. So a
 * dialog acts on a key only while it is the last one on this list.
 *
 * Membership is kept by an effect with no dependencies, so a dialog holds its
 * place across its parent's re-renders rather than being popped and pushed
 * back on top of whatever it had opened.
 */
const stack: symbol[] = []

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
  headerActions,
}: {
  title: string
  onClose: () => void
  children: ReactNode
  footer: ReactNode
  /** Controls that belong beside the title rather than in the footer. */
  headerActions?: ReactNode
}) {
  const panel = useRef<HTMLDivElement>(null)
  const opener = useRef<Element | null>(null)
  const me = useRef(Symbol('dialog'))

  /**
   * Opening and closing: the stack, the page's scroll, and where focus goes.
   *
   * Deliberately not keyed on `onClose`. Callers pass an arrow, so that key
   * changes on every render of whatever holds the dialog — and this effect
   * running again would take focus back to the first field mid-edit and
   * shuffle the dialog to the top of a stack it is not at the top of. The
   * listener below is the part that needs the current `onClose`, and it is
   * its own effect for exactly that reason.
   */
  useEffect(() => {
    const token = me.current
    stack.push(token)

    opener.current = document.activeElement
    const { overflow } = document.body.style
    document.body.style.overflow = 'hidden'

    const open = panel.current
    // The panel itself as a last resort: a dialog with nothing to focus would
    // otherwise leave focus on the page behind it, where Tab has nothing to
    // come back to.
    if (open) (tabbableIn(open)[0] ?? open).focus()

    return () => {
      const at = stack.lastIndexOf(token)
      if (at !== -1) stack.splice(at, 1)
      // An outer dialog set this to `hidden` before this one did, so what is
      // put back here is `hidden` too, and the page stays still until the last
      // dialog goes.
      document.body.style.overflow = overflow
      if (opener.current instanceof HTMLElement) opener.current.focus()
    }
  }, [])

  useEffect(() => {
    const token = me.current
    const open = panel.current

    function onKeyDown(event: KeyboardEvent) {
      // Only the innermost dialog answers. Anything further out is behind this
      // one and behind its scrim, and a key it acted on would be a key the
      // reader aimed at something else.
      if (stack.at(-1) !== token) return

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
    return () => document.removeEventListener('keydown', onKeyDown)
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
          <div className={styles.headerActions}>
            {headerActions}
            <Button variant="ghost" small onClick={onClose} aria-label="Close">
              ✕
            </Button>
          </div>
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
