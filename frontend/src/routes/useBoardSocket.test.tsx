// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { queryKeys } from '../api/queries'
import { FakeWebSocket } from '../test/dom'
import { keptCurrentBySocket, useBoardSocket, useProjectSocket } from './useBoardSocket'

import '../test/dom'

let client: QueryClient
let invalidated: unknown[][]

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

/** Hold a project's socket, and read its state the way the board does. */
function holdSocket(projectKey: string | null) {
  return renderHook(
    ({ key }: { key: string | null }) => {
      useProjectSocket(key)
      return useBoardSocket(key ?? '')
    },
    { wrapper, initialProps: { key: projectKey } },
  )
}

beforeEach(() => {
  vi.useFakeTimers()
  FakeWebSocket.install()
  client = new QueryClient()
  invalidated = []
  vi.spyOn(client, 'invalidateQueries').mockImplementation((filters) => {
    invalidated.push([...(filters?.queryKey ?? [])])
    return Promise.resolve()
  })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('useProjectSocket', () => {
  it('opens the project’s socket and is not connected until the server says ready', () => {
    const { result } = holdSocket('ATL')

    expect(FakeWebSocket.latest.url).toContain('/projects/ATL/board/ws')
    expect(result.current.connected).toBe(false)

    act(() => FakeWebSocket.latest.receive({ type: 'ready' }))

    expect(result.current.connected).toBe(true)
    expect(keptCurrentBySocket(queryKeys.tasks('ATL'))).toBe(true)
  })

  it('holds no socket off a project', () => {
    holdSocket(null)

    expect(FakeWebSocket.instances).toHaveLength(0)
  })

  it('reads everything it watches once, when it first connects', () => {
    holdSocket('ATL')

    act(() => FakeWebSocket.latest.receive({ type: 'ready' }))
    act(() => void vi.advanceTimersByTime(300))

    expect(invalidated).toContainEqual(queryKeys.tasks('ATL'))
    expect(invalidated).toContainEqual(queryKeys.board('ATL'))
  })

  it('refetches only what a change names, once for a burst', () => {
    holdSocket('ATL')
    act(() => FakeWebSocket.latest.receive({ type: 'ready' }))
    act(() => void vi.advanceTimersByTime(300))
    invalidated.length = 0

    const change = (entity_type: string, entity_id: string) => ({
      type: 'board.changed',
      at: 'now',
      changed: [{ entity_type, entity_id }],
    })
    act(() => {
      FakeWebSocket.latest.receive(change('hook', 'h1'))
      FakeWebSocket.latest.receive(change('hook', 'h2'))
    })
    act(() => void vi.advanceTimersByTime(300))

    expect(invalidated).toContainEqual(queryKeys.allHooks)
    expect(invalidated).not.toContainEqual(queryKeys.tasks('ATL'))
    expect(invalidated.filter((key) => key[0] === 'hooks')).toHaveLength(1)
  })

  it('refetches one card’s feed when its doorbell rings', () => {
    holdSocket('ATL')
    act(() => FakeWebSocket.latest.receive({ type: 'ready' }))
    invalidated.length = 0

    act(() => FakeWebSocket.latest.receive({ type: 'agent.feed', task_id: 't-1', at: 'now' }))

    expect(invalidated).toEqual([queryKeys.agentFeed('t-1')])
  })

  it('reconnects after a drop, backing off, and goes back to polling meanwhile', () => {
    const { result } = holdSocket('ATL')
    act(() => FakeWebSocket.latest.receive({ type: 'ready' }))

    act(() => FakeWebSocket.latest.drop(1006))

    expect(result.current.connected).toBe(false)
    expect(keptCurrentBySocket(queryKeys.tasks('ATL'))).toBe(false)
    expect(FakeWebSocket.instances).toHaveLength(1)

    act(() => void vi.advanceTimersByTime(1000))

    expect(FakeWebSocket.instances).toHaveLength(2)
  })

  it('stops for good when the session is refused', () => {
    const { result } = holdSocket('ATL')

    act(() => FakeWebSocket.latest.drop(4401))
    act(() => void vi.advanceTimersByTime(60_000))

    expect(result.current.signedOut).toBe(true)
    expect(FakeWebSocket.instances).toHaveLength(1)
  })

  it('closes the socket when the page leaves the project, and opens the next project’s', () => {
    const { rerender } = holdSocket('ATL')
    const first = FakeWebSocket.latest

    rerender({ key: 'HRM' })

    expect(first.closed).toBe(true)
    expect(FakeWebSocket.latest.url).toContain('/projects/HRM/board/ws')
    expect(keptCurrentBySocket(queryKeys.tasks('ATL'))).toBe(false)
  })
})
