/**
 * The small drawn pieces of a goal: its colour, its progress, its date.
 *
 * They live together because they appear apart — on the goals page, on a
 * goal's own page, in the card dialog's picker and in the board's lane
 * headings — and a goal that reads as four different things in four places is
 * four things to learn instead of one.
 */

import type { ReactNode } from 'react'
import type { Goal, GoalStatus } from '../api/client'
import { dueBucket, formatDue, formatDueLong, type DueBucket } from './dates'
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
  const description = progressDescription(total, done, cancelled)

  return (
    <div
      className={`${styles.track} ${large ? styles.trackLarge : ''}`}
      role="img"
      aria-label={description}
      title={description}
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

  const { className, icon, text, description } = targetMarkParts(bucket, goal.target_date)

  return (
    <span className={`${styles.target} ${className}`} title={description}>
      {icon}
      <span aria-hidden="true">{text}</span>
      <span className="visually-hidden">{description}</span>
    </span>
  )
}

/** What the progress bar says in words, to a pointer and to a screen reader. */
function progressDescription(total: number, done: number, cancelled: number): string {
  if (total === 0) return 'No cards on this goal yet'
  const cancelledNote = cancelled ? `, ${cancelled} cancelled` : ''
  return `${done} of ${total} cards done${cancelledNote}`
}

/**
 * How a target date is drawn on each step of the ramp: its colour, its icon,
 * the short text on the mark, and the sentence that says it in full.
 */
function targetMarkParts(
  bucket: Exclude<DueBucket, { kind: 'none' }>,
  targetDate: string,
): { className: string | undefined; icon: ReactNode; text: string; description: string } {
  const on = formatDueLong(targetDate)
  switch (bucket.kind) {
    case 'late':
      return {
        className: styles.late,
        icon: <AlertIcon size={13} />,
        text: `${bucket.days}d over`,
        description: `${bucket.days} days past its target of ${on}`,
      }
    case 'today':
      return {
        className: styles.today,
        icon: <ClockIcon size={13} />,
        text: 'Today',
        description: `Targeted for today, ${on}`,
      }
    case 'tomorrow':
      return {
        className: styles.today,
        icon: <CalendarIcon size={13} />,
        text: 'Tomorrow',
        description: `Targeted for tomorrow, ${on}`,
      }
    case 'soon':
      return {
        className: styles.soon,
        icon: <CalendarIcon size={13} />,
        text: formatDue(targetDate),
        description: `Targeted for ${on}`,
      }
    case 'later':
      return {
        className: styles.later,
        icon: <CalendarIcon size={13} />,
        text: formatDue(targetDate),
        description: `Targeted for ${on}`,
      }
  }
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
