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
  vault_tree_count: number
  /** Credentials stored across every tree. */
  vault_secret_count: number
}

export type TaskType = 'feature' | 'bug' | 'chore'
/** How soon a task needs attention. `urgent` is 0, `someday` is 3 — the default. */
export type TaskPriority = 'urgent' | 'asap' | 'week' | 'someday'
export type TaskStatus = 'active' | 'hold' | 'blocked' | 'cancelled'
/** Where one tick-box sub-task has got to. `done` and `cancelled` both settle it. */
export type ChecklistState = 'open' | 'done' | 'cancelled'
export type CommentKind = 'comment' | 'status_change'

export interface BoardColumn {
  id: string
  project_id: string
  name: string
  description: string
  position: number
  task_count: number
}

/** A board's columns plus the limits the server enforces on them. */
export interface Board {
  columns: BoardColumn[]
  min_columns: number
  max_columns: number
}

export interface ColumnInput {
  name: string
  description: string
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
  column_id: string
  position: number
  title: string
  description: string
  type: TaskType
  priority: TaskPriority
  /** Up to 4 stage labels, left to right. Empty if the card doesn't use this. */
  sub_statuses: string[]
  /** Index into `sub_statuses` of the current stage. Null when the list is empty. */
  sub_status_index: number | null
  due_date: string
  assignee: Person
  status: TaskStatus
  jira_ref: string | null
  pr_ref: string | null
  waiting_on: Person[]
  comment_count: number
  checklist: ChecklistItem[]
  /**
   * Sub-tasks — cards and tick boxes together — that are neither finished nor
   * cancelled. While this is above zero the card cannot reach the last column.
   */
  open_subtask_count: number
  created_at: string
}

export interface TaskDetail extends Task {
  comments: TaskComment[]
  /** Sub-tasks with a card of their own, in sub-number order. */
  subtasks: Task[]
}

/** The fields of a task the board can edit. Status moves separately. */
export interface TaskInput {
  title: string
  description: string
  type: TaskType
  priority: TaskPriority
  /** Up to 4 short stage labels. Moving between them happens on the board. */
  sub_statuses: string[]
  due_date: string
  assignee_id: string
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

  listTasks: (ref: string) => request<Task[]>(`/projects/${ref}/tasks`),
  getTask: (taskRef: string) => request<TaskDetail>(`/tasks/${taskRef}`),
  /** What has been done to a card — every edit, move and tick — newest first. */
  getTaskHistory: (taskRef: string) => request<TaskHistoryEntry[]>(`/tasks/${taskRef}/history`),
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
  moveTask: (taskRef: string, columnId: string, position: number) =>
    request<TaskDetail>(`/tasks/${taskRef}/move`, {
      method: 'POST',
      body: body({ column_id: columnId, position }),
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
