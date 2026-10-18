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
 * The date read is `next_due_date` — the soonest date the card has not met,
 * which since CYLIST-25 may belong to a column on the way rather than to the
 * end of the line. That is the date the board draws a card with, and a list
 * called "today" sitting beside a board has to agree with it: on `due_date`
 * this would miss a card wanted in review this morning and claim one is free
 * all week when three of its columns are owed before Friday.
 *
 * Dates are compared as the `YYYY-MM-DD` strings the API sends, which sort
 * lexicographically into date order. Deliberately no `Date` anywhere here:
 * the one boundary that needs a clock is "what is today", and that is the
 * caller's to supply — see `localToday` in `dates.ts`, which cuts it in the
 * reader's own zone rather than UTC's.
 */

import type { BoardColumn, Person, Task, TaskPriority } from '../api/client'

/** Most urgent first. The four values are a scale, and this is it — so the
 * rank is the number in the name, and a fifth level would be one more line. */
const PRIORITY_RANK: Record<TaskPriority, number> = {
  p0: 0,
  p1: 1,
  p2: 2,
  p3: 3,
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
 * rather than a report on the project. Null — a session with no person behind
 * it, which is the bootstrap login and nothing else — falls back to the whole
 * board: an empty panel that cannot say why it is empty is worse than a wider
 * answer.
 *
 * A card is left out when it is in the board's last column, because that is
 * what finished means here, and when it is cancelled or on hold, because
 * neither is work to do today: cancelled is not going to happen at all, and a
 * card on hold has been put down on purpose, with a reason the server made
 * whoever put it down give. Its due date carries on running while it waits,
 * so left in it would sit at the top of the overdue group every morning
 * saying nothing that the hold had not already said.
 *
 * Blocked stays in. It reads like hold and is not: the work is still wanted
 * today and something is in the way, which is the most useful thing this list
 * can tell you — hiding it would hide the problem rather than the card.
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
  const doneColumnId = columns.at(-1)?.id ?? null

  const wantedByToday = tasks.filter((task) => {
    if (task.status === 'cancelled' || task.status === 'hold') return false
    if (doneColumnId !== null && task.column_id === doneColumnId) return false
    if (me && task.assignee.id !== me.id) return false
    return task.next_due_date !== null && task.next_due_date <= today
  })

  return {
    overdue: wantedByToday
      .filter((task) => task.next_due_date !== null && task.next_due_date < today)
      // Oldest first: how long a card has been late is the whole of what makes
      // one overdue card more pressing than another, and it outranks priority
      // here for that reason. Priority still breaks the ties.
      .sort(
        (a, b) => (a.next_due_date ?? '').localeCompare(b.next_due_date ?? '') || byPriority(a, b),
      ),
    due: wantedByToday.filter((task) => task.next_due_date === today).sort(byPriority),
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

/** One heading's worth of the cards a day can be planned from. */
export interface CardGroup {
  name: string
  /** Whether this is the group of cards that are due or late. */
  late: boolean
  tasks: Task[]
}

/**
 * Every open card on a board, as the planner offers them to pick from.
 *
 * Open means not in the board's last column, not cancelled and not in cold
 * storage (a stored card is in no column). On hold stays: it is still work,
 * and somebody may well mean to chase whatever it is waiting on today.
 *
 * Cards wanted by today come first, in the order {@link todaysWork} puts them
 * — that list is what the old Today showed, and it is still the first thing
 * worth seeing — then everything else by column, nearest to done first,
 * because a card in review is closer to being somebody's today than one in
 * the backlog. Within a column, board order.
 *
 * `mine` narrows to your own cards; `query` to cards whose reference or
 * title holds it.
 */
export function openCardGroups(
  tasks: readonly Task[],
  columns: readonly BoardColumn[],
  { me, mine, query, today }: { me: Person | null; mine: boolean; query: string; today: string },
): CardGroup[] {
  const doneColumnId = columns.at(-1)?.id ?? null
  const wanted = query.trim().toLowerCase()

  const open = tasks.filter((task) => {
    if (task.column_id === null || task.column_id === doneColumnId) return false
    if (task.status === 'cancelled') return false
    if (mine && me && task.assignee.id !== me.id) return false
    if (wanted && !`${task.reference} ${task.title}`.toLowerCase().includes(wanted)) return false
    return true
  })

  const dueOrLate = open
    .filter((task) => task.next_due_date !== null && task.next_due_date <= today)
    .sort(
      (a, b) => (a.next_due_date ?? '').localeCompare(b.next_due_date ?? '') || byPriority(a, b),
    )
  const pulledUp = new Set(dueOrLate.map((task) => task.id))

  const groups: CardGroup[] = []
  if (dueOrLate.length) groups.push({ name: 'Due or late', late: true, tasks: dueOrLate })
  for (const column of [...columns].reverse()) {
    if (column.id === doneColumnId) continue
    const here = open
      .filter((task) => task.column_id === column.id && !pulledUp.has(task.id))
      .sort((a, b) => (a.position ?? 0) - (b.position ?? 0))
    if (here.length) groups.push({ name: column.name, late: false, tasks: here })
  }
  return groups
}

/**
 * Whether a card wanted by today has been left off the plan.
 *
 * What turns the sidebar's count red. The count itself is the to-dos left;
 * this is the old Today's warning kept alive — something of yours is late
 * and you have not planned to touch it.
 */
export function lateAndUnplanned(
  tasks: readonly Task[],
  columns: readonly BoardColumn[],
  me: Person | null,
  today: string,
  planned: ReadonlySet<string>,
): boolean {
  return todaysWork(tasks, columns, me, today).overdue.some((task) => !planned.has(task.id))
}
