/**
 * Route table.
 *
 * Projects are addressed by their key rather than their id — `/p/ATL` is
 * readable, bookmarkable and matches what the API accepts.
 */

import { createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { Shell } from './components/Shell'
import { GoalPage } from './routes/GoalPage'
import { Home } from './routes/Home'
import { ProjectBoard } from './routes/ProjectBoard'
import { ProjectFiles } from './routes/ProjectFiles'
import { ProjectGoals } from './routes/ProjectGoals'
import { ProjectHub } from './routes/ProjectHub'
import { ProjectPeople } from './routes/ProjectPeople'
import { ProjectVault } from './routes/ProjectVault'

const rootRoute = createRootRoute({ component: Shell })

const homeRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  component: Home,
})

const projectRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/p/$projectKey',
  component: ProjectHub,
})

const projectBoardRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/p/$projectKey/board',
  component: ProjectBoard,
  // `?q=` so a board can be linked to already filtered — which is how a goal's
  // page hands you its own cards. Anything else in the query string is
  // dropped rather than carried: a search box is the whole of what this route
  // takes from a URL.
  validateSearch: (search: Record<string, unknown>): { q?: string } =>
    typeof search.q === 'string' && search.q ? { q: search.q } : {},
})

const projectGoalsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/p/$projectKey/goals',
  component: ProjectGoals,
})

// A goal is addressed by its reference — `/p/ATL/goals/ATL-G1` — rather than
// by its name, so a renamed goal keeps the link somebody bookmarked.
const goalRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/p/$projectKey/goals/$goalRef',
  component: GoalPage,
})

const projectFilesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/p/$projectKey/files',
  component: ProjectFiles,
})

const projectPeopleRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/p/$projectKey/people',
  component: ProjectPeople,
})

const projectVaultRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/p/$projectKey/vault',
  component: ProjectVault,
})

const routeTree = rootRoute.addChildren([
  homeRoute,
  projectRoute,
  projectBoardRoute,
  projectGoalsRoute,
  goalRoute,
  projectFilesRoute,
  projectVaultRoute,
  projectPeopleRoute,
])

export const router = createRouter({ routeTree })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
