import type { ReactNode } from 'react'
import { horizontalStep, walkWithArrows } from './arrowWalk'
import styles from './FormTabs.module.css'

export type TabId = 'basics' | 'details' | 'dates' | 'work'

export interface Tab {
  id: TabId
  label: string
  /** Whether a required field on this tab is still empty. */
  incomplete: boolean
}

/**
 * The form's tab strip.
 *
 * A tab carries a dot when something required on it is still blank, so a
 * disabled Save button always has somewhere visible to point at — otherwise
 * the one unfilled field is behind a tab nobody has a reason to open.
 *
 * Arrow keys walk the strip and focus follows the selection, as they do in any
 * tab list — see `walkWithArrows`.
 */
export function Tabs({
  tabs,
  current,
  onSelect,
}: {
  tabs: Tab[]
  current: TabId
  onSelect: (id: TabId) => void
}) {
  return (
    <div className={styles.tabs} role="tablist" aria-label="Task fields">
      {tabs.map((tab, index) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          id={`task-tab-${tab.id}`}
          aria-selected={tab.id === current}
          aria-controls={`task-panel-${tab.id}`}
          tabIndex={tab.id === current ? 0 : -1}
          className={tab.id === current ? styles.tabOn : ''}
          onClick={() => onSelect(tab.id)}
          onKeyDown={(event) =>
            walkWithArrows(event, {
              options: tabs,
              index,
              step: horizontalStep(event.key),
              role: 'tab',
              choose: (next) => onSelect(next.id),
            })
          }
        >
          {tab.label}
          {tab.incomplete ? (
            <span className={styles.needed} aria-label="something required is still empty">
              •
            </span>
          ) : null}
        </button>
      ))}
    </div>
  )
}

/**
 * One tab's worth of the form, hidden rather than unmounted while another shows.
 *
 * Every panel stays mounted, so switching tabs never throws away what has been
 * typed on the other one. `hidden` also takes the panel out of the modal's
 * focus trap, which reads the laid-out controls rather than a list it keeps in
 * step by hand.
 */
export function TabPanel({
  id,
  current,
  children,
}: {
  id: TabId
  current: TabId
  children: ReactNode
}) {
  return (
    <div
      className={styles.panel}
      role="tabpanel"
      id={`task-panel-${id}`}
      aria-labelledby={`task-tab-${id}`}
      hidden={current !== id}
    >
      {children}
    </div>
  )
}
