/**
 * Which of a board's cards are today's work.
 *
 * Two questions, in the order the card asks them: what is wanted by now, and
 * of that, what to pick up first. Due date decides membership — a card wanted
 * next Tuesday is not today's problem, however urgent it is — and priority
 * decides the order within the day, because that is what priority is for.
 *
 * Kept out of the component for the reason `boardSearch.ts` is: none of it
 * touches React, and a list that quietly leaves out a card you needed to do
 * today is a wrong answer nobody goes looking for. It is plain array work,
 * easiest to get right on its own and to test the same way.
 *
 * Dates are compared as the `YYYY-MM-DD` strings the API sends, which sort
 * lexicographically into date order. Deliberately no `Date` anywhere here:
 * the one boundary that needs a clock is "what is today", and that is the
 * caller's to supply — see `localToday` in `dates.ts`, which cuts it in the
 * reader's own zone rather than UTC's.
 */

import type { BoardColumn, Person, Task, TaskPriority } from '../api/client'

/** Most urgent first. The four values are a scale, and this is it. */
const PRIORITY_RANK: Record<TaskPriority, number> = {
  urgent: 0,
  asap: 1,
  week: 2,
  someday: 3,
}

export interface TodayWork {
  /** Wanted before today and still open, the longest-overdue first. */
  overdue: Task[]
  /** Wanted today, the most urgent first. */
  due: Task[]
}

/**
 * Today's work off one board.
 *
 * `me` narrows it to your own cards, which is what makes this a to-do list
 * rather than a report on the project. Null — nobody in the directory is
 * marked as you yet — falls back to the whole board: an empty panel that
 * cannot say why it is empty is worse than a wider answer.
 *
 * A card is left out when it is in the board's last column, because that is
 * what finished means here, and when it is cancelled, because that is work
 * which is not going to happen. Cards on hold and blocked stay in: a blocked
 * card that was wanted yesterday is the most important thing the list can
 * tell you, and hiding it would hide the problem rather than the card.
 */
export function todaysWork(
  tasks: readonly Task[],
  columns: readonly BoardColumn[],
  me: Person | null,
  today: string,
): TodayWork {
  // The last column is the board's own definition of done — the same one the
  // server enforces when it refuses to move a card there with sub-tasks still
  // outstanding. A board with no columns has no done column either.
  const done = columns.at(-1)?.id ?? null

  const mine = tasks.filter((task) => {
    if (task.status === 'cancelled') return false
    if (done !== null && task.column_id === done) return false
    if (me && task.assignee.id !== me.id) return false
    return task.due_date !== null && task.due_date <= today
  })

  return {
    overdue: mine
      .filter((task) => task.due_date !== null && task.due_date < today)
      // Oldest first: how long a card has been late is the whole of what makes
      // one overdue card more pressing than another, and it outranks priority
      // here for that reason. Priority still breaks the ties.
      .sort((a, b) => (a.due_date ?? '').localeCompare(b.due_date ?? '') || byPriority(a, b)),
    due: mine.filter((task) => task.due_date === today).sort(byPriority),
  }
}

/** Most urgent first, and within a level the order the cards were numbered —
 * so a list that has not changed does not shuffle itself between renders. */
function byPriority(a: Task, b: Task): number {
  return (
    PRIORITY_RANK[a.priority] - PRIORITY_RANK[b.priority] ||
    (a.number ?? 0) - (b.number ?? 0) ||
    a.reference.localeCompare(b.reference)
  )
}

/** How many cards the list is holding, for the count on the folded heading. */
export function todaysWorkCount(work: TodayWork): number {
  return work.overdue.length + work.due.length
}
