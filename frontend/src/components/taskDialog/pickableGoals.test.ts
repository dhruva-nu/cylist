/**
 * Which goals the task form's picker offers.
 *
 * A goal that is achieved or dropped is done being worked towards, so a new
 * card is not offered it — but a card already written under one still shows
 * it, so opening and saving that card does not quietly take it off.
 */

import { describe, expect, it } from 'vitest'

import type { Goal, GoalStatus, Person } from '../../api/client'
import { pickableGoals } from './taskForm'

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

function goal(number: number, status: GoalStatus): Goal {
  return {
    id: `goal-${number}`,
    project_id: 'project-1',
    reference: `ATL-G${number}`,
    number,
    name: `Goal ${number}`,
    description: '',
    colour: '#3B6FC2',
    status,
    target_date: null,
    achieved_at: status === 'achieved' ? '2026-03-01T00:00:00Z' : null,
    owner: ADITI,
    progress: { total: 0, done: 0, cancelled: 0, open: 0, blocked: 0, on_hold: 0 },
    created_at: '2026-01-01T00:00:00Z',
  }
}

const OPEN = goal(1, 'open')
const ACHIEVED = goal(2, 'achieved')
const DROPPED = goal(3, 'dropped')
const ALL = [OPEN, ACHIEVED, DROPPED]

describe('pickableGoals', () => {
  it('offers a new card only the open goals', () => {
    expect(pickableGoals(ALL, null)).toEqual([OPEN])
  })

  it('keeps the achieved goal a card is already on', () => {
    expect(pickableGoals(ALL, ACHIEVED.id)).toEqual([OPEN, ACHIEVED])
  })

  it('keeps the dropped goal a card is already on', () => {
    expect(pickableGoals(ALL, DROPPED.id)).toEqual([OPEN, DROPPED])
  })

  it('adds nothing for a card on an open goal', () => {
    expect(pickableGoals(ALL, OPEN.id)).toEqual([OPEN])
  })
})
