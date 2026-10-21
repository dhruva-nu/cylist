/** Skills: the packaged jobs an agent on a project can be handed. */

import { API_BASE, request } from './http'
import { fetchAllPages } from './paging'
import type { Person } from './people'

/** An uploaded skill: a packaged job an agent can be handed. */
export interface Skill {
  id: string
  project_id: string
  name: string
  description: string | null
  size: number
  mime: string
  added_by: Person | null
  created_at: string
}

export const skillsApi = {
  listSkills: (projectKey: string) => fetchAllPages<Skill>(`/projects/${projectKey}/skills`),
  /** Filed under whoever is signed in: the server takes the uploader from the session. */
  uploadSkill: (projectKey: string, file: File, description?: string) => {
    const form = new FormData()
    form.append('file', file)
    if (description) form.append('description', description)
    return request<Skill>(`/projects/${projectKey}/skills`, { method: 'POST', body: form })
  },
  /**
   * Upload a whole folder as one skill, structure and all.
   *
   * Every file goes as its own `file` part named with the path it has inside
   * the folder — `webkitRelativePath`, which is the only place the browser
   * keeps it, since a `File`'s own name is the bare one. The server lays the
   * parts out, checks them and stores them as `<folder>.zip`.
   */
  uploadSkillFolder: (
    projectKey: string,
    folder: string,
    files: readonly File[],
    description?: string,
  ) => {
    const form = new FormData()
    form.append('folder', folder)
    for (const file of files) form.append('file', file, file.webkitRelativePath || file.name)
    if (description) form.append('description', description)
    return request<Skill>(`/projects/${projectKey}/skills`, { method: 'POST', body: form })
  },
  deleteSkill: (id: string) => request<{ ok: boolean }>(`/skills/${id}`, { method: 'DELETE' }),
  /** Where the browser fetches a skill's bytes from — used as an anchor's href. */
  skillDownloadUrl: (id: string) => `${API_BASE}/skills/${id}/download`,
}
