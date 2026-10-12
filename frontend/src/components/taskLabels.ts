/**
 * What a card's status and priority are called, wherever a card is drawn.
 *
 * One list for the board and the task dialog both, so the filter on the board,
 * the pill on a card and the control in the dialog cannot offer different
 * words for the same state.
 */

import type { TaskPriority, TaskStatus } from '../api/client'

export const STATUS_LABELS: Record<TaskStatus, string> = {
  active: 'Active',
  hold: 'On hold',
  blocked: 'Blocked',
  cancelled: 'Cancelled',
}

/** The four statuses, in the order every control offers them. */
export const STATUSES: { value: TaskStatus; label: string }[] = (
  ['active', 'hold', 'blocked', 'cancelled'] as const
).map((value) => ({ value, label: STATUS_LABELS[value] }))

/**
 * The four levels, and what each one asks of whoever picks the card up.
 *
 * The label is the level alone — it is what the chip and the board carry, and
 * a level is the whole of what P0 means to anyone who has read one before. The
 * gloss is for the picker, where somebody is choosing between them.
 */
export const PRIORITIES: { value: TaskPriority; label: string; means: string }[] = [
  { value: 'p0', label: 'P0', means: 'drop what you are doing' },
  { value: 'p1', label: 'P1', means: 'as soon as P0 is clear' },
  { value: 'p2', label: 'P2', means: 'this week' },
  { value: 'p3', label: 'P3', means: 'some time' },
]

/** A level with its gloss — "P0 — drop what you are doing" — for a picker or a tooltip. */
export function priorityInFull(priority: TaskPriority): string {
  const option = PRIORITIES.find((candidate) => candidate.value === priority)
  return option ? `${option.label} — ${option.means}` : priority
}
