import type { Goal } from '../../api/client'
import { STATUSES } from '../../components/taskLabels'
import type { StatusChoice } from '../boardHiding'
import { FilterIcon, TickIcon } from './icons'
import type { GoalChoice } from './narrowBoard'
import { usePopover } from './usePopover'
import styles from './BoardToolbar.module.css'

/** The status list's choices, every card first. */
const STATUS_CHOICES: { value: StatusChoice; label: string }[] = [
  { value: 'all', label: 'All' },
  ...STATUSES,
]

/**
 * Goal, status and the cancelled hide, narrowing the board — in a popover
 * under one button rather than sitting on the toolbar all the time.
 *
 * All of it ANDed onto whatever the search box is already narrowing to — see
 * `narrowBoard`. Closing it does not clear it: a filter left on while the
 * popover is shut is still doing its job, which is what the number on the
 * button is for.
 *
 * The cancelled hide is a switch rather than a third list because it has one
 * question and two answers, and because it is the one of the three the board
 * arrives with already set: the status list asks "show me only these", the
 * hide asks "stop showing me those", and it is on from the first visit — which
 * is why it says how many it is holding back, and why it says so even while it
 * is standing down. The cards on hold are not here at all; they are held back
 * and let out at the foot of each column, because which of them matters is a
 * question about one column rather than about the board.
 */
export function QuickFilters({
  goals,
  goal,
  onGoal,
  status,
  onStatus,
  showCancelled,
  onShowCancelled,
  cancelledCount,
  hiding,
}: {
  goals: Goal[]
  goal: GoalChoice
  onGoal: (goal: GoalChoice) => void
  status: StatusChoice
  onStatus: (status: StatusChoice) => void
  /** Whether the reader has asked for the cancelled cards back. Off by
   * default, which is the hide being on. */
  showCancelled: boolean
  onShowCancelled: (show: boolean) => void
  /** How many cancelled cards the board is holding, before the hide is
   * applied. */
  cancelledCount: number
  /** Whether the hide is actually in force — see `hidesCancelled`. */
  hiding: boolean
}) {
  const { open, setOpen, root, button } = usePopover()
  // The cancelled hide is on by default, so it is not what makes the filter
  // worth looking in — the number counts what somebody changed, and the
  // ordinary board would otherwise wear one permanently.
  const active = [goal !== 'all', status !== 'all', showCancelled].filter(Boolean).length
  const goalOptions: { value: GoalChoice; name: string; colour: string | null }[] = [
    { value: 'all', name: 'All goals', colour: null },
    { value: 'none', name: 'No goal', colour: null },
    ...goals.map((candidate) => ({
      value: candidate.id,
      name: candidate.name,
      colour: candidate.colour,
    })),
  ]

  function clearFilters() {
    onGoal('all')
    onStatus('all')
    onShowCancelled(false)
  }

  return (
    <div className={styles.popRoot} ref={root}>
      <button
        ref={button}
        type="button"
        className={`${styles.toolButton} ${open ? styles.toolButtonOpen : ''}`}
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen(!open)}
        title={open ? 'Hide the filters' : 'Filter the board by goal or status'}
      >
        <FilterIcon />
        Filter
        {active ? (
          <span className={styles.toolBadge}>
            {active}
            <span className="visually-hidden"> on</span>
          </span>
        ) : null}
      </button>
      {open ? (
        <div
          className={`${styles.popover} ${styles.filterPopover}`}
          role="dialog"
          aria-label="Filter"
        >
          <div className={styles.popHead}>
            <span className={styles.popTitle}>Filter</span>
            {active ? (
              <button type="button" className={styles.popLink} onClick={clearFilters}>
                Clear
              </button>
            ) : null}
          </div>

          <div className={styles.popLabel} id="filter-goal">
            Goal
          </div>
          <div className={styles.optionList} role="group" aria-labelledby="filter-goal">
            {goalOptions.map((option) => (
              <button
                key={option.value}
                type="button"
                className={styles.option}
                aria-pressed={goal === option.value}
                onClick={() => onGoal(option.value)}
              >
                <span
                  className={styles.optionSwatch}
                  style={option.colour ? { background: option.colour } : undefined}
                  aria-hidden="true"
                />
                <span className={styles.optionName}>{option.name}</span>
                {goal === option.value ? <TickIcon /> : null}
              </button>
            ))}
          </div>

          <div className={styles.popLabel} id="filter-status">
            Status
          </div>
          <div className={styles.statusPicks} role="group" aria-labelledby="filter-status">
            {STATUS_CHOICES.map((choice) => (
              <button
                key={choice.value}
                type="button"
                className={styles.statusPick}
                aria-pressed={status === choice.value}
                onClick={() => onStatus(choice.value)}
              >
                {choice.label}
              </button>
            ))}
          </div>

          <div className={styles.popRule} />

          {/* Pressed, but not necessarily doing anything: `hiding` is what the
              board actually did, and the two part company when the status
              list is asking for the very cards this puts away. The switch
              stays on and says so in its own words rather than silently
              turning itself off — the setting is still on, it is just being
              overruled by the more specific question next to it. */}
          <button
            type="button"
            className={styles.switchRow}
            role="switch"
            aria-checked={!showCancelled}
            onClick={() => onShowCancelled(!showCancelled)}
          >
            <span className={styles.switchText}>
              Hide cancelled
              <span className={styles.switchHint}>
                {cancelledHint(hiding, showCancelled, cancelledCount)}
              </span>
            </span>
            <span className={styles.switch} aria-hidden="true" />
          </button>
        </div>
      ) : null}
    </div>
  )
}

/**
 * What the cancelled hide says beside its name: how many cards it is holding
 * back, that it has been overruled, or — once it is off — how many it has let
 * through, which is the number the reader turned it off to see.
 */
function cancelledHint(hiding: boolean, showCancelled: boolean, cancelledCount: number) {
  if (hiding) return `${cancelledCount} hidden`
  if (!showCancelled) return 'overruled by the status'
  return `${cancelledCount} shown`
}
