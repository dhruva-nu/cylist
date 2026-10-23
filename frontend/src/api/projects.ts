/** Projects themselves: the list of them, and making and ending one. */

import { jsonBody, request } from './http'
import { fetchAllPages } from './paging'

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

export interface ProjectInput {
  key: string
  name: string
  description?: string
}

export const projectsApi = {
  listProjects: (signal?: AbortSignal) => fetchAllPages<Project>('/projects', signal),
  getProject: (projectKey: string, signal?: AbortSignal) =>
    request<Project>(`/projects/${projectKey}`, { signal }),
  createProject: (input: ProjectInput) =>
    request<Project>('/projects', { method: 'POST', body: jsonBody(input) }),
  /**
   * Destroy a project: its board, its files, its docs, its vault, all of it.
   *
   * `confirm` is the project's own key, and the server checks it rather than
   * trusting us to have asked. The screen asks for it typed out for the same
   * reason the server insists on it — this is the one call in the client that
   * nobody can undo.
   */
  deleteProject: (projectKey: string, confirm: string) =>
    request<void>(`/projects/${projectKey}?${new URLSearchParams({ confirm })}`, {
      method: 'DELETE',
    }),
}
