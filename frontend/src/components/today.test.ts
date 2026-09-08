/**
 * What the sidebar calls today's work.
 *
 * This is the half of the feature that can be wrong quietly. A card left out
 * of the list looks exactly like a card that is not due, and nobody goes
 * looking for work they have not been shown — so every rule about membership
 * is pinned here, and so is the order, because a list whose first row is not
 * the thing to do next is a list you stop trusting.
 */

import { describe, expect, it } from 'vitest'

import type { BoardColumn, Person, Task, TaskPriority, TaskStatus } from '../api/client'
import { todaysWork, todaysWorkCount } from './today'

const TODAY = '2026-09-08'

function person(id: string, name: string, isMe = false): Person {
  return {
    id,
    name,
    kind: 'team',
    role: '',
    responsibilities: '',
    email: null,
    colour: '#1D7D46',
    archived_at: null,
    created_at: '2026-01-01T00:00:00Z',
    is_me: isMe,
  }
}

const ME = person('me', 'Dhruva', true)
const ADITI = person('aditi', 'Aditi K')

function column(id: string, name: string, position: number): BoardColumn {
  // No outcomes: none of these tests turn on how the last column is divided,
  // and empty is the ordinary case for every column anyway.
  return {
    id,
    project_id: 'project-1',
    name,
    description: '',
    position,
    outcomes: [],
    task_count: 0,
  }
}

const TO_DO = column('col-todo', 'To do', 0)
const DOING = column('col-doing', 'In progress', 1)
const DONE = column('col-done', 'Done', 2)
const BOARD = [TO_DO, DOING, DONE]

let counter = 0

function card(
  reference: string,
  fields: {
    due?: string | null
    priority?: TaskPriority
    status?: TaskStatus
    column?: string
    assignee?: Person
  } = {},
): Task {
  counter += 1
  return {
    id: reference,
    project_id: 'project-1',
    reference,
    number: counter,
    parent_id: null,
    parent_reference: null,
    sub_number: null,
    column_id: fields.column ?? TO_DO.id,
    position: 0,
    title: reference,
    description: '',
    type: 'feature',
    priority: fields.priority ?? 'p3',
    sub_statuses: [],
    sub_status_index: null,
    due_date: fields.due === undefined ? TODAY : fields.due,
    // No per-column dates on these fixtures, and with none the date a card is
    // working towards is its final one. Keeping the two in step is what makes
    // `due` still mean, in a test, what it has always meant here.
    column_due_dates: [],
    next_due_date: fields.due === undefined ? TODAY : fields.due,
    outcome: null,
    outcome_index: null,
    assignee: fields.assignee ?? ME,
    status: fields.status ?? 'active',
    template_id: null,
    template_name: null,
    goal_id: null,
    goal_reference: null,
    goal_name: null,
    goal_colour: null,
    jira_ref: null,
    pr_ref: null,
    waiting_on: [],
    comment_count: 0,
    checklist: [],
    finished_at: null,
    open_subtask_count: 0,
    subtask_count: 0,
    subtask_assignees: [],
    created_at: '2026-01-01T00:00:00Z',
  }
}

/** The references in each group, which is all these assertions are about. */
function refs(tasks: Task[]): string[] {
  return tasks.map((task) => task.reference)
}

describe('which cards are today’s work', () => {
  it('takes what is due today and what is already late, and nothing later', () => {
    const work = todaysWork(
      [
        card('A-1', { due: TODAY }),
        card('A-2', { due: '2026-09-07' }),
        card('A-3', { due: '2026-09-09' }),
        card('A-4', { due: '2026-10-01' }),
      ],
      BOARD,
      ME,
      TODAY,
    )

    expect(refs(work.overdue)).toEqual(['A-2'])
    expect(refs(work.due)).toEqual(['A-1'])
  })

  it('leaves out a card with no due date, however urgent it is', () => {
    // Membership is the due date's question alone. An urgent card with no date
    // is work to schedule, not work for today, and putting it here would mean
    // the list never emptied.
    const work = todaysWork([card('A-1', { due: null, priority: 'p0' })], BOARD, ME, TODAY)

    expect(todaysWorkCount(work)).toBe(0)
  })

  it('leaves out a card in the board’s last column, which is what finished means', () => {
    const work = todaysWork(
      [card('A-1', { column: DONE.id }), card('A-2', { column: DOING.id })],
      BOARD,
      ME,
      TODAY,
    )

    expect(refs(work.due)).toEqual(['A-2'])
  })

  it('leaves out a cancelled card, which is work that is not going to happen', () => {
    const work = todaysWork([card('A-1', { status: 'cancelled' })], BOARD, ME, TODAY)

    expect(todaysWorkCount(work)).toBe(0)
  })

  it('keeps a blocked or on-hold card, because that is the part worth knowing', () => {
    const work = todaysWork(
      [
        card('A-1', { status: 'blocked', due: '2026-09-01' }),
        card('A-2', { status: 'hold' }),
        card('A-3'),
      ],
      BOARD,
      ME,
      TODAY,
    )

    expect(refs(work.overdue)).toEqual(['A-1'])
    expect(refs(work.due)).toEqual(['A-2', 'A-3'])
  })

  it('narrows to your own cards when the directory knows who you are', () => {
    const work = todaysWork(
      [card('A-1', { assignee: ME }), card('A-2', { assignee: ADITI })],
      BOARD,
      ME,
      TODAY,
    )

    expect(refs(work.due)).toEqual(['A-1'])
  })

  it('falls back to the whole board when nobody is marked as you', () => {
    // An empty panel that cannot say why it is empty is worse than a wider
    // answer, so an unclaimed directory gets the project's day rather than
    // nothing at all.
    const work = todaysWork(
      [card('A-1', { assignee: ME }), card('A-2', { assignee: ADITI })],
      BOARD,
      null,
      TODAY,
    )

    expect(refs(work.due)).toEqual(['A-1', 'A-2'])
  })

  it('treats a board with no columns as a board with no done column', () => {
    const work = todaysWork([card('A-1')], [], ME, TODAY)

    expect(refs(work.due)).toEqual(['A-1'])
  })
})

describe('what order today’s work is in', () => {
  it('puts the most urgent of today’s cards first', () => {
    const work = todaysWork(
      [
        card('A-1', { priority: 'p3' }),
        card('A-2', { priority: 'p0' }),
        card('A-3', { priority: 'p2' }),
        card('A-4', { priority: 'p1' }),
      ],
      BOARD,
      ME,
      TODAY,
    )

    expect(refs(work.due)).toEqual(['A-2', 'A-4', 'A-3', 'A-1'])
  })

  it('puts the longest-overdue card first, whatever its priority', () => {
    // How long a card has been late outranks priority in this group: three
    // weeks over is a conversation to have, and a P3 card that old is more
    // pressing than a P0 that slipped yesterday.
    const work = todaysWork(
      [
        card('A-1', { due: '2026-09-07', priority: 'p0' }),
        card('A-2', { due: '2026-08-18', priority: 'p3' }),
        card('A-3', { due: '2026-09-02', priority: 'p1' }),
      ],
      BOARD,
      ME,
      TODAY,
    )

    expect(refs(work.overdue)).toEqual(['A-2', 'A-3', 'A-1'])
  })

  it('breaks a shared due date with priority', () => {
    const work = todaysWork(
      [
        card('A-1', { due: '2026-09-05', priority: 'p2' }),
        card('A-2', { due: '2026-09-05', priority: 'p0' }),
      ],
      BOARD,
      ME,
      TODAY,
    )

    expect(refs(work.overdue)).toEqual(['A-2', 'A-1'])
  })

  it('is stable across calls when nothing distinguishes two cards', () => {
    // Same priority, same date. Without the tie-break on the card's number the
    // two could swap places between renders, which reads as the list changing
    // when the work has not.
    const cards = [card('A-1'), card('A-2'), card('A-3')]
    const once = refs(todaysWork(cards, BOARD, ME, TODAY).due)
    const twice = refs(todaysWork([...cards].reverse(), BOARD, ME, TODAY).due)

    expect(once).toEqual(twice)
  })

  it('does not reorder the array it was given', () => {
    // `sort` is in place, and these cards are the board's own query cache.
    const cards = [card('A-1', { priority: 'p3' }), card('A-2', { priority: 'p0' })]
    const order = refs(cards)

    todaysWork(cards, BOARD, ME, TODAY)

    expect(refs(cards)).toEqual(order)
  })
})
