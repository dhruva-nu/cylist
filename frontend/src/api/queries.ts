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

import { useQuery, useQueryClient, type QueryKey } from '@tanstack/react-query'
import { useCallback } from 'react'
import { ApiError, api } from './client'

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
  /** Every card's detail, whichever project it is on. */
  allTasks: ['task'],
  goals: (projectKey: string) => ['goals', projectKey],
  /** Every goal's page, whichever project it is on. */
  allGoals: ['goal'],
  goal: (goalRef: string) => ['goal', goalRef],
} as const satisfies Record<string, QueryKey | ((...args: string[]) => QueryKey)>

/**
 * Who this session is.
 *
 * Not being signed in is an answer rather than a failure, so it is not retried
 * — the gate in `App.tsx` turns it into the sign-in screen straight away.
 */
export function useIdentity() {
  return useQuery({
    queryKey: queryKeys.identity,
    queryFn: api.me,
    retry: (failureCount, error) =>
      !(error instanceof ApiError && error.isUnauthenticated) && failureCount < 2,
  })
}

/** What this deployment is, and whether anybody can sign in to it yet. */
export function useSetup() {
  return useQuery({ queryKey: queryKeys.setup, queryFn: api.setup })
}

export function useProjects() {
  return useQuery({ queryKey: queryKeys.projects, queryFn: api.listProjects })
}

export function useProject(projectKey: string) {
  return useQuery({
    queryKey: queryKeys.project(projectKey),
    queryFn: () => api.getProject(projectKey),
  })
}

/** The whole directory, every project's people together. */
export function usePeople() {
  return useQuery({ queryKey: queryKeys.people, queryFn: api.listPeople })
}

export function useMembers(projectKey: string) {
  return useQuery({
    queryKey: queryKeys.members(projectKey),
    queryFn: () => api.listMembers(projectKey),
  })
}

/** The project's roles, as the screen that manages them lists them. */
export function useRoles(projectKey: string) {
  return useQuery({
    queryKey: queryKeys.roles(projectKey),
    queryFn: () => api.listRoles(projectKey),
  })
}

/**
 * The project's permission grid: what each role may do, the words to draw it
 * with, and what the reader may do here.
 */
export function usePermissionGrid(projectKey: string) {
  return useQuery({
    queryKey: queryKeys.permissions(projectKey),
    queryFn: () => api.getPermissions(projectKey),
  })
}

/** The board's columns, and the limits the server holds them to. */
export function useBoard(projectKey: string) {
  return useQuery({
    queryKey: queryKeys.board(projectKey),
    queryFn: () => api.listColumns(projectKey),
  })
}

export function useTemplates(projectKey: string) {
  return useQuery({
    queryKey: queryKeys.templates(projectKey),
    queryFn: () => api.listTemplates(projectKey),
  })
}

export function useTasks(projectKey: string) {
  return useQuery({
    queryKey: queryKeys.tasks(projectKey),
    queryFn: () => api.listTasks(projectKey),
  })
}

export function useGoals(projectKey: string) {
  return useQuery({
    queryKey: queryKeys.goals(projectKey),
    queryFn: () => api.listGoals(projectKey),
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
