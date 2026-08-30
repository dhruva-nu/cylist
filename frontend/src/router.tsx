/**
 * Route table.
 *
 * Projects are addressed by their key rather than their id — `/p/ATL` is
 * readable, bookmarkable and matches what the API accepts.
 */

import { createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { Shell } from './components/Shell'
import { Home } from './routes/Home'
import { ProjectBoard } from './routes/ProjectBoard'
import { ProjectFiles } from './routes/ProjectFiles'
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
