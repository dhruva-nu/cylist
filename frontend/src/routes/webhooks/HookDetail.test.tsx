// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type * as ClientModule from '../../api/client'
import { api, type Hook, type HookDelivery } from '../../api/client'
import { HookDetail } from './HookDetail'

import '../../test/dom'

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof ClientModule>()
  return { ...actual, api: { ...actual.api, listHookDeliveries: vi.fn() } }
})

const HOOK = {
  id: 'hook-1',
  name: 'Deploy',
  enabled: true,
  manual: false,
  url: 'https://ci.example.com/cylist',
  verbs: [],
  secret_hint: 'abcd',
} as unknown as Hook

/** Deliveries `delivery-n`, newest first: `delivery-12` is the latest. */
const delivery = (n: number) =>
  ({
    id: `delivery-${n}`,
    hook_id: HOOK.id,
    event: `event.${n}`,
    state: 'delivered',
    attempt_count: 1,
    attempts: [],
    payload: {},
    created_at: '2026-10-06T10:00:00Z',
  }) as unknown as HookDelivery
const LOG = Array.from({ length: 12 }, (_, i) => delivery(12 - i))

/** The server's paging: newest first, `limit` at a time after the cursor. */
function serve(_project: string, _hook: string, after: string | null, limit: number) {
  const start = after === null ? 0 : LOG.findIndex((entry) => entry.id === after) + 1
  const items = LOG.slice(start, start + limit)
  const more = start + limit < LOG.length
  return Promise.resolve({ items, next: more ? (items.at(-1)?.id ?? null) : null })
}

let client: QueryClient

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  vi.mocked(api.listHookDeliveries).mockImplementation(serve)
})

afterEach(() => {
  client.clear()
  vi.mocked(api.listHookDeliveries).mockReset()
})

function open() {
  render(
    <QueryClientProvider client={client}>
      <HookDetail
        projectKey="ATL"
        hook={HOOK}
        events={[]}
        onEdit={() => undefined}
        onSecret={() => undefined}
        onChanged={() => Promise.resolve()}
      />
    </QueryClientProvider>,
  )
}

const shown = () => screen.queryAllByText(/^event\.\d+$/).map((node) => node.textContent)

describe('the delivery log', () => {
  it('opens on the latest five and fetches older ones only when asked', async () => {
    open()
    await waitFor(() => expect(shown()).toHaveLength(5))
    expect(shown()).toEqual(['event.12', 'event.11', 'event.10', 'event.9', 'event.8'])
    expect(api.listHookDeliveries).toHaveBeenCalledTimes(1)
    expect(api.listHookDeliveries).toHaveBeenCalledWith('ATL', 'hook-1', null, 5, expect.anything())

    await userEvent.click(screen.getByRole('button', { name: 'Show older deliveries' }))
    await waitFor(() => expect(shown()).toHaveLength(12))
    expect(api.listHookDeliveries).toHaveBeenLastCalledWith(
      'ATL',
      'hook-1',
      'delivery-8',
      20,
      expect.anything(),
    )
    expect(screen.queryByRole('button', { name: 'Show older deliveries' })).toBeNull()
  })

  it('goes back to the latest five', async () => {
    open()
    await waitFor(() => expect(shown()).toHaveLength(5))
    expect(screen.queryByRole('button', { name: /Show only the latest/ })).toBeNull()

    await userEvent.click(screen.getByRole('button', { name: 'Show older deliveries' }))
    await waitFor(() => expect(shown()).toHaveLength(12))
    await userEvent.click(screen.getByRole('button', { name: 'Show only the latest 5' }))

    expect(shown()).toEqual(['event.12', 'event.11', 'event.10', 'event.9', 'event.8'])
    expect(screen.getByRole('button', { name: 'Show older deliveries' })).toBeTruthy()
  })

  it('offers nothing more when the log fits in five', async () => {
    vi.mocked(api.listHookDeliveries).mockResolvedValue({ items: LOG.slice(0, 3), next: null })
    open()
    await waitFor(() => expect(shown()).toHaveLength(3))
    expect(screen.queryByRole('button', { name: 'Show older deliveries' })).toBeNull()
  })
})
