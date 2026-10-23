// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type * as ClientModule from '../../api/client'
import { api, type Task, type TaskDetail } from '../../api/client'
import { queryKeys, type BoardCards } from '../../api/queries'
import { useBoardMutations, type Move } from './useBoardMutations'

import '../../test/dom'

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof ClientModule>()
  return { ...actual, api: { ...actual.api, moveTask: vi.fn(), updateTask: vi.fn() } }
})

const card = (id: string, column_id: string, position: number) =>
  ({ id, reference: `ATL-${id}`, column_id, position, goal_id: null }) as unknown as Task

const START: BoardCards = {
  tasks: [card('a', 'todo', 1000), card('b', 'todo', 2000), card('c', 'doing', 1000)],
  columns: [],
}

let client: QueryClient

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

const drop: Move = {
  taskId: 'a',
  taskRef: 'ATL-a',
  columnId: 'doing',
  position: 0,
  outcome: null,
  outcomeIndex: null,
}

const cards = () => client.getQueryData<BoardCards>(queryKeys.tasks('ATL'))?.tasks ?? []
const columnOf = (id: string) => cards().find((task) => task.id === id)?.column_id

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
  client.setQueryData(queryKeys.tasks('ATL'), START)
  vi.mocked(api.moveTask).mockReset()
})

afterEach(() => {
  client.clear()
})

describe('moving a card', () => {
  it('draws it in its new column before the server has answered', async () => {
    vi.mocked(api.moveTask).mockReturnValue(new Promise(() => undefined))
    const { result } = renderHook(() => useBoardMutations('ATL', [], [], true), { wrapper })

    act(() => result.current.moveTask.mutate(drop))

    await waitFor(() => expect(columnOf('a')).toBe('doing'))
    // Above the card that was already there, not merely in the same column.
    const [moved, there] = [cards().find((t) => t.id === 'a'), cards().find((t) => t.id === 'c')]
    expect((moved?.position ?? 0) < (there?.position ?? 0)).toBe(true)
  })

  it('puts the cards back as they were when the server refuses', async () => {
    vi.mocked(api.moveTask).mockRejectedValue(new Error('You cannot move cards there.'))
    const { result } = renderHook(() => useBoardMutations('ATL', [], [], true), { wrapper })

    act(() => result.current.moveTask.mutate(drop))
    await waitFor(() => expect(result.current.moveTask.isError).toBe(true))

    expect(cards()).toEqual(START.tasks)
  })

  it('keeps the card where it was dropped when the server agrees', async () => {
    vi.mocked(api.moveTask).mockResolvedValue(card('a', 'doing', 500) as unknown as TaskDetail)
    const { result } = renderHook(() => useBoardMutations('ATL', [], [], true), { wrapper })

    act(() => result.current.moveTask.mutate(drop))
    await waitFor(() => expect(result.current.moveTask.isSuccess).toBe(true))

    expect(columnOf('a')).toBe('doing')
    expect(api.moveTask).toHaveBeenCalledWith('a', 'doing', 0, null)
  })

  it('does not touch the other cards while it is in the air', async () => {
    vi.mocked(api.moveTask).mockReturnValue(new Promise(() => undefined))
    const { result } = renderHook(() => useBoardMutations('ATL', [], [], true), { wrapper })

    act(() => result.current.moveTask.mutate(drop))
    await waitFor(() => expect(columnOf('a')).toBe('doing'))

    expect(columnOf('b')).toBe('todo')
    expect(columnOf('c')).toBe('doing')
  })
})
