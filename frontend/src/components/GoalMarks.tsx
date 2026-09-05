/**
 * The small drawn pieces of a goal: its colour, its progress, its date.
 *
 * They live together because they appear apart — on the goals page, on a
 * goal's own page, in the card dialog's picker and in the board's lane
 * headings — and a goal that reads as four different things in four places is
 * four things to learn instead of one.
 */

import type { Goal, GoalStatus } from '../api/client'
import { dueBucket, formatDue, formatDueLong } from './dates'
import { AlertIcon, CalendarIcon, ClockIcon } from './ui'
import styles from './GoalMarks.module.css'

export const GOAL_STATUS_LABELS: Record<GoalStatus, string> = {
  open: 'Open',
  achieved: 'Achieved',
  dropped: 'Dropped',
}

/**
 * How much of a goal is done, drawn as one bar.
 *
 * Three lengths on one track: what is finished in the goal's own colour, what
 * was cancelled in grey beside it, and what is left as the empty remainder.
 * Cancelled work is drawn rather than dropped from the total, because a goal
 * that shrank every time a card was abandoned would look like progress.
 *
 * A goal with no cards at all gets an empty track rather than a full one:
 * nothing is done, which is what nothing looks like.
 */
export function GoalProgressBar({ goal, large = false }: { goal: Goal; large?: boolean }) {
  const { total, done, cancelled } = goal.progress
  const share = (count: number) => (total === 0 ? 0 : (count / total) * 100)
  const said =
    total === 0
      ? 'No cards on this goal yet'
      : `${done} of ${total} cards done` + (cancelled ? `, ${cancelled} cancelled` : '')

  return (
    <div
      className={`${styles.track} ${large ? styles.trackLarge : ''}`}
      role="img"
      aria-label={said}
      title={said}
    >
      <span className={styles.done} style={{ width: `${share(done)}%`, background: goal.colour }} />
      <span className={styles.cancelled} style={{ width: `${share(cancelled)}%` }} />
    </div>
  )
}

/**
 * When a goal is wanted by, drawn as near or as far as it is.
 *
 * The same five steps a card's due date uses — see `dates.ts` — because they
 * are the same question. A goal that has settled shows nothing: the day a
 * finished goal was once aimed at is history, and history drawn in red reads
 * as a problem.
 */
export function GoalTargetMark({ goal }: { goal: Goal }) {
  const bucket = dueBucket(goal.status === 'open' ? goal.target_date : null)
  if (goal.target_date === null || bucket.kind === 'none') return null

  const on = formatDueLong(goal.target_date)
  const { className, icon, text, said } = {
    late: {
      className: styles.late,
      icon: <AlertIcon size={13} />,
      text: `${'days' in bucket ? bucket.days : 0}d over`,
      said: `${'days' in bucket ? bucket.days : 0} days past its target of ${on}`,
    },
    today: {
      className: styles.today,
      icon: <ClockIcon size={13} />,
      text: 'Today',
      said: `Targeted for today, ${on}`,
    },
    tomorrow: {
      className: styles.today,
      icon: <CalendarIcon size={13} />,
      text: 'Tomorrow',
      said: `Targeted for tomorrow, ${on}`,
    },
    soon: {
      className: styles.soon,
      icon: <CalendarIcon size={13} />,
      text: formatDue(goal.target_date),
      said: `Targeted for ${on}`,
    },
    later: {
      className: styles.later,
      icon: <CalendarIcon size={13} />,
      text: formatDue(goal.target_date),
      said: `Targeted for ${on}`,
    },
  }[bucket.kind]

  return (
    <span className={`${styles.target} ${className}`} title={said}>
      {icon}
      <span aria-hidden="true">{text}</span>
      <span className="visually-hidden">{said}</span>
    </span>
  )
}

/**
 * A goal named in a line of other things — a dot in its colour and its name.
 *
 * The dot rather than the whole chip tinted: this sits on cards and in forms
 * beside marks that already carry meaning in their fill, and a second filled
 * shape beside them would be one colour too many to read at a glance.
 */
export function GoalChip({
  name,
  colour,
  title,
}: {
  name: string
  colour: string
  title?: string
}) {
  return (
    <span className={styles.chip} title={title ?? name}>
      <span className={styles.dot} style={{ background: colour }} aria-hidden="true" />
      <span className={styles.chipName}>{name}</span>
    </span>
  )
}
