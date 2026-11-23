import { beforeEach, describe, expect, it, vi } from 'vitest'
import type * as ClientModule from '../../api/client'
import { api, type Task } from '../../api/client'
import { runBulk, summariseBulk } from './bulkActions'

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof ClientModule>()
  return {
    ...actual,
    api: { ...actual.api, moveTask: vi.fn(), setTaskStatus: vi.fn(), deleteTask: vi.fn() },
  }
})

const card = (id: string, column_id = 'todo', status = 'active') =>
  ({ id, reference: `ATL-${id}`, column_id, status }) as unknown as Task

beforeEach(() => {
  vi.mocked(api.moveTask).mockReset()
  vi.mocked(api.setTaskStatus).mockReset()
  vi.mocked(api.deleteTask).mockReset()
})

describe('runBulk', () => {
  it('moves cards in the order given, each below the one before', async () => {
    const result = await runBulk([card('1'), card('2')], { kind: 'move', columnId: 'doing' })
    expect(api.moveTask).toHaveBeenNthCalledWith(1, 'ATL-1', 'doing', 0)
    expect(api.moveTask).toHaveBeenNthCalledWith(2, 'ATL-2', 'doing', 1)
    expect(result).toEqual({ done: 2, failed: [] })
  })

  it('leaves out a card already where it is going', async () => {
    const result = await runBulk([card('1', 'doing'), card('2')], {
      kind: 'move',
      columnId: 'doing',
    })
    expect(api.moveTask).toHaveBeenCalledTimes(1)
    expect(result.done).toBe(2)
  })

  it('sends the reason with a hold, and skips cards already on hold', async () => {
    await runBulk([card('1'), card('2', 'todo', 'hold')], {
      kind: 'status',
      status: 'hold',
      reason: 'waiting on design',
    })
    expect(api.setTaskStatus).toHaveBeenCalledOnce()
    expect(api.setTaskStatus).toHaveBeenCalledWith('ATL-1', {
      status: 'hold',
      reason: 'waiting on design',
    })
  })

  it('keeps going past a card the server refuses, and names it', async () => {
    vi.mocked(api.deleteTask)
      .mockRejectedValueOnce(new Error('nope'))
      .mockResolvedValue({ ok: true })
    const result = await runBulk([card('1'), card('2')], { kind: 'delete' })
    expect(result).toEqual({ done: 1, failed: [{ reference: 'ATL-1', message: 'nope' }] })
    expect(summariseBulk({ kind: 'delete' }, result)).toBe(
      '1 card deleted. 1 card refused — ATL-1: nope',
    )
  })
})
