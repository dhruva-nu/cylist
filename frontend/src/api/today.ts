/** Your plan for a day: the cards you picked up, and your to-dos under them. */

import { jsonBody, request } from './http'

/**
 * Where a to-do stands. `moved` and `not_done` are what wrapping a day up
 * leaves behind; a moved one has a live copy on the day it went to.
 */
export type TodoState = 'open' | 'done' | 'moved' | 'not_done'

export interface Todo {
  id: string
  /** As written, `@person` and `>file` tags included. */
  text: string
  state: TodoState
  done_at: string | null
  /** The day it was handed on to, as `YYYY-MM-DD`, when it was. */
  moved_to: string | null
  /** The day it was handed on from, when it arrived by wrap-up. */
  carried_from: string | null
  /**
   * What on the card it stands for, when it came from the card. Ticking it
   * ticks that too — finishes the sub-task, ticks the tick box, moves the card
   * on past the stage — and ticking that on the board ticks this.
   */
  source_kind: 'stage' | 'subtask' | 'checklist' | null
  created_at: string
}

export interface PickedCard {
  id: string
  reference: string
  title: string
  /** Its column now; null once it has gone to cold storage. */
  column: string | null
}

/** One card on your plan for a day, with your to-dos under it. */
export interface DayPick {
  id: string
  day: string
  task: PickedCard
  todos: Todo[]
}

export interface TodayPlan {
  project_key: string
  day: string
  timezone: string
  /** In the order the cards were picked. */
  picks: DayPick[]
  open_count: number
  done_count: number
}

export interface WrapUpResult {
  day: string
  to_day: string
  moved: number
  not_done: number
}

export const todayApi = {
  getToday: (projectKey: string, day: string, timezone: string) =>
    request<TodayPlan>(
      `/projects/${projectKey}/today?${new URLSearchParams({ date: day, timezone })}`,
    ),
  /** Put a card on a day. Picking one already there hands the same pick back. */
  pickCard: (projectKey: string, task: string, day: string) =>
    request<DayPick>(`/projects/${projectKey}/today/picks`, {
      method: 'POST',
      body: jsonBody({ task, day }),
    }),
  /** Take a card off its day, and its to-dos for that day with it. */
  unpickCard: (pickId: string) =>
    request<{ ok: boolean }>(`/today/picks/${pickId}`, { method: 'DELETE' }),
  addTodo: (pickId: string, text: string) =>
    request<Todo>(`/today/picks/${pickId}/todos`, { method: 'POST', body: jsonBody({ text }) }),
  updateTodo: (todoId: string, change: { text?: string; state?: 'open' | 'done' | 'not_done' }) =>
    request<Todo>(`/today/todos/${todoId}`, { method: 'PATCH', body: jsonBody(change) }),
  deleteTodo: (todoId: string) =>
    request<{ ok: boolean }>(`/today/todos/${todoId}`, { method: 'DELETE' }),
  /** Close a day: `move` goes on to `toDay`, every other open to-do stays as not done. */
  wrapUpDay: (projectKey: string, day: string, toDay: string, move: string[]) =>
    request<WrapUpResult>(`/projects/${projectKey}/today/wrap-up`, {
      method: 'POST',
      body: jsonBody({ day, to_day: toDay, move }),
    }),
}
