/**
 * What a `board.changed` makes stale.
 *
 * The server says which things changed — `{entity_type, entity_id}`, no more —
 * and this is the one place that knows what each kind of thing is cached as.
 * Reading everything for every change was the cost of not asking; a card
 * edited by an agent now refetches the board's cards and that card, and a
 * hook edited by an admin refetches the hooks.
 */

import type { QueryKey } from '@tanstack/react-query'
import { affects, queryKeys } from './queries'
import type { ChangedRef } from './realtime'

/** What a change to a card reads again: the board's cards, and every place a card is open. */
function cardKeys(projectKey: string): QueryKey[] {
  return [
    queryKeys.tasks(projectKey),
    queryKeys.allTasks,
    queryKeys.allTaskHistory,
    queryKeys.todayPlans(projectKey),
    queryKeys.allAgentFeeds,
  ]
}

/** What each kind of entity is cached under, for a project. */
const KEYS: Record<string, (projectKey: string) => QueryKey[]> = {
  task: cardKeys,
  column: (p) => [queryKeys.board(p), queryKeys.tasks(p)],
  goal: (p) => [queryKeys.goals(p), queryKeys.allGoals],
  template: (p) => [queryKeys.templates(p)],
  role: (p) => [queryKeys.roles(p), queryKeys.members(p), queryKeys.permissions(p)],
  project: (p) => [
    queryKeys.project(p),
    queryKeys.members(p),
    queryKeys.roles(p),
    queryKeys.permissions(p),
  ],
  person: (p) => [queryKeys.people, queryKeys.members(p), queryKeys.tasks(p)],
  hook: () => [...affects.hooks],
  doc: () => [...affects.docs],
  doc_topic: () => [...affects.docs],
  folder: () => [...affects.files],
  item: () => [...affects.files],
  skill: () => [...affects.skills],
  vault_tree: () => [...affects.vault],
  vault_node: () => [...affects.vault],
}

/**
 * Every query a `board.changed` with this hint makes stale, for `projectKey`.
 *
 * A hint of `null` is "something changed and nobody says what", which is also
 * what a resync after a dropped socket is: the cards, the columns and the goals,
 * the three that a missed change most often leaves wrong. A kind of thing this
 * does not know is refreshed as a card would be, which is what every change
 * used to be.
 */
export function keysToRefresh(
  projectKey: string,
  changed: readonly ChangedRef[] | null,
): QueryKey[] {
  if (changed === null) {
    return [...cardKeys(projectKey), queryKeys.board(projectKey), queryKeys.goals(projectKey)]
  }
  const keys: QueryKey[] = []
  for (const ref of changed) {
    keys.push(...(KEYS[ref.entity_type] ?? cardKeys)(projectKey))
  }
  return dedupe(keys)
}

/** Join what has been named since the last refetch with what has just been, `null` for "anything". */
export function joinChanged(
  waiting: readonly ChangedRef[] | null | undefined,
  arriving: readonly ChangedRef[] | null,
): ChangedRef[] | null {
  if (waiting === null || arriving === null) return null
  return [...(waiting ?? []), ...arriving]
}

function dedupe(keys: QueryKey[]): QueryKey[] {
  const seen = new Set<string>()
  return keys.filter((key) => {
    const id = JSON.stringify(key)
    if (seen.has(id)) return false
    seen.add(id)
    return true
  })
}
