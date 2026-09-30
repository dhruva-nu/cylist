/**
 * What the board puts away by default, and when it stops.
 *
 * Two kinds of card are not this week's work, and the board hides both without
 * being asked — but they are not hidden the same way, because they are not
 * hidden for the same reason (CYLIST-62).
 *
 * A **cancelled** card is not going to happen at all. Nothing on the board
 * wants it back, so it goes away board-wide and comes back from one button in
 * the filter drawer.
 *
 * A card **on hold** is work somebody put down on purpose and means to pick up.
 * Which ones they are is a question about a column — "what has stalled in
 * review?" — so it is answered inside the column, by a line at its foot that
 * opens that column's held cards and leaves every other column as it was.
 *
 * Both hides stand down when the reader asks for exactly what they put away: a
 * board that answered "show me the cancelled ones" with nothing would be one
 * filter quietly overruling another. That rule is the whole reason this is a
 * module of its own rather than four `filter` calls in `ProjectBoard.tsx`.
 */

import type { ParsedSearch } from './boardSearch'
import type { Task } from '../api/client'

/** The status filter's value: every card, or one status. */
export type StatusChoice = 'all' | Task['status']

/** Whether a card is one of the cancelled ones the board hides board-wide. */
export function isCancelled(task: Task): boolean {
  return task.status === 'cancelled'
}

/** Whether a card is one a column holds back at its foot. */
export function isOnHold(task: Task): boolean {
  return task.status === 'hold'
}

/**
 * Whether the cancelled cards are being kept off the board.
 *
 * On unless the reader has turned it off from the filter drawer, or has asked
 * for the cancelled cards by name — the status filter set to "Cancelled", or
 * `cnl:` typed in the search box.
 */
export function hidesCancelled(
  showCancelled: boolean,
  status: StatusChoice,
  parsed: ParsedSearch,
): boolean {
  if (showCancelled) return false
  if (status === 'cancelled') return false
  return !parsed.cancelled
}

/**
 * Whether a column is holding its on-hold cards back.
 *
 * `expanded` is that one column's answer — the foot of a column is opened per
 * column, because a board is read a column at a time. The other two are the
 * board's, and they overrule it the same way they overrule the cancelled hide:
 * a status filter on "On hold", or `hld:` in the search box, is a reader asking
 * for precisely these cards, and every column shows them.
 */
export function hidesHold(expanded: boolean, status: StatusChoice, parsed: ParsedSearch): boolean {
  if (expanded) return false
  if (status === 'hold') return false
  return !parsed.hold
}

/** A column's cards, split into the ones it draws and the ones it holds back. */
export interface HoldSplit {
  shown: Task[]
  onHold: Task[]
}

/**
 * Splits one column's cards at its foot.
 *
 * `onHold` is filled whether or not the hide is in force, so the column always
 * knows how many cards the hide is about; `hiding` decides only whether they
 * are also drawn. Order is the order they arrived in — a held card that is
 * shown again goes back where it was rather than to the bottom, because the
 * position of a card in a column is something somebody chose.
 */
export function splitOnHold(tasks: Task[], hiding: boolean): HoldSplit {
  const onHold = tasks.filter(isOnHold)
  return { shown: hiding ? tasks.filter((task) => !isOnHold(task)) : tasks, onHold }
}
