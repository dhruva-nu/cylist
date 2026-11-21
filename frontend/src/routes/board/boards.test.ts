/** A board per template: which boards there are, and which cards and columns each one gets. */

import { describe, expect, it } from 'vitest'

import type { BoardColumn, Task, Template } from '../../api/client'
import {
  boardsFor,
  boardSlug,
  cardsOnBoard,
  GENERAL_BOARD,
  pickBoard,
  summariseBoard,
} from './boards'

const column = (id: string, name: string) => ({ id, name }) as BoardColumn
const COLUMNS = [
  column('c1', 'Backlog'),
  column('c2', 'Build'),
  column('c3', 'Review'),
  column('c4', 'Done'),
]

const template = (id: string, name: string, allowed: string[]) =>
  ({ id, name, allowed_column_ids: allowed, stages: [] }) as unknown as Template

const card = (id: string, extra: Partial<Task> = {}) =>
  ({
    id,
    template_id: null,
    column_id: 'c1',
    status: 'active',
    next_due_date: null,
    ...extra,
  }) as Task

describe('boardsFor', () => {
  it('puts General first, with every column', () => {
    const [general] = boardsFor([], COLUMNS)
    expect(general?.key).toBe(GENERAL_BOARD)
    expect(general?.columns).toBe(COLUMNS)
  })

  it("gives a template its stages' columns, in the project's order", () => {
    // Stages listed out of order still draw the way the project does.
    const boards = boardsFor([template('t1', 'Bug', ['c3', 'c1', 'c4'])], COLUMNS)
    expect(boards[1]?.columns.map((one) => one.name)).toEqual(['Backlog', 'Review', 'Done'])
  })

  it('gives a template with no stages every column', () => {
    const boards = boardsFor([template('t1', 'Chore', [])], COLUMNS)
    expect(boards[1]?.columns).toBe(COLUMNS)
  })

  it('keeps every key distinct', () => {
    const boards = boardsFor(
      [template('t1', 'Bug', []), template('t2', 'Bug!', []), template('t3', 'General', [])],
      COLUMNS,
    )
    expect(boards.map((board) => board.key)).toEqual(['general', 'bug', 'bug-2', 'general-2'])
  })
})

describe('boardSlug', () => {
  it('lowercases and hyphenates', () => {
    expect(boardSlug('Design task')).toBe('design-task')
    expect(boardSlug('  QA / Release ')).toBe('qa-release')
    expect(boardSlug('!!!')).toBe('board')
  })
})

describe('cardsOnBoard', () => {
  const boards = boardsFor([template('t1', 'Bug', [])], COLUMNS)
  const tasks = [card('a'), card('b', { template_id: 't1' }), card('c', { template_id: 'gone' })]

  it("puts a template's cards on its board", () => {
    expect(cardsOnBoard(tasks, boards[1]!, boards).map((task) => task.id)).toEqual(['b'])
  })

  it('puts cards with no template, or an unknown one, on General', () => {
    expect(cardsOnBoard(tasks, boards[0]!, boards).map((task) => task.id)).toEqual(['a', 'c'])
  })
})

describe('pickBoard', () => {
  const boards = boardsFor([template('t1', 'Bug', [])], COLUMNS)

  it('takes the first key that names a board', () => {
    expect(pickBoard(boards, 'nope', 'bug').key).toBe('bug')
  })

  it('falls back to General', () => {
    expect(pickBoard(boards, undefined, null).key).toBe(GENERAL_BOARD)
  })
})

describe('summariseBoard', () => {
  const [board] = boardsFor([], COLUMNS)

  it('counts each column, and the open cards outside the last', () => {
    const cards = [
      card('a', { column_id: 'c1' }),
      card('b', { column_id: 'c1' }),
      card('c', { column_id: 'c3' }),
      card('d', { column_id: 'c4' }),
      card('e', { column_id: 'c2', status: 'cancelled' }),
    ]
    const summary = summariseBoard(board!, cards, '2026-11-21')
    expect(summary.perColumn).toEqual([2, 1, 1, 1])
    expect(summary.open).toBe(3)
    expect(summary.attention).toBeNull()
    expect(summary.flow).toBe('Backlog › Build › Review › Done')
  })

  it('wants a look when an open card is overdue or blocked', () => {
    const cards = [
      card('a', { next_due_date: '2026-11-20' }),
      card('b', { status: 'blocked' }),
      // Finished, so not overdue whatever its date says.
      card('c', { column_id: 'c4', next_due_date: '2026-01-01' }),
    ]
    expect(summariseBoard(board!, cards, '2026-11-21').attention).toBe('1 overdue, 1 blocked')
  })
})
