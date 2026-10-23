/**
 * The reads more than one screen makes, each under the one key every reader
 * shares.
 *
 * react-query answers every reader of a key from one request and refreshes
 * them all when it is invalidated — which only works while they all spell the
 * key the same way. A key written out by hand at each call site is how an
 * upload appears in one list and not the one beside it, so the shared ones are
 * spelled here, once, along with the hook that reads each.
 */

import { useMutation, useQuery, useQueryClient, type QueryKey } from '@tanstack/react-query'
import { useCallback } from 'react'
import { ApiError, api, type Task } from './client'

export const queryKeys = {
  identity: ['me'],
  setup: ['setup'],
  projects: ['projects'],
  people: ['people'],
  hookEvents: ['hook-events'],
  project: (projectKey: string) => ['project', projectKey],
  members: (projectKey: string) => ['members', projectKey],
  roles: (projectKey: string) => ['roles', projectKey],
  permissions: (projectKey: string) => ['permissions', projectKey],
  board: (projectKey: string) => ['board', projectKey],
  templates: (projectKey: string) => ['templates', projectKey],
  tasks: (projectKey: string) => ['tasks', projectKey],
  /** The board's cards in cold storage — off the board, so not in `tasks`. */
  coldStorage: (projectKey: string) => ['cold-storage', projectKey],
  /** Every card's detail, whichever project it is on. */
  allTasks: ['task'],
  task: (taskId: string | null) => ['task', taskId],
  /**
   * The history under a card's timeline. A prefix of every page's key, so
   * invalidating it refreshes whichever page is showing.
   */
  taskHistory: (taskId: string) => ['task-history', taskId],
  goals: (projectKey: string) => ['goals', projectKey],
  /** Every goal's page, whichever project it is on. */
  allGoals: ['goal'],
  goal: (goalRef: string) => ['goal', goalRef],
} as const satisfies Record<string, QueryKey | ((...args: string[]) => QueryKey)>

/**
 * A mutation's `meta` for one that invalidates what it changes itself, which
 * excuses it from the refetch of everything that follows any other write
 * (`main.tsx`). Only for a write whose keys are listed in full — a key it
 * forgets is a list that stays stale until a reload.
 */
export const refreshesItself = { refreshesItself: true } as const

/**
 * How long a list that hardly ever changes counts as fresh: who you are, what
 * you may do, the projects, a project and its people and templates.
 *
 * The default is half a minute, and every time the tab comes back into view
 * whatever is older than that is read again — which, for a reader moving
 * between this and a terminal, was all of these every visit. A change to one
 * from this tab still shows at once, because a write invalidates them
 * regardless of age; this only decides how soon someone else's change is
 * noticed.
 */
const SETTLED_MS = 5 * 60_000

/**
 * Who this session is.
 *
 * Not being signed in is an answer rather than a failure, so it is not retried
 * — the gate in `App.tsx` turns it into the sign-in screen straight away.
 */
export function useIdentity() {
  return useQuery({
    queryKey: queryKeys.identity,
    queryFn: ({ signal }) => api.me(signal),
    staleTime: SETTLED_MS,
    retry: (failureCount, error) =>
      !(error instanceof ApiError && error.isUnauthenticated) && failureCount < 2,
  })
}

/** What this deployment is, and whether anybody can sign in to it yet. */
export function useSetup() {
  return useQuery({ queryKey: queryKeys.setup, queryFn: ({ signal }) => api.setup(signal) })
}

export function useProjects() {
  return useQuery({
    queryKey: queryKeys.projects,
    queryFn: ({ signal }) => api.listProjects(signal),
    staleTime: SETTLED_MS,
  })
}

export function useProject(projectKey: string) {
  return useQuery({
    queryKey: queryKeys.project(projectKey),
    queryFn: ({ signal }) => api.getProject(projectKey, signal),
    staleTime: SETTLED_MS,
  })
}

/** The whole directory, every project's people together. */
export function usePeople() {
  return useQuery({ queryKey: queryKeys.people, queryFn: ({ signal }) => api.listPeople(signal) })
}

export function useMembers(projectKey: string) {
  return useQuery({
    queryKey: queryKeys.members(projectKey),
    queryFn: ({ signal }) => api.listMembers(projectKey, signal),
    staleTime: SETTLED_MS,
  })
}

/** The project's roles, as the screen that manages them lists them. */
export function useRoles(projectKey: string) {
  return useQuery({
    queryKey: queryKeys.roles(projectKey),
    queryFn: ({ signal }) => api.listRoles(projectKey, signal),
  })
}

/**
 * The project's permission grid: what each role may do, the words to draw it
 * with, and what the reader may do here.
 */
export function usePermissionGrid(projectKey: string) {
  return useQuery({
    queryKey: queryKeys.permissions(projectKey),
    queryFn: ({ signal }) => api.getPermissions(projectKey, signal),
    staleTime: SETTLED_MS,
  })
}

/** The board's columns, and the limits the server holds them to. */
export function useBoard(projectKey: string) {
  return useQuery({
    queryKey: queryKeys.board(projectKey),
    queryFn: ({ signal }) => api.listColumns(projectKey, signal),
  })
}

export function useTemplates(projectKey: string) {
  return useQuery({
    queryKey: queryKeys.templates(projectKey),
    queryFn: ({ signal }) => api.listTemplates(projectKey, signal),
    staleTime: SETTLED_MS,
  })
}

/** How far down one column of the board has been read. */
export interface ColumnCards {
  column_id: string
  /** Cards in the column in all, read or not. */
  total: number
  /** The cursor for the cards below the ones read. Null once all are. */
  next: string | null
  /** How many pages of it have been read — what a refetch reads again. */
  pages: number
}

/**
 * The board's cards as far as they have been read.
 *
 * A column arrives a page at a time (CYLIST-130): the board reads the top of
 * every column, and the reader asks for more of one. `tasks` is every card
 * read so far, left to right and top to bottom, which is the shape everything
 * that draws or searches the board already took.
 */
export interface BoardCards {
  tasks: Task[]
  columns: ColumnCards[]
}

/**
 * Read the board, as deep into each column as `previous` had read it.
 *
 * A refetch — the socket ringing, a write landing — reads the board again,
 * and one that went back to the first page of every column would take away
 * the cards the reader had asked for. A cursor whose card has since left the
 * column is refused, and the column then stops where it got to.
 */
export async function fetchBoardCards(
  projectKey: string,
  previous?: BoardCards,
  signal?: AbortSignal,
): Promise<BoardCards> {
  const board = await api.listTasks(projectKey, undefined, signal)
  const tasks: Task[] = []
  const columns: ColumnCards[] = []
  for (const column of board.columns) {
    const wanted = previous?.columns.find((read) => read.column_id === column.column_id)?.pages ?? 1
    let { next } = column
    let pages = 1
    tasks.push(...column.items)
    while (next && pages < wanted) {
      try {
        const page = await api.listColumnTasks(column.column_id, next, undefined, signal)
        tasks.push(...page.items)
        next = page.next
        pages += 1
      } catch (error) {
        if (error instanceof ApiError && error.code === 'unprocessable') break
        throw error
      }
    }
    columns.push({ column_id: column.column_id, total: column.total, next, pages })
  }
  return { tasks, columns }
}

/** The board's cards, as far as each column has been read — see `BoardCards`. */
export function useTasks(projectKey: string) {
  const queryClient = useQueryClient()
  return useQuery({
    queryKey: queryKeys.tasks(projectKey),
    queryFn: ({ signal }) =>
      fetchBoardCards(
        projectKey,
        queryClient.getQueryData<BoardCards>(queryKeys.tasks(projectKey)),
        signal,
      ),
  })
}

/**
 * Read the next page of one column into the board's cards.
 *
 * Appended to what is in hand rather than refetched, so the cards above stay
 * where the reader left them; a card that has since moved into the page is
 * not drawn twice.
 */
export function useLoadMoreCards(projectKey: string) {
  const queryClient = useQueryClient()
  const key = queryKeys.tasks(projectKey)
  return useMutation({
    // A read, not a write: it changes nothing anybody else has on screen, and
    // the refetch after a write would read every page it just appended again.
    meta: refreshesItself,
    mutationFn: async (columnId: string) => {
      const cursor = queryClient
        .getQueryData<BoardCards>(key)
        ?.columns.find((column) => column.column_id === columnId)?.next
      return cursor ? { columnId, page: await api.listColumnTasks(columnId, cursor) } : null
    },
    onSuccess: (loaded) => {
      if (!loaded) return
      queryClient.setQueryData<BoardCards>(key, (current) => {
        if (!current) return current
        const known = new Set(current.tasks.map((task) => task.id))
        return {
          tasks: [...current.tasks, ...loaded.page.items.filter((task) => !known.has(task.id))],
          columns: current.columns.map((column) =>
            column.column_id === loaded.columnId
              ? { ...column, next: loaded.page.next, pages: column.pages + 1 }
              : column,
          ),
        }
      })
    },
    // The cursor's card left the column; read the board again from the top.
    onError: () => queryClient.invalidateQueries({ queryKey: key }),
  })
}

export function useGoals(projectKey: string) {
  return useQuery({
    queryKey: queryKeys.goals(projectKey),
    queryFn: ({ signal }) => api.listGoals(projectKey, undefined, signal),
  })
}

/**
 * Mark several keys stale at once, and wait for what is on screen to refetch.
 *
 * What a screen calls after a change: the change touched more than the one
 * list it was made from, and each reader of the others should see it too.
 */
export function useInvalidate() {
  const queryClient = useQueryClient()
  return useCallback(
    async (...keys: QueryKey[]) => {
      await Promise.all(keys.map((queryKey) => queryClient.invalidateQueries({ queryKey })))
    },
    [queryClient],
  )
}
