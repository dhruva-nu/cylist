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
