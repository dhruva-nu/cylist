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
}

export interface PersonInput {
  name: string
  kind: PersonKind
  role: string
  responsibilities: string
  email?: string | null
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
  task_count: number
  column_count: number
  blocked_count: number
  on_hold_count: number
}

export type TaskType = 'feature' | 'bug' | 'chore'
export type TaskStatus = 'active' | 'hold' | 'blocked'
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

export interface Task {
  id: string
  project_id: string
  /** `ATL-41` — usable in place of the id on every task path. */
  reference: string
  number: number
  column_id: string
  position: number
  title: string
  description: string
  type: TaskType
  due_date: string
  assignee: Person
  status: TaskStatus
  jira_ref: string | null
  pr_ref: string | null
  waiting_on: Person[]
  comment_count: number
  created_at: string
}

export interface TaskDetail extends Task {
  comments: TaskComment[]
}

/** The fields of a task the board can edit. Status moves separately. */
export interface TaskInput {
  title: string
  description: string
  type: TaskType
  due_date: string
  assignee_id: string
  jira_ref: string | null
  pr_ref: string | null
}

export interface StatusChange {
  status: TaskStatus
  reason?: string
  waiting_on?: string[]
}

export interface ProjectInput {
  key: string
  name: string
  description?: string
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
        ...(init.body ? { 'Content-Type': 'application/json' } : {}),
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
  createTask: (ref: string, input: TaskInput) =>
    request<TaskDetail>(`/projects/${ref}/tasks`, { method: 'POST', body: body(input) }),
  updateTask: (taskRef: string, input: Partial<TaskInput>) =>
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
}
