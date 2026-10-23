/** What a project's day amounted to. */

import { request } from './http'
import type { TaskHistoryEntry, TaskStatus } from './tasks'
import type { TodoState } from './today'

/** One card's share of a day: everything that happened to it, oldest first. */
export interface TaskDay {
  /** `ATL-41`, or `ATL-41-2` for a sub-task. */
  reference: string
  /** Its title now — or the one it had at the time, if it has since been deleted. */
  title: string
  /**
   * Which column it sits in now. Null on a sub-task, which is not on the board,
   * and on a card that no longer exists — `parent` tells the two apart.
   */
  column: string | null
  /** `ATL-41`, when this is a sub-task. Null on a card and on anything deleted. */
  parent: string | null
  status: TaskStatus | null
  /**
   * Whether the day left it finished: a card by ending it in the board's last
   * column, a sub-task by being ticked off.
   */
  finished: boolean
  /**
   * What happened to it, oldest first — with its moves collapsed to the one
   * they amounted to. A card walked To do → In progress → Dev in a day got
   * from To do to Dev; `payload.moves` on that entry says how many drags it
   * stands for, and the card's own history still holds each of them.
   */
  entries: TaskHistoryEntry[]
}

/** One to-do from your plan for the day, and how the day left it. */
export interface DayTodo {
  text: string
  state: TodoState
  moved_to: string | null
  carried_from: string | null
}

/** One card on your plan for the day, with what you meant to do about it. */
export interface CardTodos {
  reference: string
  title: string
  /** Where the card sits now; null in cold storage. */
  column: string | null
  todos: DayTodo[]
}

/**
 * What one project's day amounted to.
 *
 * The same audit entries a card's history is made of, cut at the boundaries of
 * one local day and grouped by the card they happened to, and condensed: a
 * card's moves arrive as the one move they amounted to. `markdown` is the
 * whole thing already worded, so a note pasted out of the browser and one an
 * agent writes say the same.
 */
export interface DayReport {
  project_key: string
  project_name: string
  /** The day reported on, as `YYYY-MM-DD` in `timezone`. */
  day: string
  timezone: string
  /** Midnight that began the day, in UTC. */
  starts_at: string
  /** Midnight that ended it, in UTC. Exclusive. */
  ends_at: string
  /** How many lines the report holds, a card's day of moves counting as one. */
  entry_count: number
  /** References of the cards that ended the day in the board's last column. */
  finished: string[]
  /** The cards touched, in the order they were first touched. */
  tasks: TaskDay[]
  /** Changes that were not to a card — files, columns, the vault, the project. */
  elsewhere: TaskHistoryEntry[]
  /** Your own plan for the day, from Today, in the order the cards were picked. */
  todos: CardTodos[]
  headline: string
  markdown: string
}

export const reportsApi = {
  /**
   * What was done on a project on one day.
   *
   * The zone is the caller's to name and is not optional here: the server
   * falls back to UTC, and a change made at 9pm in Kolkata would then land in
   * tomorrow's report.
   */
  getDayReport: (projectKey: string, day: string, timezone: string, signal?: AbortSignal) =>
    request<DayReport>(
      `/projects/${projectKey}/reports/day?${new URLSearchParams({ date: day, timezone })}`,
      { signal },
    ),
}
