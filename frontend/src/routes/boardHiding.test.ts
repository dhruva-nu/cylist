/**
 * What the board hides without being asked.
 *
 * The dangerous half of the feature: a card the board has put away looks
 * exactly like a card that is not there, and nobody goes looking for what they
 * have not been shown. So what is pinned here is mostly the other direction —
 * every way a hide is made to stand down, because a filter that answers "show
 * me the cancelled ones" with an empty board is the failure worth a test.
 */

import { describe, expect, it } from 'vitest'

import type { Person, Task, TaskStatus } from '../api/client'
import { parseQuery, tokenize } from './boardSearch'
import { hidesCancelled, hidesHold, isCancelled, isOnHold, splitOnHold } from './boardHiding'

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

function task(title: string, status: TaskStatus): Task {
  return {
    id: title,
    project_id: 'project-1',
    reference: 'ATL-1',
    number: 1,
    parent_id: null,
    parent_reference: null,
    sub_number: null,
    column_id: 'column-1',
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
    assignee: ADITI,
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
    open_subtask_count: 0,
    subtask_count: 0,
    subtask_assignees: [],
    agent_session: null,
    created_at: '2026-01-01T00:00:00Z',
  }
}

const parsed = (query: string) => parseQuery(tokenize(query))

describe('which cards each hide is about', () => {
  it('counts the cancelled ones, and nothing else', () => {
    expect(isCancelled(task('a', 'cancelled'))).toBe(true)
    expect(isCancelled(task('a', 'hold'))).toBe(false)
    expect(isCancelled(task('a', 'blocked'))).toBe(false)
    expect(isCancelled(task('a', 'active'))).toBe(false)
  })

  it('counts the ones on hold, and nothing else', () => {
    expect(isOnHold(task('a', 'hold'))).toBe(true)
    expect(isOnHold(task('a', 'cancelled'))).toBe(false)
    expect(isOnHold(task('a', 'blocked'))).toBe(false)
    expect(isOnHold(task('a', 'active'))).toBe(false)
  })

  it('keeps the two apart, so one hide never carries the other', () => {
    // The board used to put both away under one button. Cancelled work is gone
    // and held work is coming back, so a reader who wants one of them back has
    // no reason to be handed the other.
    expect(isCancelled(task('a', 'hold'))).toBe(false)
    expect(isOnHold(task('a', 'cancelled'))).toBe(false)
  })
})

describe('the cancelled hide', () => {
  it('is in force by default, with nothing else asking', () => {
    expect(hidesCancelled(false, 'all', parsed(''))).toBe(true)
  })

  it('is in force alongside filters that are about something else', () => {
    expect(hidesCancelled(false, 'active', parsed(''))).toBe(true)
    expect(hidesCancelled(false, 'blocked', parsed('blk: totals'))).toBe(true)
    expect(hidesCancelled(false, 'hold', parsed('hld:'))).toBe(true)
    expect(hidesCancelled(false, 'all', parsed('invoice totals'))).toBe(true)
  })

  it('does nothing once the reader has turned it off', () => {
    expect(hidesCancelled(true, 'all', parsed(''))).toBe(false)
    expect(hidesCancelled(true, 'active', parsed(''))).toBe(false)
  })

  it('stands down when the status filter asks for exactly what it hides', () => {
    expect(hidesCancelled(false, 'cancelled', parsed(''))).toBe(false)
  })

  it('stands down for cnl: in the search box', () => {
    expect(hidesCancelled(false, 'all', parsed('cnl:'))).toBe(false)
    expect(hidesCancelled(false, 'all', parsed('cnl: totals'))).toBe(false)
  })

  it('is not stood down by the tag for the other hide', () => {
    expect(hidesCancelled(false, 'all', parsed('hld:'))).toBe(true)
  })
})

describe('a column holding its on-hold cards back', () => {
  it('holds them back until that column is opened', () => {
    expect(hidesHold(false, 'all', parsed(''))).toBe(true)
    expect(hidesHold(true, 'all', parsed(''))).toBe(false)
  })

  it('stands down when the status filter asks for exactly what it holds back', () => {
    expect(hidesHold(false, 'hold', parsed(''))).toBe(false)
  })

  it('stands down for hld: in the search box', () => {
    expect(hidesHold(false, 'all', parsed('hld:'))).toBe(false)
    expect(hidesHold(false, 'all', parsed('hld: totals'))).toBe(false)
  })

  it('is not stood down by the tag for the other hide', () => {
    expect(hidesHold(false, 'all', parsed('cnl:'))).toBe(true)
    expect(hidesHold(false, 'cancelled', parsed(''))).toBe(true)
  })

  it('is left alone by filters about something else', () => {
    expect(hidesHold(false, 'active', parsed(''))).toBe(true)
    expect(hidesHold(false, 'blocked', parsed('blk:'))).toBe(true)
  })
})

describe('splitting a column at its foot', () => {
  const doing = task('Doing', 'active')
  const stalled = task('Stalled', 'hold')
  const stuck = task('Stuck', 'blocked')
  const paused = task('Paused', 'hold')
  const column = [doing, stalled, stuck, paused]

  it('draws everything but the held cards while the hide is on', () => {
    const { shown } = splitOnHold(column, true)
    expect(shown.map((one) => one.title)).toEqual(['Doing', 'Stuck'])
  })

  it('draws every card once the column is opened', () => {
    const { shown } = splitOnHold(column, false)
    expect(shown.map((one) => one.title)).toEqual(['Doing', 'Stalled', 'Stuck', 'Paused'])
  })

  it('puts a card shown again back where it was, not at the bottom', () => {
    // The order cards sit in a column is something somebody chose by dragging
    // them there, and opening the foot is a way of reading the column, not a
    // re-sort of it.
    const { shown } = splitOnHold(column, false)
    expect(shown.indexOf(stalled)).toBe(1)
    expect(shown.indexOf(paused)).toBe(3)
  })

  it('counts the held cards either way, so the foot can say how many', () => {
    expect(splitOnHold(column, true).onHold.map((one) => one.title)).toEqual(['Stalled', 'Paused'])
    expect(splitOnHold(column, false).onHold.map((one) => one.title)).toEqual(['Stalled', 'Paused'])
  })

  it('leaves a column with nothing on hold exactly as it found it', () => {
    const plain = [doing, stuck]
    expect(splitOnHold(plain, true).shown).toEqual(plain)
    expect(splitOnHold(plain, true).onHold).toEqual([])
  })

  it('leaves a cancelled card alone — that hide is the whole board’s', () => {
    const dropped = task('Dropped', 'cancelled')
    const { shown, onHold } = splitOnHold([doing, dropped, stalled], true)
    expect(shown.map((one) => one.title)).toEqual(['Doing', 'Dropped'])
    expect(onHold.map((one) => one.title)).toEqual(['Stalled'])
  })
})
