/**
 * Typed access to the Cylist API.
 *
 * Every failure the server produces arrives in one envelope:
 *   { "error": { "code": "...", "message": "...", "details": {} } }
 * so `request` turns any non-2xx response into an `ApiError` carrying that
 * code. Callers branch on `error.code`, never on a status number.
 *
 * Once the backend is running, regenerate exact response types with
 * `npm run api:types`.
 */

const API_BASE = '/api/v1'

export type Scope = 'read' | 'write' | 'vault:read' | 'vault:reveal' | 'admin'
export type PersonKind = 'team' | 'client'

export interface Identity {
  token_id: string
  label: string
  channel: 'web' | 'api'
  scopes: Scope[]
  /** The directory entry marked as you, if one has been named yet. */
  person: Person | null
}

export interface Person {
  id: string
  name: string
  kind: PersonKind
  role: string
  responsibilities: string
  email: string | null
  colour: string
  archived_at: string | null
  created_at: string
  /** Whether this is you. At most one person in the directory is. */
  is_me: boolean
}

export interface PersonInput {
  name: string
  kind: PersonKind
  role: string
  responsibilities: string
  email?: string | null
  /** Claim the directory's one "this is me" slot, taking it off whoever held it. */
  is_me?: boolean
}

export interface Project {
  id: string
  key: string
  name: string
  description: string
  colour: string
  archived_at: string | null
  created_at: string
  member_count: number
}

export interface ProjectSummary extends Project {
  team_count: number
  client_count: number
  folder_count: number
  /** Everything in the project's folders — uploads and links alike. */
  file_count: number
  task_count: number
  column_count: number
  blocked_count: number
  on_hold_count: number
  /** Goals on the project, settled ones included. */
  goal_count: number
  /** Goals still being worked towards — neither achieved nor dropped. */
  open_goal_count: number
  vault_tree_count: number
  /** Credentials stored across every tree. */
  vault_secret_count: number
}

export type TaskType = 'feature' | 'bug' | 'chore'
/** How soon a task needs attention. `p0` is drop everything, `p3` the default. */
export type TaskPriority = 'p0' | 'p1' | 'p2' | 'p3'
export type TaskStatus = 'active' | 'hold' | 'blocked' | 'cancelled'
/** Where one tick-box sub-task has got to. `done` and `cancelled` both settle it. */
export type ChecklistState = 'open' | 'done' | 'cancelled'
export type CommentKind = 'comment' | 'status_change'

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
  /** The cards on this goal, in board order. */
  tasks: Task[]
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

export interface BoardColumn {
  id: string
  project_id: string
  name: string
  description: string
  position: number
  /**
   * The sections this column is divided into, left to right — "Done",
   * "Cancelled", "In prod". Only the board's last column may have any, and
   * empty is the ordinary case: a column that draws no distinction.
   */
  outcomes: string[]
  task_count: number
}

/** A board's columns plus the limits the server enforces on them. */
export interface Board {
  columns: BoardColumn[]
  min_columns: number
  max_columns: number
  /** How many sections the last column may be divided into. */
  max_outcomes: number
}

export interface ColumnInput {
  name: string
  description: string
  /** Sent only for the board's last column; anywhere else it must be empty. */
  outcomes?: string[]
}

/**
 * One column a template's cards may sit in, and the sub-stages a card passes
 * through there.
 *
 * A card created from the template — or arriving here by a later move — has
 * these labels loaded onto its own `sub_statuses`, the same click-through
 * progress bar every card carries, and cannot leave the column until it is on
 * the last one.
 */
export interface TemplateStage {
  column_id: string
  /** So a stage reads without the board's columns fetched beside it. */
  column_name: string
  /** The sub-stages a card passes through here, left to right. May be empty:
   * a column can be named without asking anything of the card there. */
  sub_stage_labels: string[]
  /** Which of this column's outcomes the template's cards may end on. Empty
   * means all of them, the same silence `sub_stage_labels` keeps. */
  allowed_outcomes: string[]
}

/**
 * A kind of card — "Hotfix", "Design task" — and the rule for where its
 * cards go.
 *
 * A template carries nothing onto the task it makes beyond that rule: no
 * type, no priority. `stages` is the whole of it, one entry per column the
 * template's cards may sit in.
 */
export interface Template {
  id: string
  project_id: string
  name: string
  description: string
  stages: TemplateStage[]
  /**
   * The stages' column ids, in the same order — a convenience for knowing
   * where a card may go without reading what it owes to get there. **Empty
   * means unrestricted**: this template has no stages yet, so its cards go
   * anywhere on the board.
   */
  allowed_column_ids: string[]
  /** How many cards were created from it. Above zero, it cannot be deleted. */
  task_count: number
  created_at: string
}

/**
 * A template as it is written.
 *
 * `stages` is all-or-nothing on an edit: sending it replaces the whole set,
 * because the stages together are the policy. Leave it out to rename alone.
 */
export interface TemplateInput {
  name: string
  description?: string
  stages?: { column_id: string; sub_stage_labels: string[]; allowed_outcomes: string[] }[]
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
  headline: string
  markdown: string
}

export type AgentSessionState = 'working' | 'waiting' | 'done'
export type AgentSessionReason =
  'turn_ended' | 'permission' | 'idle' | 'question' | 'moved' | 'session_ended'

/** One harness session — a Claude Code conversation — on one card. */
export interface AgentSessionRead {
  id: string
  task_id: string
  /** The name of the token the hook reports with. */
  actor_label: string
  /** The harness's own id for the conversation. */
  client_session_id: string
  /** The session's display name, if known. Usually the card's reference. */
  client_name: string | null
  state: AgentSessionState
  reason: AgentSessionReason | null
  note: string | null
  started_at: string
  /** When `state` last changed. */
  state_changed_at: string
  /** When the hook last reported in. */
  last_seen_at: string
  ended_at: string | null
  dismissed_at: string | null
  /** A `working` session not heard from for long enough that the board no
   * longer believes it. Computed by the server, never stored. */
  is_stale: boolean
}

export type AgentPresenceState = 'working' | 'waiting' | 'done' | 'stale'

/**
 * What a card says about the agents on it, reduced to the one state its border
 * shows. `waiting` wins over `working` wins over `done`; a `stale` session is
 * ignored while another is live and shown only when it is all that is left.
 */
export interface AgentPresence {
  state: AgentPresenceState
  /** How many sessions this summarises: the open ones while any is open, the
   * finished-but-undismissed ones once none are. */
  count: number
  /** The deciding session's reason. */
  reason: AgentSessionReason | null
  /** The deciding session's name. */
  client_name: string | null
  /** When the deciding session entered its state. */
  since: string
  /** When the deciding session last reported in. */
  last_seen_at: string
}

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
  /** Which column the card is in. Null on a sub-task, which is not on the board. */
  column_id: string | null
  /** Where it sits in that column, from the top. Null on a sub-task. */
  position: number | null
  title: string
  description: string
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
  pr_ref: string | null
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
  comments: TaskComment[]
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
  assignee_id: string
  /** One of the project's templates, or null for a card with no template. */
  template_id: string | null
  /** One of the project's goals, or null for a card that stands on its own. */
  goal_id: string | null
  jira_ref: string | null
  pr_ref: string | null
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

export type VaultNodeKind = 'branch' | 'secret'

export interface VaultTree {
  id: string
  project_id: string
  name: string
  position: number
  node_count: number
  secret_count: number
  created_at: string
  updated_at: string
}

export interface VaultTreeDetail extends VaultTree {
  nodes: VaultNode[]
}

/** A secret's metadata. There is deliberately no `value` here — only
 * `revealSecret` returns one, and only with the `vault:reveal` scope. */
export interface VaultSecretMeta {
  username: string | null
  url: string | null
  notes: string
  key_version: number
  updated_at: string
}

export interface VaultNode {
  id: string
  tree_id: string
  parent_id: string | null
  name: string
  kind: VaultNodeKind
  position: number
  created_at: string
  updated_at: string
  secret: VaultSecretMeta | null
  children: VaultNode[]
}

export interface SecretInput {
  value?: string
  username?: string | null
  url?: string | null
  notes?: string
}

export interface VaultNodeInput {
  tree_id: string
  parent_id?: string | null
  name: string
  kind: VaultNodeKind
  secret?: SecretInput
}

export interface RevealedSecret {
  node_id: string
  name: string
  value: string
  revealed_at: string
}

export interface ProjectInput {
  key: string
  name: string
  description?: string
}

export type ItemKind = 'file' | 'link'
export type ItemSource = 'upload' | 'sharepoint' | 'gdrive' | 'other'

export interface Folder {
  id: string
  project_id: string
  /** Null only for the project's root folder. */
  parent_id: string | null
  name: string
  /**
   * Whether this is the project's root: one per project, named after it,
   * holding files and folders alike, and impossible to rename or delete.
   */
  is_root: boolean
  created_at: string
}

/** A folder in the whole-tree response, with its subfolders inside it. */
export interface FolderNode {
  id: string
  name: string
  parent_id: string | null
  is_root: boolean
  children: FolderNode[]
}

export interface FolderCrumb {
  id: string
  name: string
}

export interface FileItem {
  id: string
  folder_id: string
  kind: ItemKind
  name: string
  url: string | null
  source: ItemSource
  size: number | null
  mime: string | null
  added_by: Person | null
  created_at: string
}

export interface FolderChildren {
  folder: Folder
  /** The folders above this one, outermost first, ending with it. */
  path: FolderCrumb[]
  folders: Folder[]
  items: FileItem[]
}

export interface LinkInput {
  name: string
  url: string
  source: ItemSource
  added_by?: string | null
}

export interface Health {
  status: 'ok' | 'degraded'
  database: 'up' | 'down'
}

/** A failure the server described in its error envelope. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message)
    this.name = 'ApiError'
  }

  /** True when the caller is not signed in, or the credential was revoked. */
  get isUnauthenticated(): boolean {
    return this.code === 'unauthorized'
  }
}

interface ErrorEnvelope {
  error?: { code?: string; message?: string; details?: Record<string, unknown> }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      credentials: 'same-origin', // carries the session cookie
      headers: {
        Accept: 'application/json',
        // FormData carries its own multipart content type, boundary included;
        // setting one here would produce a body the server cannot parse.
        ...(init.body && !(init.body instanceof FormData)
          ? { 'Content-Type': 'application/json' }
          : {}),
        ...init.headers,
      },
    })
  } catch {
    throw new ApiError(0, 'network_error', 'Cannot reach the Cylist API. Is it running?')
  }

  if (response.status === 204) {
    return undefined as T
  }

  const body: unknown = await response.json().catch(() => null)

  if (!response.ok) {
    const envelope = (body ?? {}) as ErrorEnvelope
    throw new ApiError(
      response.status,
      envelope.error?.code ?? 'unknown_error',
      envelope.error?.message ?? 'Something went wrong.',
      envelope.error?.details ?? {},
    )
  }

  return body as T
}

const body = (value: unknown) => JSON.stringify(value)

export const api = {
  health: () => request<Health>('/health'),

  signIn: (password: string) =>
    request<Identity>('/auth/login', { method: 'POST', body: body({ password }) }),
  signOut: () => request<{ ok: boolean }>('/auth/logout', { method: 'POST' }),
  me: () => request<Identity>('/me'),

  listProjects: () => request<Project[]>('/projects'),
  getProject: (ref: string) => request<Project>(`/projects/${ref}`),
  getProjectSummary: (ref: string) => request<ProjectSummary>(`/projects/${ref}/summary`),
  /**
   * What was done on a project on one day.
   *
   * The zone is the caller's to name and is not optional here: the server
   * falls back to UTC, and a change made at 9pm in Kolkata would then land in
   * tomorrow's report.
   */
  getDayReport: (ref: string, day: string, timezone: string) =>
    request<DayReport>(
      `/projects/${ref}/reports/day?${new URLSearchParams({ date: day, timezone })}`,
    ),
  createProject: (input: ProjectInput) =>
    request<Project>('/projects', { method: 'POST', body: body(input) }),
  updateProject: (ref: string, input: Partial<ProjectInput>) =>
    request<Project>(`/projects/${ref}`, { method: 'PATCH', body: body(input) }),
  archiveProject: (ref: string) =>
    request<{ ok: boolean }>(`/projects/${ref}`, { method: 'DELETE' }),

  listMembers: (ref: string) => request<{ members: Person[] }>(`/projects/${ref}/members`),
  setMembers: (ref: string, personIds: string[]) =>
    request<{ members: Person[] }>(`/projects/${ref}/members`, {
      method: 'PUT',
      body: body({ person_ids: personIds }),
    }),

  /** The project's root folder, with the whole tree nested inside it. */
  getTree: (ref: string) => request<FolderNode>(`/projects/${ref}/tree`),
  createFolder: (ref: string, input: { name: string; parent_id: string | null }) =>
    request<Folder>(`/projects/${ref}/folders`, { method: 'POST', body: body(input) }),
  updateFolder: (id: string, input: { name?: string; parent_id?: string | null }) =>
    request<Folder>(`/folders/${id}`, { method: 'PATCH', body: body(input) }),
  deleteFolder: (id: string) => request<{ ok: boolean }>(`/folders/${id}`, { method: 'DELETE' }),
  getFolderChildren: (id: string) => request<FolderChildren>(`/folders/${id}/children`),

  uploadFile: (folderId: string, file: File, addedBy?: string | null) => {
    const form = new FormData()
    form.append('file', file)
    if (addedBy) form.append('added_by', addedBy)
    return request<FileItem>(`/folders/${folderId}/upload`, { method: 'POST', body: form })
  },
  addLink: (folderId: string, input: LinkInput) =>
    request<FileItem>(`/folders/${folderId}/links`, { method: 'POST', body: body(input) }),
  updateItem: (id: string, input: Partial<LinkInput>) =>
    request<FileItem>(`/items/${id}`, { method: 'PATCH', body: body(input) }),
  deleteItem: (id: string) => request<{ ok: boolean }>(`/items/${id}`, { method: 'DELETE' }),
  /** Where the browser fetches a file's bytes from — used as an anchor's href. */
  downloadUrl: (id: string) => `${API_BASE}/items/${id}/download`,
  listColumns: (ref: string) => request<Board>(`/projects/${ref}/columns`),
  createColumn: (ref: string, input: ColumnInput) =>
    request<BoardColumn>(`/projects/${ref}/columns`, { method: 'POST', body: body(input) }),
  updateColumn: (id: string, input: Partial<ColumnInput>) =>
    request<BoardColumn>(`/columns/${id}`, { method: 'PATCH', body: body(input) }),
  deleteColumn: (id: string) => request<{ ok: boolean }>(`/columns/${id}`, { method: 'DELETE' }),
  reorderColumns: (ref: string, columnIds: string[]) =>
    request<Board>(`/projects/${ref}/columns/order`, {
      method: 'PUT',
      body: body({ column_ids: columnIds }),
    }),

  listTemplates: (ref: string) => request<Template[]>(`/projects/${ref}/templates`),
  createTemplate: (ref: string, input: TemplateInput) =>
    request<Template>(`/projects/${ref}/templates`, { method: 'POST', body: body(input) }),
  updateTemplate: (id: string, input: Partial<TemplateInput>) =>
    request<Template>(`/templates/${id}`, { method: 'PATCH', body: body(input) }),
  deleteTemplate: (id: string) =>
    request<{ ok: boolean }>(`/templates/${id}`, { method: 'DELETE' }),

  listGoals: (ref: string, openOnly = false) =>
    request<Goal[]>(`/projects/${ref}/goals${openOnly ? '?open_only=true' : ''}`),
  getGoal: (goalRef: string) => request<GoalDetail>(`/goals/${goalRef}`),
  createGoal: (ref: string, input: GoalInput) =>
    request<GoalDetail>(`/projects/${ref}/goals`, { method: 'POST', body: JSON.stringify(input) }),
  updateGoal: (goalRef: string, input: Partial<GoalInput>) =>
    request<GoalDetail>(`/goals/${goalRef}`, { method: 'PATCH', body: JSON.stringify(input) }),
  deleteGoal: (goalRef: string) =>
    request<{ ok: boolean }>(`/goals/${goalRef}`, { method: 'DELETE' }),

  listTasks: (ref: string) => request<Task[]>(`/projects/${ref}/tasks`),
  getTask: (taskRef: string) => request<TaskDetail>(`/tasks/${taskRef}`),
  /**
   * One page of what has been done to a card — every edit, move and tick —
   * newest first. Its own request, made only when somebody asks to see it.
   */
  getTaskHistory: (taskRef: string, page: number, perPage = HISTORY_PER_PAGE) =>
    request<TaskHistoryPage>(`/tasks/${taskRef}/history?page=${page}&per_page=${perPage}`),
  /** The agent sessions on a card: open first, then finished-but-undismissed. */
  listAgentSessions: (taskRef: string) =>
    request<AgentSessionRead[]>(`/tasks/${taskRef}/agent-sessions`),
  /** Clear the finished sessions off a card. The ones still running stay. */
  dismissAgentSessions: (taskRef: string) =>
    request<void>(`/tasks/${taskRef}/agent-sessions/dismiss`, { method: 'POST' }),
  createTask: (ref: string, input: TaskInput) =>
    request<TaskDetail>(`/projects/${ref}/tasks`, { method: 'POST', body: body(input) }),
  /** Split a task into a sub-task with its own card, referenced `ATL-41-2`. */
  createSubtask: (parentRef: string, input: TaskInput) =>
    request<TaskDetail>(`/tasks/${parentRef}/subtasks`, { method: 'POST', body: body(input) }),
  addChecklistItem: (taskRef: string, title: string) =>
    request<ChecklistItem>(`/tasks/${taskRef}/checklist`, {
      method: 'POST',
      body: body({ title }),
    }),
  updateChecklistItem: (itemId: string, input: { title?: string; state?: ChecklistState }) =>
    request<ChecklistItem>(`/checklist/${itemId}`, { method: 'PATCH', body: body(input) }),
  deleteChecklistItem: (itemId: string) =>
    request<{ ok: boolean }>(`/checklist/${itemId}`, { method: 'DELETE' }),
  updateTask: (taskRef: string, input: TaskPatch) =>
    request<TaskDetail>(`/tasks/${taskRef}`, { method: 'PATCH', body: body(input) }),
  deleteTask: (taskRef: string) =>
    request<{ ok: boolean }>(`/tasks/${taskRef}`, { method: 'DELETE' }),
  /** `outcome` names a section of the board's last column; left out, a card
   * arriving there lands on the first one its template allows. */
  moveTask: (taskRef: string, columnId: string, position: number, outcome?: string | null) =>
    request<TaskDetail>(`/tasks/${taskRef}/move`, {
      method: 'POST',
      body: body({ column_id: columnId, position, ...(outcome ? { outcome } : {}) }),
    }),
  /** Ticks a sub-task off, or puts it back. The only way one is finished:
   * a sub-task is not on the board, so there is no last column to move it to. */
  finishTask: (taskRef: string, finished: boolean) =>
    request<TaskDetail>(`/tasks/${taskRef}/finish`, {
      method: 'POST',
      body: body({ finished }),
    }),
  setTaskStatus: (taskRef: string, change: StatusChange) =>
    request<TaskDetail>(`/tasks/${taskRef}/status`, { method: 'POST', body: body(change) }),
  /** Moves a task to one of its sub-status stages — backwards as readily as
   * forwards, which is what makes the board's control a slider. */
  setSubStatus: (taskRef: string, index: number) =>
    request<TaskDetail>(`/tasks/${taskRef}/sub-status`, { method: 'POST', body: body({ index }) }),
  addComment: (taskRef: string, text: string, authorId: string | null) =>
    request<TaskComment>(`/tasks/${taskRef}/comments`, {
      method: 'POST',
      body: body({ body: text, author_id: authorId }),
    }),

  listPeople: () => request<Person[]>('/people'),
  createPerson: (input: PersonInput) =>
    request<Person>('/people', { method: 'POST', body: body(input) }),
  updatePerson: (id: string, input: Partial<PersonInput>) =>
    request<Person>(`/people/${id}`, { method: 'PATCH', body: body(input) }),
  archivePerson: (id: string) => request<{ ok: boolean }>(`/people/${id}`, { method: 'DELETE' }),

  listVaultTrees: (ref: string) => request<VaultTree[]>(`/projects/${ref}/vault/trees`),
  createVaultTree: (ref: string, name: string) =>
    request<VaultTree>(`/projects/${ref}/vault/trees`, { method: 'POST', body: body({ name }) }),
  getVaultTree: (treeId: string) => request<VaultTreeDetail>(`/vault/trees/${treeId}`),
  renameVaultTree: (treeId: string, name: string) =>
    request<VaultTree>(`/vault/trees/${treeId}`, { method: 'PATCH', body: body({ name }) }),
  deleteVaultTree: (treeId: string) =>
    request<{ ok: boolean }>(`/vault/trees/${treeId}`, { method: 'DELETE' }),

  createVaultNode: (input: VaultNodeInput) =>
    request<VaultNode>('/vault/nodes', { method: 'POST', body: body(input) }),
  updateVaultNode: (nodeId: string, input: { name?: string; secret?: SecretInput }) =>
    request<VaultNode>(`/vault/nodes/${nodeId}`, { method: 'PATCH', body: body(input) }),
  deleteVaultNode: (nodeId: string) =>
    request<{ ok: boolean }>(`/vault/nodes/${nodeId}`, { method: 'DELETE' }),
  moveVaultNode: (nodeId: string, parentId: string | null, position: number) =>
    request<VaultNode>(`/vault/nodes/${nodeId}/move`, {
      method: 'POST',
      body: body({ parent_id: parentId, position }),
    }),

  /**
   * Decrypt one stored secret.
   *
   * The only call in this client that returns a credential, and the only one
   * the server writes to the audit trail on sight. Never cache what it
   * returns beyond the moment it is shown.
   */
  revealSecret: (nodeId: string) =>
    request<RevealedSecret>(`/vault/nodes/${nodeId}/reveal`, { method: 'POST' }),
}
