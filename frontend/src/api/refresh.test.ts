import { QueryClient } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'
import { affects, invalidates, queryKeys, refreshesItself } from './queries'
import { refreshAfterWrite } from './refresh'

function refreshed(meta: Record<string, unknown> | undefined) {
  const client = new QueryClient()
  const invalidate = vi.spyOn(client, 'invalidateQueries').mockResolvedValue()
  refreshAfterWrite(client, { meta })
  return invalidate.mock.calls.map(([filters]) => filters)
}

describe('refreshAfterWrite', () => {
  it('reads everything again after a write that says nothing', () => {
    expect(refreshed(undefined)).toEqual([undefined])
  })

  it('reads only what a write names', () => {
    const keys = [queryKeys.hooks('ATL'), queryKeys.allHookDeliveries]

    expect(refreshed(invalidates(...keys))).toEqual(keys.map((queryKey) => ({ queryKey })))
  })

  it('reads nothing after a write that names nothing', () => {
    expect(refreshed(invalidates())).toEqual([])
  })

  it('leaves a write that refreshes itself to it', () => {
    expect(refreshed(refreshesItself)).toEqual([])
  })
})

describe('affects', () => {
  it('never reaches the board, the cards, or the directory', () => {
    // That is what lets a write to hooks or docs skip the blanket refetch: the
    // expensive lists are not theirs.
    const kept = new Set<unknown>(
      [
        queryKeys.identity,
        queryKeys.projects,
        queryKeys.people,
        queryKeys.allBoardCards,
        queryKeys.allBoards,
        queryKeys.allTasks,
        queryKeys.allTaskHistory,
        queryKeys.allGoals,
      ].map((key) => key[0]),
    )
    const reached = Object.values(affects)
      .flat()
      .map((key) => key[0])

    expect(reached.filter((root) => kept.has(root))).toEqual([])
  })
})
