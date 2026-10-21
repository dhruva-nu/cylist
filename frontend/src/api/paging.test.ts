import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Task } from './client'
import { fetchAllPages, pagePath } from './paging'
import { fetchBoardCards, type BoardCards } from './queries'

/** Answer each request with the next of `bodies`, recording what was asked. */
function serve(...bodies: { status?: number; body: unknown }[]) {
  const asked: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string) => {
      asked.push(url)
      const next = bodies.shift()
      if (!next) return Promise.reject(new Error(`Unexpected request: ${url}`))
      return Promise.resolve(
        new Response(JSON.stringify(next.body), {
          status: next.status ?? 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    }),
  )
  return asked
}

const card = (id: string, columnId: string) => ({ id, column_id: columnId }) as Task

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('pagePath', () => {
  it('sets the limit and the cursor', () => {
    expect(pagePath('/people', 'abc', 10)).toBe('/people?limit=10&after=abc')
  })

  it('keeps a query the path already had', () => {
    expect(pagePath('/projects/ATL/goals?open_only=true', null)).toBe(
      '/projects/ATL/goals?open_only=true&limit=100',
    )
  })
})

describe('fetchAllPages', () => {
  it('follows each page’s cursor to the end', async () => {
    const asked = serve(
      { body: { items: [1, 2], next: 'two' } },
      { body: { items: [3], next: null } },
    )
    await expect(fetchAllPages<number>('/people')).resolves.toEqual([1, 2, 3])
    expect(asked[1]).toContain('after=two')
  })
})

describe('fetchBoardCards', () => {
  const firstPages = {
    columns: [
      { column_id: 'todo', total: 3, items: [card('a', 'todo')], next: 'a' },
      { column_id: 'done', total: 0, items: [], next: null },
    ],
  }

  it('reads the top of every column, and remembers how far', async () => {
    serve({ body: firstPages })
    const read = await fetchBoardCards('ATL')
    expect(read.tasks.map((task) => task.id)).toEqual(['a'])
    expect(read.columns[0]).toEqual({ column_id: 'todo', total: 3, next: 'a', pages: 1 })
  })

  it('reads a column again as deep as the reader had taken it', async () => {
    const previous: BoardCards = {
      tasks: [],
      columns: [{ column_id: 'todo', total: 3, next: 'b', pages: 3 }],
    }
    const asked = serve(
      { body: firstPages },
      { body: { items: [card('b', 'todo')], next: 'b' } },
      { body: { items: [card('c', 'todo')], next: null } },
    )
    const read = await fetchBoardCards('ATL', previous)
    expect(read.tasks.map((task) => task.id)).toEqual(['a', 'b', 'c'])
    expect(read.columns[0]?.pages).toBe(3)
    expect(asked[1]).toContain('/columns/todo/tasks')
  })

  it('stops a column where it got to when its cursor has gone', async () => {
    const previous: BoardCards = {
      tasks: [],
      columns: [{ column_id: 'todo', total: 3, next: 'b', pages: 2 }],
    }
    serve(
      { body: firstPages },
      { status: 422, body: { error: { code: 'unprocessable', message: 'gone' } } },
    )
    const read = await fetchBoardCards('ATL', previous)
    expect(read.tasks.map((task) => task.id)).toEqual(['a'])
    expect(read.columns[0]).toMatchObject({ next: 'a', pages: 1 })
  })
})
