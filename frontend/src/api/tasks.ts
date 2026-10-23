/** Cards, their sub-tasks and tick boxes, and what has been said and done on them. */

import type { AgentPresence, AgentSessionRead } from './agents'
import type { FileItem } from './files'
import { jsonBody, request } from './http'
import { fetchPage, pagePath, type Page } from './paging'
import type { Person } from './people'

export type TaskType = 'feature' | 'bug' | 'chore'
/** How soon a task needs attention. `p0` is drop everything, `p3` the default. */
export type TaskPriority = 'p0' | 'p1' | 'p2' | 'p3'
export type TaskStatus = 'active' | 'hold' | 'blocked' | 'cancelled'
/** Where one tick-box sub-task has got to. `done` and `cancelled` both settle it. */
export type ChecklistState = 'open' | 'done' | 'cancelled'
export type CommentKind = 'comment' | 'status_change'

/**
 * How far one of a card's pull requests got.
 *
 * Three states and no more: is it up, has anybody said yes, is it in.
 * Everything a forge reports in between — draft, changes requested, checks
 * failing — is a question for the pull request itself, which the reference
 * links to.
 */
export type PrState = 'raised' | 'approved' | 'merged'

/**
 * One pull request on a card: where it is, and how far it got.
 *
 * One object rather than two parallel lists, because a state means nothing
 * apart from the pull request it is on — see the same reasoning on the column
 * itself in `app/models/task.py`.
 */
export interface PrRef {
  /** A URL, or a short form such as `#212`. Free text, as `jira_ref` is. */
  url: string
  state: PrState
}

/** What a `status_change` entry carries. Empty on a comment somebody typed. */
export interface StatusChangeMeta {
  from?: TaskStatus
  to?: TaskStatus
  reason?: string
  tagged?: string[]
}

export interface TaskComment {
  id: string
  task_id: string
  author: Person | null
  body: string
  kind: CommentKind
  meta: StatusChangeMeta
  created_at: string
}

/** A sub-task that is a tick box rather than a card: no owner, no reference. */
export interface ChecklistItem {
  id: string
  task_id: string
  title: string
  state: ChecklistState
  position: number
  created_at: string
}

/** One field of a card, as it read before and after somebody touched it. */
export interface FieldChange {
  /** The field's name on the task, e.g. `due_date`. */
  field: string
  /** How to word it, e.g. `due date`. */
  label: string
  /** What it said before, already rendered — a person by name, a date as
   * `YYYY-MM-DD`. Null when it was not set. */
  from: string | number | string[] | null
  to: string | number | string[] | null
}

/**
 * One thing that happened to a task: what changed, when, and who did it.
 *
 * The counterpart to `TaskComment`. A comment is what somebody *said* about
 * the card; this is what was *done* to it, whether by a person in the browser
 * or by an agent through the API — which is what `channel` distinguishes.
 */
export interface TaskHistoryEntry {
  id: string
  occurred_at: string
  /** A token's name, or `Web session` for somebody working in the browser. */
  actor_label: string
  channel: 'web' | 'api'
  /** Dotted past-tense event name, e.g. `task.moved`. */
  verb: string
  /** The same thing as one readable sentence, worded by the server so the
   * board, the CLI and an agent all tell the same story. */
  summary: string
  /** Field-by-field detail, where the event has any. Empty otherwise. */
  changes: FieldChange[]
}

/** How many cards of each column the board reads at a time. */
export const BOARD_PAGE = 50

/** How many history entries a page holds. The server's default, said out loud
 * so the board can size its pager without a round trip to find out. */
export const HISTORY_PER_PAGE = 10

/** One page of a card's history, and enough to draw a pager for the rest. */
export interface TaskHistoryPage {
  entries: TaskHistoryEntry[]
  /** How many entries the whole history holds. */
  total: number
  /** Which page this is, counting from 1. */
  page: number
  /** How many pages there are. At least 1, even when the history is empty. */
  pages: number
  per_page: number
}

/**
 * A card as a list draws it — everything but its description, which is the one
 * unbounded field on a card and is read with the card itself, `TaskDetail`.
 */
export interface Task {
  id: string
  project_id: string
  /** `ATL-41`, or `ATL-41-2` for a sub-task. Usable in place of the id. */
  reference: string
  /** Null on a sub-task, which is numbered under its parent instead. */
  number: number | null
  /** The card this was split out of, if any. */
  parent_id: string | null
  /** `ATL-41`, when this is a sub-task. */
  parent_reference: string | null
  /** `2` in `ATL-41-2`. Null at the top level. */
  sub_number: number | null
  /**
   * Which column the card is in. Null on a sub-task, which is not on the board,
   * and on a card in cold storage, which has left it.
   */
  column_id: string | null
  /**
   * Where it sits in that column: sort ascending for top to bottom. A sort key,
   * not an index — it has gaps and may be negative, so it is never a count of
   * the cards above. Null on a sub-task and in cold storage.
   */
  position: number | null
  title: string
  type: TaskType
  priority: TaskPriority
  /** Up to 4 stage labels, left to right. Empty if the card doesn't use this. */
  sub_statuses: string[]
  /** Index into `sub_statuses` of the current stage. Null when the list is empty. */
  sub_status_index: number | null
  /**
   * When the card is wanted in the board's last column, `YYYY-MM-DD` — which is
   * when the work is wanted done. Null when the card has no date.
   */
  due_date: string | null
  /** Dates for the columns before the last one, in board order. */
  column_due_dates: ColumnDueDate[]
  /**
   * The date the card is working towards now: the soonest of the dates it has
   * not met, `due_date` among them. Null once the card is done. This is the
   * date a card is drawn with — `due_date` is the end of the line, this is the
   * next thing owed.
   */
  next_due_date: string | null
  assignee: Person
  status: TaskStatus
  /** The template this card was created from, if any. Null is unrestricted. */
  template_id: string | null
  /** That template's name, so a card reads without the template list beside it. */
  template_name: string | null
  /** The goal this card is work towards, if any. Null stands on its own. */
  goal_id: string | null
  /** `ATL-G1`, when the card is on a goal. */
  goal_reference: string | null
  /** That goal's name, so a card reads without the goal list beside it. */
  goal_name: string | null
  /** That goal's hex colour — the rail the board draws. Null when the card is
   * on no goal, and the board draws its status colour instead. */
  goal_colour: string | null
  jira_ref: string | null
  /**
   * The pull requests this card's work lands as, in the order they were added,
   * each with how far it got. Empty on a card that has none yet, which most
   * cards are for most of their lives — never null, so there is one way to say
   * "none" rather than two.
   */
  pr_refs: PrRef[]
  waiting_on: Person[]
  comment_count: number
  checklist: ChecklistItem[]
  /**
   * How the work ended: the section of the board's last column this card is
   * in, by name. Null on every card that is not in a column divided that way.
   */
  outcome: string | null
  /** Which of the column's `outcomes` that is. Null exactly when `outcome` is. */
  outcome_index: number | null
  /**
   * When this task was finished, or null while it is open. A sub-task is
   * finished by being ticked off; a card by being moved into the board's last
   * column, and moving it back out clears this.
   */
  finished_at: string | null
  /**
   * When this card was put in cold storage, or null while it is on the board.
   * A stored card is still finished; restoring it puts it back in the board's
   * last column.
   */
  archived_at: string | null
  /**
   * Sub-tasks — cards and tick boxes together — that are neither finished nor
   * cancelled. While this is above zero the card cannot reach the last column.
   */
  open_subtask_count: number
  /** How many sub-tasks in all, cancelled ones excluded. `total - open` are done. */
  subtask_count: number
  /**
   * Who owns this card's sub-tasks, in sub-number order and each named once.
   * The board shows these faces because it no longer shows where the work is.
   */
  subtask_assignees: Person[]
  /**
   * Who is working on this card right now, if an agent is — the one state the
   * card's border shows. Null when no agent is on it, which is most cards.
   */
  agent_session: AgentPresence | null
  created_at: string
}

export interface TaskDetail extends Task {
  description: string
  /** The newest entries of the timeline, oldest first — the last fifty at most.
   * `comment_count` says how many there are in all. */
  comments: TaskComment[]
  /** Pass to `listComments` for the entries before these. Null when `comments`
   * is the whole timeline. */
  comments_next: string | null
  /** Sub-tasks with a reference of their own, in sub-number order. */
  subtasks: Task[]
  /** Every harness session on this card still worth showing: the open ones
   * first, then the finished ones nobody has dismissed. */
  agent_sessions: AgentSessionRead[]
}

/**
 * A date a card is wanted in one particular column by.
 *
 * The last column is not among these: a card is done when it reaches the end of
 * the board, so the date for the end of the board is the card's own `due_date`.
 */
export interface ColumnDueDateInput {
  column_id: string
  /** `YYYY-MM-DD`. */
  due_date: string
}

export interface ColumnDueDate extends ColumnDueDateInput {
  /** That column's name, so a date reads without the board beside it. */
  column_name: string
  /** Whether the card has reached that column. A met date is behind the card. */
  met: boolean
}

/** The fields of a task the board can edit. Status moves separately. */
export interface TaskInput {
  title: string
  description: string
  type: TaskType
  priority: TaskPriority
  /** Up to 4 short stage labels. Moving between them happens on the board. */
  sub_statuses: string[]
  /** `YYYY-MM-DD`, or null for a card with no date. The last column's date. */
  due_date: string | null
  /**
   * Dates for the columns on the way there. Sent whole: what goes up replaces
   * every per-column date the card had. Never sent for a sub-task, which is not
   * on the board.
   */
  column_due_dates?: ColumnDueDateInput[]
  /**
   * Who owns it. The form always sends one — it opens on whoever is signed
   * in — though the API takes a card without it and puts it on its creator.
   */
  assignee_id: string
  /** One of the project's templates, or null for a card with no template. */
  template_id: string | null
  /** One of the project's goals, or null for a card that stands on its own. */
  goal_id: string | null
  jira_ref: string | null
  /**
   * Every pull request the card names, sent whole: adding one is a longer
   * list, removing one a shorter list, moving one along is the same list with
   * one `state` changed, and `[]` takes them all off. Blank and repeated
   * entries are dropped by the server, so the form need not.
   */
  pr_refs: PrRef[]
}

/**
 * A partial edit of a task.
 *
 * `sub_status_index` is not part of `TaskInput` because creating a card cannot
 * say it — a new card starts on its first stage. Editing one can: reordering
 * or deleting a stage moves the current marker, and the form doing the
 * reordering is the only thing that knows where it ended up.
 */
export interface TaskPatch extends Partial<TaskInput> {
  sub_status_index?: number
}

export interface StatusChange {
  status: TaskStatus
  reason?: string
  waiting_on?: string[]
}

/** What putting a board's finished cards in cold storage did. */
export interface ColdStorageRun {
  /** The column they were taken from: the board's last. */
  column: string
  /** The cards stored, top to bottom as they stood. */
  references: string[]
  /** The CSV they were written to, in the project's `Cold storage` folder. */
  file: FileItem
}

/** The top of one column of the board, and how many cards it holds in all. */
export interface ColumnTasks {
  column_id: string
  total: number
  items: Task[]
  /** Pass to `listColumnTasks` for the cards below these. Null when that is all. */
  next: string | null
}

/** A board's cards, a page per column, left to right. */
export interface BoardTasks {
  columns: ColumnTasks[]
}

/** A page of a card's timeline, newest first, and how long the whole of it is. */
export interface CommentPage extends Page<TaskComment> {
  total: number
}

export const tasksApi = {
  /** The top `limit` cards of every column. */
  listTasks: (projectKey: string, limit = BOARD_PAGE, signal?: AbortSignal) =>
    request<BoardTasks>(`/projects/${projectKey}/tasks?limit=${limit}`, { signal }),
  /** The cards of one column after `after`, continuing from its `next`. */
  listColumnTasks: (columnId: string, after: string, limit = BOARD_PAGE, signal?: AbortSignal) =>
    fetchPage<Task>(`/columns/${columnId}/tasks`, after, limit, signal),
  /** A page of a card's timeline, newest first, from `after` back. */
  listComments: (taskRef: string, after: string | null, signal?: AbortSignal) =>
    request<CommentPage>(pagePath(`/tasks/${taskRef}/comments`, after, 50), { signal }),
  getTask: (taskRef: string, signal?: AbortSignal) =>
    request<TaskDetail>(`/tasks/${taskRef}`, { signal }),
  /**
   * One page of what has been done to a card — every edit, move and tick —
   * newest first. Its own request, made only when somebody asks to see it.
   */
  getTaskHistory: (
    taskRef: string,
    page: number,
    perPage = HISTORY_PER_PAGE,
    signal?: AbortSignal,
  ) =>
    request<TaskHistoryPage>(`/tasks/${taskRef}/history?page=${page}&per_page=${perPage}`, {
      signal,
    }),
  createTask: (projectKey: string, input: TaskInput) =>
    request<TaskDetail>(`/projects/${projectKey}/tasks`, { method: 'POST', body: jsonBody(input) }),
  /** Split a task into a sub-task with its own card, referenced `ATL-41-2`. */
  createSubtask: (parentRef: string, input: TaskInput) =>
    request<TaskDetail>(`/tasks/${parentRef}/subtasks`, { method: 'POST', body: jsonBody(input) }),
  addChecklistItem: (taskRef: string, title: string) =>
    request<ChecklistItem>(`/tasks/${taskRef}/checklist`, {
      method: 'POST',
      body: jsonBody({ title }),
    }),
  updateChecklistItem: (itemId: string, input: { title?: string; state?: ChecklistState }) =>
    request<ChecklistItem>(`/checklist/${itemId}`, { method: 'PATCH', body: jsonBody(input) }),
  deleteChecklistItem: (itemId: string) =>
    request<{ ok: boolean }>(`/checklist/${itemId}`, { method: 'DELETE' }),
  updateTask: (taskRef: string, input: TaskPatch) =>
    request<TaskDetail>(`/tasks/${taskRef}`, { method: 'PATCH', body: jsonBody(input) }),
  deleteTask: (taskRef: string) =>
    request<{ ok: boolean }>(`/tasks/${taskRef}`, { method: 'DELETE' }),
  /** `outcome` names a section of the board's last column; left out, a card
   * arriving there lands on the first one its template allows. */
  moveTask: (taskRef: string, columnId: string, position: number, outcome?: string | null) =>
    request<TaskDetail>(`/tasks/${taskRef}/move`, {
      method: 'POST',
      body: jsonBody({ column_id: columnId, position, ...(outcome ? { outcome } : {}) }),
    }),
  /** Ticks a sub-task off, or puts it back. The only way one is finished:
   * a sub-task is not on the board, so there is no last column to move it to. */
  finishTask: (taskRef: string, finished: boolean) =>
    request<TaskDetail>(`/tasks/${taskRef}/finish`, {
      method: 'POST',
      body: jsonBody({ finished }),
    }),
  setTaskStatus: (taskRef: string, change: StatusChange) =>
    request<TaskDetail>(`/tasks/${taskRef}/status`, { method: 'POST', body: jsonBody(change) }),
  /** Moves a task to one of its sub-status stages — backwards as readily as
   * forwards, which is what makes the board's control a slider. */
  setSubStatus: (taskRef: string, index: number) =>
    request<TaskDetail>(`/tasks/${taskRef}/sub-status`, {
      method: 'POST',
      body: jsonBody({ index }),
    }),
  addComment: (taskRef: string, text: string, authorId: string | null) =>
    request<TaskComment>(`/tasks/${taskRef}/comments`, {
      method: 'POST',
      body: jsonBody({ body: text, author_id: authorId }),
    }),
  /**
   * Take a comment off a card's timeline.
   *
   * Its author may take back their own; an admin of the project may take off
   * anybody's. A status change is on the same timeline and is refused — it is
   * the card's history rather than something somebody said.
   */
  deleteComment: (commentId: string) =>
    request<void>(`/comments/${commentId}`, { method: 'DELETE' }),
  /** A page of the board's cards in cold storage, most recently finished first. */
  listColdStorage: (projectKey: string, after: string | null = null, signal?: AbortSignal) =>
    fetchPage<Task>(`/projects/${projectKey}/cold-storage`, after, undefined, signal),
  /**
   * Take every card in the board's last column off the board, into a CSV in the
   * project's files. The cards are kept, not deleted, and each can be restored.
   */
  storeFinished: (projectKey: string) =>
    request<ColdStorageRun>(`/projects/${projectKey}/cold-storage`, { method: 'POST' }),
  /** Put a stored card back at the top of the board's last column, still finished. */
  restoreTask: (taskRef: string) =>
    request<TaskDetail>(`/tasks/${taskRef}/restore`, { method: 'POST' }),
}
