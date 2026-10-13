/**
 * Typed access to the Cylist API.
 *
 * Every resource has a module of its own beside this one — its wire types and
 * the calls that move them — and this file gathers them back into the one
 * `api` object and the one import path every screen already uses. Importing
 * from here or from the resource's own module is the same thing; here is the
 * habit.
 *
 * How a request is made and how a failure arrives is `http.ts`. Once the
 * backend is running, regenerate exact response types with
 * `npm run api:types`.
 */

import { agentsApi } from './agents'
import { authApi } from './auth'
import { boardApi } from './board'
import { docsApi } from './docs'
import { filesApi } from './files'
import { goalsApi } from './goals'
import { peopleApi } from './people'
import { projectsApi } from './projects'
import { reportsApi } from './reports'
import { rolesApi } from './roles'
import { skillsApi } from './skills'
import { tasksApi } from './tasks'
import { vaultApi } from './vault'
import { webhooksApi } from './webhooks'

export { ApiError, errorMessage } from './http'
export * from './agents'
export * from './auth'
export * from './board'
export * from './docs'
export * from './files'
export * from './goals'
export * from './people'
export * from './projects'
export * from './reports'
export * from './roles'
export * from './skills'
export * from './tasks'
export * from './vault'
export * from './webhooks'

export const api = {
  ...authApi,
  ...peopleApi,
  ...projectsApi,
  ...reportsApi,
  ...rolesApi,
  ...filesApi,
  ...docsApi,
  ...skillsApi,
  ...boardApi,
  ...webhooksApi,
  ...goalsApi,
  ...tasksApi,
  ...agentsApi,
  ...vaultApi,
}
