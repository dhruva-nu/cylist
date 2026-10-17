/**
 * The board's own small icons: the toolbar's, and the marks a card's lists
 * draw. Kept together because the tick is drawn in both places — the goal the
 * filter is on, and a finished part of a card.
 */

import styles from './icons.module.css'

/** A tick, for a finished part or a chosen option. */
export function TickIcon() {
  return (
    <svg
      className={styles.tick}
      viewBox="0 0 16 16"
      width="12"
      height="12"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3.5 8.5 6.5 11.5 12.5 4.5" />
    </svg>
  )
}

/** A row that opens something: shown on hover, so a list at rest stays quiet. */
export function ChevronIcon() {
  return (
    <svg className={styles.go} viewBox="0 0 16 16" aria-hidden="true">
      <path d="m6 3.5 4.5 4.5L6 12.5" />
    </svg>
  )
}

/** A row that opens elsewhere, in a new tab. */
export function OutIcon() {
  return (
    <svg className={styles.go} viewBox="0 0 16 16" aria-hidden="true">
      <path d="M9.5 2.5h4v4M13.5 2.5 7.5 8.5M12 9.5V13a.5.5 0 0 1-.5.5h-8A.5.5 0 0 1 3 13V5a.5.5 0 0 1 .5-.5H7" />
    </svg>
  )
}

/** Three columns, for the board read by column. */
export function ColumnsIcon() {
  return (
    <svg className={styles.toolIcon} viewBox="0 0 16 16" aria-hidden="true">
      <rect x="2" y="2.5" width="3.2" height="11" rx="1" />
      <rect x="6.4" y="2.5" width="3.2" height="7.5" rx="1" />
      <rect x="10.8" y="2.5" width="3.2" height="9.5" rx="1" />
    </svg>
  )
}

/** Stacked bands, for the board split into a lane per goal. */
export function LanesIcon() {
  return (
    <svg className={styles.toolIcon} viewBox="0 0 16 16" aria-hidden="true">
      <rect x="2" y="2.5" width="12" height="3" rx="1" />
      <rect x="2" y="6.5" width="12" height="3" rx="1" />
      <rect x="2" y="10.5" width="12" height="3" rx="1" />
    </svg>
  )
}

/** A funnel, for the filter. */
export function FilterIcon() {
  return (
    <svg className={styles.toolIcon} viewBox="0 0 16 16" aria-hidden="true">
      <path d="M2.5 3.5h11l-4.2 5v3.8l-2.6 1.2v-5z" />
    </svg>
  )
}

/** A storage box, for cold storage: where the last column's cards are put away. */
export function StoreIcon() {
  return (
    <svg className={styles.toolIcon} viewBox="0 0 16 16" aria-hidden="true">
      <rect x="2" y="3" width="12" height="3.2" rx="0.8" />
      <path d="M3.2 6.2v6.3a1 1 0 0 0 1 1h7.6a1 1 0 0 0 1-1V6.2M6.5 8.8h3" />
    </svg>
  )
}
