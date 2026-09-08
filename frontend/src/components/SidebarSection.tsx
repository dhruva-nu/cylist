/**
 * One foldable section of the sidebar.
 *
 * Its own module rather than a local in `Sidebar.tsx` because a section that
 * fetches its own data has to draw its own heading — the count on a folded
 * "Today" is the whole reason to fold it and still trust it — and a panel
 * reaching back into the file that renders it would be a cycle between the
 * two.
 *
 * The heading is the button, so the whole strip is the target rather than a
 * chevron beside a label, and `aria-expanded` on it is what says the strip
 * shows and hides what follows.
 */

import type { ReactNode } from 'react'
import styles from './Sidebar.module.css'

export function SidebarSection({
  name,
  note,
  folded,
  onToggle,
  children,
}: {
  name: string
  /** What the section is holding, said on the heading so folding it away does
   * not hide the fact that there is something in there. */
  note?: ReactNode
  folded: boolean
  onToggle: () => void
  children: ReactNode
}) {
  return (
    <section className={styles.section}>
      <h2>
        <button
          type="button"
          className={styles.sectionHead}
          aria-expanded={!folded}
          onClick={onToggle}
        >
          <span className={styles.sectionName}>{name}</span>
          {note}
          <span className={styles.chevron} aria-hidden="true">
            {folded ? '▸' : '▾'}
          </span>
        </button>
      </h2>
      {folded ? null : <div className={styles.sectionBody}>{children}</div>}
    </section>
  )
}

/**
 * A section of the sidebar that is a control rather than something that folds.
 *
 * Today and the day report are both of these: a list you consult and a report
 * you ask for, each wanted at a dialog's width and then wanted gone. Neither
 * is a panel you keep open beside your work, which is what a fold is for.
 *
 * Drawn as the folding heads are, down to the strip, the ink and the hover —
 * the sidebar is a stack of rows and one row in a different livery would read
 * as something bolted on rather than as the third of three. What it does not
 * wear is their chevron, which is the whole of the difference: a chevron says
 * this opens *downwards, here*, and these open over the page. No
 * `aria-expanded` either, for the same reason — there is nothing beneath this
 * to be expanded.
 */
export function SidebarAction({
  name,
  note,
  disabled = false,
  title,
  onOpen,
  children,
}: {
  name: string
  /** What is waiting behind it, said on the strip — the count on Today. */
  note?: ReactNode
  /** Nothing to open: off a project, neither of these has anything to say. */
  disabled?: boolean
  /** Why it is disabled, since a strip with no chevron has nowhere to say so.
   * `| undefined` because `exactOptionalPropertyTypes` is on and callers pass
   * the reason or nothing, in one expression. */
  title?: string | undefined
  onOpen: () => void
  /** What it opens. Rendered here so the dialog lives with the control. */
  children?: ReactNode
}) {
  return (
    <section className={styles.section}>
      <button
        type="button"
        className={styles.sectionHead}
        disabled={disabled}
        title={title}
        onClick={onOpen}
      >
        <span className={styles.sectionName}>{name}</span>
        {note}
      </button>
      {children}
    </section>
  )
}
