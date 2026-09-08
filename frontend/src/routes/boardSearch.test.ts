/**
 * What `goal:` narrows the board to.
 *
 * The tag is the half of the feature that can be wrong quietly: a card left
 * out of a filtered board looks exactly like a card that is not there, and
 * nobody goes looking for what they cannot see. The rest of the search box —
 * `col:`, `who:`, the quoting — is exercised here only where the goal tag has
 * to live alongside it.
 */

import { describe, expect, it } from 'vitest'

import type { Goal, Person, Task } from '../api/client'
import {
  NO_GOAL,
  applySuggestion,
  filterTasks,
  parseQuery,
  suggestionsFor,
  tokenize,
} from './boardSearch'

const ADITI: Person = {
  id: 'person-1',
  name: 'Aditi K',
  kind: 'team',
  role: 'Backend engineer',
  responsibilities: '',
  email: null,
  colour: '#1D7D46',
  archived_at: null,
  created_at: '2026-01-01T00:00:00Z',
  is_me: false,
}

function goal(name: string, colour = '#3B6FC2'): Goal {
  return {
    id: name,
    project_id: 'project-1',
    reference: 'ATL-G1',
    number: 1,
    name,
    description: '',
    colour,
    status: 'open',
    target_date: null,
    achieved_at: null,
    owner: ADITI,
    progress: { total: 0, done: 0, cancelled: 0, open: 0, blocked: 0, on_hold: 0 },
    created_at: '2026-01-01T00:00:00Z',
  }
}

function task(title: string, onGoal: string | null): Task {
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
    status: 'active',
    template_id: null,
    template_name: null,
    goal_id: onGoal,
    goal_reference: onGoal ? 'ATL-G1' : null,
    goal_name: onGoal,
    goal_colour: onGoal ? '#3B6FC2' : null,
    jira_ref: null,
    pr_ref: null,
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

const SEARCH = task('Search filters', 'Search revamp')
const BILLING = task('Invoice totals', 'Billing hardening')
const LOOSE = task('Bump the dependency', null)
const ALL = [SEARCH, BILLING, LOOSE]

function filter(query: string): string[] {
  return filterTasks(ALL, parseQuery(tokenize(query)), [], [ADITI]).map((found) => found.title)
}

describe('the goal: tag', () => {
  it('narrows the board to one goal, by part of its name', () => {
    expect(filter('goal:Search')).toEqual(['Search filters'])
  })

  it('matches however the name was capitalised', () => {
    expect(filter('goal:sEaRcH')).toEqual(['Search filters'])
  })

  it('survives a name with a space in it, in quotes', () => {
    expect(filter('goal:"Billing hardening"')).toEqual(['Invoice totals'])
  })

  it('finds the cards on no goal at all', () => {
    expect(filter(`goal:${NO_GOAL}`)).toEqual(['Bump the dependency'])
  })

  it('leaves the board alone when the tag is still being typed', () => {
    // `goal:` with nothing after it is a caret mid-token, not a filter that
    // matches nothing — narrowing the board to empty as somebody types is the
    // board flickering out from under them.
    expect(filter('goal:')).toEqual(['Search filters', 'Invoice totals', 'Bump the dependency'])
  })

  it('is ANDed with the rest of the query', () => {
    expect(filter('goal:Search who:Aditi')).toEqual(['Search filters'])
    expect(filter('goal:Billing totals')).toEqual(['Invoice totals'])
    expect(filter('goal:Billing filters')).toEqual([])
  })
})

describe('its suggestions', () => {
  const goals = [goal('Search revamp'), goal('Billing hardening', '#B5533F')]

  it('offers every goal, with the colour it stands for', () => {
    const offered = suggestionsFor('goal:', [], [ADITI], goals)
    expect(offered.map((one) => one.value)).toEqual(['Search revamp', 'Billing hardening', NO_GOAL])
    expect(offered[0]?.colour).toBe('#3B6FC2')
  })

  it('narrows to what has been typed', () => {
    expect(suggestionsFor('goal:bill', [], [ADITI], goals).map((one) => one.value)).toEqual([
      'Billing hardening',
    ])
  })

  it('offers "none" last, and only while it still matches', () => {
    expect(suggestionsFor('goal:non', [], [ADITI], goals).map((one) => one.value)).toEqual([
      NO_GOAL,
    ])
    expect(suggestionsFor('goal:zzz', [], [ADITI], goals)).toEqual([])
  })

  it('quotes a chosen name that has a space in it', () => {
    const chosen = suggestionsFor('goal:bill', [], [ADITI], goals)[0]!
    expect(applySuggestion('goal:bill', chosen)).toBe('goal:"Billing hardening" ')
  })

  it('leaves an earlier token alone when one is accepted', () => {
    const chosen = suggestionsFor('goal:sea', [], [ADITI], goals)[0]!
    expect(applySuggestion('blk: goal:sea', chosen)).toBe('blk: goal:"Search revamp" ')
  })
})
