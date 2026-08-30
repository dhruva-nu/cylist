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

  listPeople: () => request<Person[]>('/people'),
  createPerson: (input: PersonInput) =>
    request<Person>('/people', { method: 'POST', body: body(input) }),
  updatePerson: (id: string, input: Partial<PersonInput>) =>
    request<Person>(`/people/${id}`, { method: 'PATCH', body: body(input) }),
  archivePerson: (id: string) => request<{ ok: boolean }>(`/people/${id}`, { method: 'DELETE' }),
}
