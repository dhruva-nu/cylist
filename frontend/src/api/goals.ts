/** Goals: the epics a board's cards are written under. */

import { jsonBody, request } from './http'
import { fetchAllPages, fetchPage } from './paging'
import type { Person } from './people'
import type { Task } from './tasks'

/** Whether a goal is still being worked towards, and if not, how it ended. */
export type GoalStatus = 'open' | 'achieved' | 'dropped'

/**
 * How far along a goal is, counted from the cards linked to it.
 *
 * Derived server-side on every read rather than stored: a percentage kept
 * beside the cards is a number that can disagree with them.
 */
export interface GoalProgress {
  /** Cards linked to this goal, cancelled ones included. */
  total: number
  /** Cards in the board's last column. */
  done: number
  /** Cards dropped. Settled, but not achieved. */
  cancelled: number
  /** Neither done nor cancelled — what is left. */
  open: number
  /** Open cards that cannot proceed. */
  blocked: number
  /** Open cards deliberately paused. */
  on_hold: number
}

/**
 * An outcome a board's cards are work towards — an epic.
 *
 * It is deliberately not a card: no column, no position, no template. A goal
 * is what the work is for, and its colour is the rail its cards wear.
 */
export interface Goal {
  id: string
  project_id: string
  /** `ATL-G1`. Usable in place of the id, and unchanged by a rename. */
  reference: string
  number: number
  name: string
  description: string
  /** Six-digit hex — the rail the board draws down this goal's cards. */
  colour: string
  status: GoalStatus
  /** `YYYY-MM-DD`, or null when nobody has said. */
  target_date: string | null
  /** When it was reached. Null until it is. */
  achieved_at: string | null
  owner: Person
  progress: GoalProgress
  created_at: string
}

export interface GoalDetail extends Goal {
  /** The first page of the cards on this goal, in board order; `progress.total`
   * says how many there are in all. */
  tasks: Task[]
  /** Pass to `listGoalTasks` for the cards after these. Null when that is all. */
  tasks_next: string | null
}

export interface GoalInput {
  name: string
  description?: string
  /** Six-digit hex. Omit to take a stable colour from the palette. */
  colour?: string
  target_date?: string | null
  owner_id: string
  status?: GoalStatus
}

export const goalsApi = {
  listGoals: (projectKey: string, openOnly = false) =>
    fetchAllPages<Goal>(`/projects/${projectKey}/goals${openOnly ? '?open_only=true' : ''}`),
  getGoal: (goalRef: string) => request<GoalDetail>(`/goals/${goalRef}`),
  /** The goal's cards after `after`, continuing from its `tasks_next`. */
  listGoalTasks: (goalRef: string, after: string) =>
    fetchPage<Task>(`/goals/${goalRef}/tasks`, after),
  createGoal: (projectKey: string, input: GoalInput) =>
    request<GoalDetail>(`/projects/${projectKey}/goals`, { method: 'POST', body: jsonBody(input) }),
  updateGoal: (goalRef: string, input: Partial<GoalInput>) =>
    request<GoalDetail>(`/goals/${goalRef}`, { method: 'PATCH', body: jsonBody(input) }),
  deleteGoal: (goalRef: string) =>
    request<{ ok: boolean }>(`/goals/${goalRef}`, { method: 'DELETE' }),
}
