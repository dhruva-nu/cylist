import { describe, expect, it } from 'vitest'

import type { Task } from '../../api/client'
import { sortKeyAt } from './sortKey'

/** Only the fields a sort key is read from; the rest of a card is noise here. */
function card(id: string, columnId: string, position: number): Task {
  return { id, column_id: columnId, position } as Task
}

const COLUMN = [card('a', 'todo', 65536), card('b', 'todo', 131072), card('c', 'todo', 196608)]

function order(tasks: Task[], movingId: string, key: number): string[] {
  return tasks
    .map((task) => (task.id === movingId ? { ...task, position: key } : task))
    .sort((left, right) => (left.position ?? 0) - (right.position ?? 0))
    .map((task) => task.id)
}

describe('sortKeyAt', () => {
  it('puts a card dropped at the bottom below every key there, however large', () => {
    const tasks = [...COLUMN, card('x', 'done', 0)]

    const key = sortKeyAt(tasks, 'todo', 3, 'x')

    expect(order(tasks, 'x', key).slice(0, 4)).toEqual(['a', 'b', 'c', 'x'])
  })

  it('clamps an index past the bottom to the bottom', () => {
    expect(sortKeyAt(COLUMN, 'todo', 99, 'x')).toBeGreaterThan(196608)
  })

  it('puts a card at the top above the first key', () => {
    expect(sortKeyAt(COLUMN, 'todo', 0, 'x')).toBeLessThan(65536)
  })

  it('puts a card between its two neighbours', () => {
    const key = sortKeyAt(COLUMN, 'todo', 1, 'x')

    expect(key).toBeGreaterThan(65536)
    expect(key).toBeLessThan(131072)
  })

  it('does not count the card being moved as its own neighbour', () => {
    // Moving `a` down one: past `b`, above `c`.
    const key = sortKeyAt(COLUMN, 'todo', 1, 'a')

    expect(order(COLUMN, 'a', key)).toEqual(['b', 'a', 'c'])
  })

  it('starts an empty column anywhere', () => {
    expect(sortKeyAt(COLUMN, 'done', 0, 'x')).toBe(0)
  })
})
