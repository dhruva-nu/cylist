/**
 * The one button that closes a goal and the one that opens it again.
 *
 * What is worth checking without a browser is the rule it borrows from the
 * server — a goal cannot be achieved over open cards — and that it says so in
 * a sentence somebody can act on: the count, and what to do about it.
 */

import { describe, expect, it } from 'vitest'

import type { Goal, GoalProgress, GoalStatus, Person } from '../api/client'
import { goalClosure } from './goalClose'

const ADITI: Person = {
  id: 'person-1',
  name: 'Aditi K',
  kind: 'team',
  title: 'Backend engineer',
  responsibilities: '',
  email: null,
  colour: '#1D7D46',
  archived_at: null,
  created_at: '2026-01-01T00:00:00Z',
  is_agent: false,
  has_account: false,
  invite_is_pending: false,
}

const NOTHING: GoalProgress = { total: 0, done: 0, cancelled: 0, open: 0, blocked: 0, on_hold: 0 }

function goal(status: GoalStatus, progress: Partial<GoalProgress> = {}): Goal {
  return {
    id: 'goal-1',
    project_id: 'project-1',
    reference: 'ATL-G1',
    number: 1,
    name: 'Search revamp',
    description: '',
    colour: '#3B6FC2',
    status,
    target_date: null,
    achieved_at: status === 'achieved' ? '2026-03-01T00:00:00Z' : null,
    owner: ADITI,
    progress: { ...NOTHING, ...progress },
    created_at: '2026-01-01T00:00:00Z',
  }
}

describe('goalClosure', () => {
  it('offers to close a goal whose cards are all settled', () => {
    const closure = goalClosure(goal('open', { total: 4, done: 3, cancelled: 1 }))

    expect(closure.next).toBe('achieved')
    expect(closure.label).toBe('Mark achieved')
    expect(closure.refusal).toBeNull()
    expect(closure.announcement).toBe('Search revamp marked achieved.')
  })

  it('offers to close a goal that has no cards at all', () => {
    // Nothing is outstanding, so nothing holds it: the server refuses on open
    // cards, not on an empty goal.
    expect(goalClosure(goal('open')).refusal).toBeNull()
  })

  it('holds the button while cards are still open, and says how many', () => {
    const closure = goalClosure(goal('open', { total: 3, done: 1, open: 2, blocked: 1 }))

    expect(closure.next).toBe('achieved')
    expect(closure.refusal).toContain('2 cards are still open')
    expect(closure.refusal).toContain('each of them')
  })

  it('counts one open card in the singular', () => {
    const closure = goalClosure(goal('open', { total: 2, done: 1, open: 1 }))

    expect(closure.refusal).toContain('1 card is still open')
    expect(closure.refusal).not.toContain('each of them')
  })

  it('offers to reopen an achieved goal', () => {
    const closure = goalClosure(goal('achieved', { total: 2, done: 2 }))

    expect(closure.next).toBe('open')
    expect(closure.label).toBe('Reopen')
    expect(closure.refusal).toBeNull()
    expect(closure.announcement).toBe('Search revamp is open again.')
  })

  it('offers to reopen a dropped goal, open cards and all', () => {
    // Dropping is the one way a goal settles over unfinished work, so the way
    // back has to be open to it too.
    const closure = goalClosure(goal('dropped', { total: 5, done: 1, open: 4 }))

    expect(closure.next).toBe('open')
    expect(closure.label).toBe('Reopen')
    expect(closure.refusal).toBeNull()
  })
})
