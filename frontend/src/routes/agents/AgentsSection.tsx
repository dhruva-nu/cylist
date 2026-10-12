import type { ReactNode } from 'react'
import styles from './Agents.module.css'

/** A heading over one half of the screen, with its count and its action. */
export function Section({
  title,
  blurb,
  count,
  actions,
  children,
}: {
  title: string
  blurb: ReactNode
  count?: ReactNode
  actions?: ReactNode
  children: ReactNode
}) {
  return (
    <section className={styles.section}>
      <div className={styles.sectionHead}>
        <div className={styles.sectionText}>
          <h2>
            {title}
            {count !== undefined ? <span className={styles.count}>{count}</span> : null}
          </h2>
          <p>{blurb}</p>
        </div>
        {actions ? <div className={styles.sectionActions}>{actions}</div> : null}
      </div>
      {children}
    </section>
  )
}
