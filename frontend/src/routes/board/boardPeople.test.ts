/**
 * The people filter (CYLIST-99): whose cards a card counts as, what a board
 * with no `?who=` opens on, and how the filter sits among the others.
 */

import { describe, expect, it } from 'vitest'

import type { BoardColumn, Person, Task, TaskStatus } from '../../api/client'
import {
  cardsOwned,
  EVERYONE,
  facesInOrder,
  ownedByAny,
  peopleFromSearch,
  searchFromPeople,
} from './boardPeople'
import { narrowBoard, type BoardFilters } from './narrowBoard'

function person(id: string, name: string): Person {
  return {
    id,
    name,
    kind: 'team',
    title: '',
    responsibilities: '',
    email: null,
    colour: '#1D7D46',
    archived_at: null,
    created_at: '2026-01-01T00:00:00Z',
    is_agent: false,
    has_account: true,
    invite_is_pending: false,
  }
}

const ADITI = person('aditi', 'Aditi K')
const RAVI = person('ravi', 'Ravi S')
const MEERA = person('meera', 'Meera P')
const MEMBERS = [ADITI, RAVI, MEERA]

const COLUMN: BoardColumn = {
  id: 'column-1',
  name: 'To do',
} as BoardColumn

function task(
  title: string,
  owner: Person,
  { subtaskOwners = [], status = 'active' }: { subtaskOwners?: Person[]; status?: TaskStatus } = {},
): Task {
  return {
    id: title,
    project_id: 'project-1',
    reference: 'ATL-1',
    number: 1,
    parent_id: null,
    parent_reference: null,
    sub_number: null,
    column_id: COLUMN.id,
    position: 0,
    title,
    description: '',
    type: 'feature',
    priority: 'p3',
    sub_statuses: [],
    sub_status_index: null,
    due_date: null,
    column_due_dates: [],
    next_due_date: null,
    assignee: owner,
    status,
    template_id: null,
    template_name: null,
    goal_id: null,
    goal_reference: null,
    goal_name: null,
    goal_colour: null,
    jira_ref: null,
    pr_refs: [],
    waiting_on: [],
    comment_count: 0,
    checklist: [],
    outcome: null,
    outcome_index: null,
    finished_at: null,
    archived_at: null,
    open_subtask_count: 0,
    subtask_count: subtaskOwners.length,
    subtask_assignees: subtaskOwners,
    agent_session: null,
    created_at: '2026-01-01T00:00:00Z',
  }
}

const FILTERS: BoardFilters = {
  search: '',
  goal: 'all',
  status: 'all',
  agent: 'all',
  people: [],
  showCancelled: false,
}

const titles = (tasks: Task[]) => tasks.map((card) => card.title)

describe('whose a card is', () => {
  it('is its owner’s, and whoever owns one of its sub-tasks', () => {
    const card = task('login', ADITI, { subtaskOwners: [RAVI] })
    expect(ownedByAny(card, ['aditi'])).toBe(true)
    expect(ownedByAny(card, ['ravi'])).toBe(true)
    expect(ownedByAny(card, ['meera'])).toBe(false)
  })

  it('is anybody’s when nobody is picked', () => {
    expect(ownedByAny(task('login', ADITI), [])).toBe(true)
  })

  it('counts a card once per person on it, however many sub-tasks they own', () => {
    const owned = cardsOwned([
      task('login', ADITI, { subtaskOwners: [RAVI, RAVI, ADITI] }),
      task('signup', RAVI),
    ])
    expect(owned.get('aditi')).toBe(1)
    expect(owned.get('ravi')).toBe(2)
    expect(owned.has('meera')).toBe(false)
  })
})

describe('what ?who= opens on', () => {
  it('opens on you, when you are on the project', () => {
    expect(peopleFromSearch(undefined, 'ravi', MEMBERS)).toEqual(['ravi'])
  })

  it('opens on everyone, when you are not', () => {
    expect(peopleFromSearch(undefined, 'outsider', MEMBERS)).toEqual([])
    expect(peopleFromSearch(undefined, null, MEMBERS)).toEqual([])
  })

  it('reads a shared pick as given, whoever is reading it', () => {
    expect(peopleFromSearch('aditi,meera', 'ravi', MEMBERS)).toEqual(['aditi', 'meera'])
    expect(peopleFromSearch(EVERYONE, 'ravi', MEMBERS)).toEqual([])
  })

  it('opens on the pick last made here, over you', () => {
    expect(peopleFromSearch(undefined, 'ravi', MEMBERS, 'aditi,meera')).toEqual(['aditi', 'meera'])
    expect(peopleFromSearch(undefined, 'ravi', MEMBERS, EVERYONE)).toEqual([])
  })

  it('drops whoever has left from the remembered pick, and opens on you if nobody is left', () => {
    expect(peopleFromSearch(undefined, 'ravi', MEMBERS, 'aditi,gone')).toEqual(['aditi'])
    expect(peopleFromSearch(undefined, 'ravi', MEMBERS, 'gone')).toEqual(['ravi'])
  })

  it('lets a shared link win over the remembered pick', () => {
    expect(peopleFromSearch('meera', 'ravi', MEMBERS, 'aditi')).toEqual(['meera'])
    expect(peopleFromSearch(EVERYONE, 'ravi', MEMBERS, 'aditi')).toEqual([])
  })

  it('writes back what it reads', () => {
    for (const pick of [[], ['aditi'], ['aditi', 'meera']]) {
      expect(peopleFromSearch(searchFromPeople(pick), 'ravi', MEMBERS)).toEqual(pick)
      expect(peopleFromSearch(undefined, 'ravi', MEMBERS, searchFromPeople(pick))).toEqual(pick)
    }
  })
})

describe('the faces', () => {
  it('put you first, then whoever owns most, then by name', () => {
    const owned = cardsOwned([task('a', MEERA), task('b', MEERA), task('c', ADITI)])
    const order = facesInOrder(MEMBERS, owned, 'ravi').map((face) => face.id)
    expect(order).toEqual(['ravi', 'meera', 'aditi'])
  })
})

describe('the people filter on the board', () => {
  const cards = [
    task('aditi’s', ADITI),
    task('ravi’s', RAVI),
    task('meera’s, ravi helps', MEERA, { subtaskOwners: [RAVI] }),
    task('aditi’s, cancelled', ADITI, { status: 'cancelled' }),
  ]
  const narrow = (filters: Partial<BoardFilters>) =>
    narrowBoard(cards, { ...FILTERS, ...filters }, [COLUMN], MEMBERS)

  it('shows the cards of anybody picked', () => {
    expect(titles(narrow({ people: ['ravi'] }).visibleTasks)).toEqual([
      'ravi’s',
      'meera’s, ravi helps',
    ])
    expect(titles(narrow({ people: ['aditi', 'meera'] }).visibleTasks)).toEqual([
      'aditi’s',
      'meera’s, ravi helps',
    ])
  })

  it('is ANDed with the search box, who: tags included', () => {
    expect(titles(narrow({ people: ['ravi'], search: 'helps' }).visibleTasks)).toEqual([
      'meera’s, ravi helps',
    ])
    expect(titles(narrow({ people: ['ravi'], search: 'who:aditi' }).visibleTasks)).toEqual([])
  })

  it('counts each person before it narrows, and after everything else', () => {
    const { peopleTally } = narrow({ people: ['aditi'], search: 'helps' })
    expect(peopleTally.get('ravi')).toBe(1)
    expect(peopleTally.get('meera')).toBe(1)
    expect(peopleTally.has('aditi')).toBe(false)
  })

  it('counts only the picked people’s cancelled cards as hidden', () => {
    expect(narrow({ people: ['ravi'] }).cancelledCount).toBe(0)
    expect(narrow({ people: ['aditi'] }).cancelledCount).toBe(1)
  })

  it('tallies the agent counts off the picked people’s cards', () => {
    // Only `state` is read by the tally; the rest of a presence is beside the point.
    const working = {
      ...task('ravi’s, agent on it', RAVI),
      agent_session: { state: 'working' },
    } as Task
    const tally = (people: string[]) =>
      narrowBoard([...cards, working], { ...FILTERS, people }, [COLUMN], MEMBERS).agentTally
    expect(tally(['ravi']).working).toBe(1)
    expect(tally(['aditi']).working).toBe(0)
  })
})
