/**
 * Route table.
 *
 * Projects are addressed by their key rather than their id — `/p/ATL` is
 * readable, bookmarkable and matches what the API accepts.
 *
 * Every page is its own chunk, fetched when its route is first visited: the
 * shell and the sign-in screen are what everybody needs, and the vault, the
 * hooks and the docs editor are what most visits never open.
 */

import {
  createRootRoute,
  createRoute,
  createRouter,
  lazyRouteComponent,
  redirect,
} from '@tanstack/react-router'
import { BASE_PATH } from './api/basePath'
import { Shell } from './components/Shell'
import { EmptyState } from './components/ui'

const rootRoute = createRootRoute({ component: Shell })

const homeRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  component: lazyRouteComponent(() => import('./routes/home/Home'), 'Home'),
})

// A project's own address opens its board. It used to open an overview — a
// page of cards linking to each area — which the sidebar's list of areas has
// replaced; the address stays, because it is in bookmarks and in links pasted
// into chat, and `replace` so Back does not land on a page that only bounces
// you forward again.
const projectRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/p/$projectKey',
  beforeLoad: ({ params }) => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- how TanStack redirects
    throw redirect({ to: '/p/$projectKey/board', params, replace: true })
  },
})

const projectBoardRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/p/$projectKey/board',
  component: lazyRouteComponent(() => import('./routes/ProjectBoard'), 'ProjectBoard'),
  // `?q=` so a board can be linked to already filtered — which is how a goal's
  // page hands you its own cards — and `?who=` for whose cards it shows
  // (CYLIST-99), so a shared link shows the same people. Anything else in the
  // query string is dropped rather than carried.
  validateSearch: (search: Record<string, unknown>): { q?: string; who?: string } => ({
    ...(typeof search.q === 'string' && search.q ? { q: search.q } : {}),
    ...(typeof search.who === 'string' && search.who ? { who: search.who } : {}),
  }),
})

const projectGoalsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/p/$projectKey/goals',
  component: lazyRouteComponent(() => import('./routes/goals/ProjectGoals'), 'ProjectGoals'),
})

// A goal is addressed by its reference — `/p/ATL/goals/ATL-G1` — rather than
// by its name, so a renamed goal keeps the link somebody bookmarked.
const goalRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/p/$projectKey/goals/$goalRef',
  component: lazyRouteComponent(() => import('./routes/goals/GoalPage'), 'GoalPage'),
})

const projectFilesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/p/$projectKey/files',
  component: lazyRouteComponent(() => import('./routes/files/ProjectFiles'), 'ProjectFiles'),
})

// `?doc=` so a doc can be linked to — pasted into a card or a chat — and open
// on the doc rather than on the tree it is somewhere in. `?ask=` the same for
// a question put to the docs, so an answer can be passed on too. Anything else
// in the query string is dropped, as the board drops it.
const projectDocsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/p/$projectKey/docs',
  component: lazyRouteComponent(() => import('./routes/docs/ProjectDocs'), 'ProjectDocs'),
  validateSearch: (search: Record<string, unknown>): { doc?: string; ask?: string } => ({
    ...(typeof search.doc === 'string' && search.doc ? { doc: search.doc } : {}),
    ...(typeof search.ask === 'string' && search.ask.trim() ? { ask: search.ask } : {}),
  }),
})

const projectPeopleRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/p/$projectKey/people',
  component: lazyRouteComponent(() => import('./routes/people/ProjectPeople'), 'ProjectPeople'),
})

// Where an admin says who is what on this board and what each of those things
// may do. Its own route rather than a tab of People, because half of what is
// on it — the permission grid — is about the board rather than about anybody.
const projectRolesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/p/$projectKey/roles',
  component: lazyRouteComponent(() => import('./routes/roles/ProjectRoles'), 'ProjectRoles'),
})

// Where an admin sends the board's changes to a URL. Beside Roles, and admin-only
// like it: a hook's URL is often a capability in its own right.
const projectHooksRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/p/$projectKey/hooks',
  component: lazyRouteComponent(() => import('./routes/webhooks/ProjectHooks'), 'ProjectHooks'),
})

// A project's area, not the platform's: an agent's token is scoped per
// project, so what it may do on one board is not what it may do on the next.
const projectAgentsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/p/$projectKey/agents',
  component: lazyRouteComponent(() => import('./routes/agents/Agents'), 'Agents'),
})

const projectVaultRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/p/$projectKey/vault',
  component: lazyRouteComponent(() => import('./routes/vault/ProjectVault'), 'ProjectVault'),
})

const routeTree = rootRoute.addChildren([
  homeRoute,
  projectRoute,
  projectBoardRoute,
  projectGoalsRoute,
  goalRoute,
  projectFilesRoute,
  projectDocsRoute,
  projectVaultRoute,
  projectPeopleRoute,
  projectRolesRoute,
  projectHooksRoute,
  projectAgentsRoute,
])

// Every route above is relative to the base path, so `/p/ATL/board` is
// `/dev_1/p/ATL/board` in a dev slot, and every `<Link>` says so.
export const router = createRouter({
  routeTree,
  basepath: BASE_PATH || '/',
  // What stands in for a page while its chunk is on the way, once that has
  // taken long enough to notice. Without it the shell sits there empty.
  defaultPendingComponent: () => <EmptyState>Loading…</EmptyState>,
})

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
