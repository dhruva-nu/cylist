import { describe, expect, it } from 'vitest'
import type { Hook, HookDelivery, HookEvent } from '../api/client'
import {
  EMPTY_DRAFT,
  groupEvents,
  pickedIn,
  withVerbs,
  deliveryOutcome,
  describeRule,
  draftOf,
  eventsPhrase,
  inputOf,
  isDangling,
  isHookUrl,
  whyIncomplete,
} from './projectHooks'

const event = (verb: string, label: string, category: string, name: string): HookEvent => ({
  verb,
  label,
  hint: '',
  category,
  category_name: name,
})

const EVENTS: HookEvent[] = [
  event('task.moved', 'Moved to another column', 'moves', 'Card moves'),
  event('task.sub_status_moved', 'Sub-status stepped', 'moves', 'Card moves'),
  event('task.created', 'Card created', 'cards', 'Card changes'),
  event('task.deleted', 'Card deleted', 'cards', 'Card changes'),
  event('task.commented', 'Comment added', 'comments', 'Comments'),
]

const HOTFIX_TO_STAGING: Hook = {
  id: 'hook-1',
  name: 'Hotfix to staging',
  enabled: true,
  url: 'https://ci.example.com/cylist',
  verbs: ['task.moved'],
  to_column_id: 'col-staging',
  to_column_name: 'In staging',
  from_column_id: null,
  from_column_name: null,
  template_id: 'tpl-hotfix',
  template_name: 'Hotfix',
  task_type: null,
  secret_hint: 'abcd',
  last_delivery: null,
  created_at: '2026-10-06T00:00:00Z',
  updated_at: '2026-10-06T00:00:00Z',
}

const DELIVERY: HookDelivery = {
  id: 'delivery-1',
  hook_id: 'hook-1',
  activity_id: 'activity-1',
  event: 'task.moved',
  state: 'delivered',
  attempt_count: 1,
  next_attempt_at: null,
  delivered_at: '2026-10-06T10:00:00Z',
  last_status_code: 200,
  last_error: null,
  attempts: [{ at: '2026-10-06T10:00:00Z', status_code: 200, error: null, duration_ms: 84 }],
  payload: {},
  created_at: '2026-10-06T10:00:00Z',
}

describe('describeRule', () => {
  it('reads a hotfix moved into staging the way it was thought of', () => {
    expect(describeRule(HOTFIX_TO_STAGING, EVENTS)).toBe(
      'Moved to another column · Hotfix cards · into In staging',
    )
  })

  it('says "in" a column for a hook that is not only about moves', () => {
    const hook = { ...HOTFIX_TO_STAGING, verbs: [], template_id: null, template_name: null }
    expect(describeRule(hook, EVENTS)).toBe('Any change · in In staging')
  })

  it('names a filter whose column has been deleted', () => {
    const hook = { ...HOTFIX_TO_STAGING, to_column_name: null }
    expect(describeRule(hook, EVENTS)).toContain('into a deleted column')
    expect(isDangling(hook)).toBe(true)
    expect(isDangling(HOTFIX_TO_STAGING)).toBe(false)
  })

  it('names a group ticked whole by the group, and the rest by label', () => {
    expect(eventsPhrase(['task.moved', 'task.sub_status_moved'], EVENTS)).toBe('Card moves')
    expect(eventsPhrase(['task.moved', 'task.created'], EVENTS)).toBe(
      'Moved to another column or Card created',
    )
    // A one-event group is named by its event, not by itself.
    expect(eventsPhrase(['task.commented'], EVENTS)).toBe('Comment added')
  })

  it('says how many more past two, rather than running on', () => {
    expect(eventsPhrase(['task.moved', 'task.created', 'task.commented'], EVENTS)).toBe(
      'Moved to another column, Card created and 1 more',
    )
  })
})

describe('the event picker', () => {
  it('groups the catalogue in the order it came', () => {
    const groups = groupEvents(EVENTS)
    expect(groups.map((group) => group.name)).toEqual(['Card moves', 'Card changes', 'Comments'])
    expect(groups[0]?.events.map((e) => e.verb)).toEqual(['task.moved', 'task.sub_status_moved'])
  })

  it('counts what is ticked in a group', () => {
    const [moves] = groupEvents(EVENTS)
    expect(moves && pickedIn(moves, ['task.moved', 'task.created'])).toBe(1)
  })

  it('ticks and unticks in catalogue order, whatever order they were clicked', () => {
    const ticked = withVerbs(['task.commented'], ['task.created', 'task.moved'], true, EVENTS)
    expect(ticked).toEqual(['task.moved', 'task.created', 'task.commented'])
    expect(withVerbs(ticked, ['task.moved', 'task.created'], false, EVENTS)).toEqual([
      'task.commented',
    ])
  })
})

describe('the form', () => {
  it('round-trips a hook, sending a cleared filter as null', () => {
    const draft = { ...draftOf(HOTFIX_TO_STAGING), templateId: '' }
    const input = inputOf(draft, false)
    expect(input.template_id).toBeNull()
    expect(input.to_column_id).toBe('col-staging')
    expect(input).not.toHaveProperty('secret')
  })

  it('sends a secret only when making a hook, and only if one was typed', () => {
    const draft = { ...EMPTY_DRAFT, name: 'A', url: 'https://x.dev', anyChange: true, secret: '  ' }
    expect(inputOf(draft, true)).not.toHaveProperty('secret')
    expect(inputOf({ ...draft, secret: 'whsec_mine' }, true).secret).toBe('whsec_mine')
  })

  it('refuses what the server would', () => {
    const draft = {
      ...EMPTY_DRAFT,
      name: 'Deploy',
      url: 'https://ci.example.com',
      verbs: ['task.moved'],
    }
    expect(whyIncomplete(draft, [], null)).toBeNull()
    expect(whyIncomplete({ ...draft, verbs: [] }, [], null)).toMatch(/at least one event/)
    expect(whyIncomplete({ ...draft, verbs: [], anyChange: true }, [], null)).toBeNull()
    expect(whyIncomplete({ ...draft, name: ' ' }, [], null)).toMatch(/name/)
    expect(whyIncomplete({ ...draft, url: 'ftp://x' }, [], null)).toMatch(/http/)
    expect(
      whyIncomplete({ ...draft, name: 'Hotfix to staging' }, [HOTFIX_TO_STAGING], null),
    ).toMatch(/already/)
    // Its own name is not a clash with itself.
    expect(
      whyIncomplete(
        { ...draft, name: 'Hotfix to staging' },
        [HOTFIX_TO_STAGING],
        HOTFIX_TO_STAGING,
      ),
    ).toBeNull()
    expect(
      whyIncomplete({ ...draft, fromColumnId: 'col', verbs: ['task.created'] }, [], null),
    ).toMatch(/only matches moves/)
  })

  it('sends Any change as no verbs, and keeps what was ticked to switch back to', () => {
    const draft = draftOf({ ...HOTFIX_TO_STAGING, verbs: [] })
    expect(draft.anyChange).toBe(true)
    const ticked = { ...draft, verbs: ['task.moved'] }
    expect(inputOf({ ...ticked, anyChange: true }, false).verbs).toEqual([])
    expect(inputOf({ ...ticked, anyChange: false }, false).verbs).toEqual(['task.moved'])
  })

  it('knows a URL when it sees one', () => {
    expect(isHookUrl('https://ci.example.com/hook')).toBe(true)
    expect(isHookUrl('http://localhost:9000')).toBe(true)
    expect(isHookUrl('ci.example.com')).toBe(false)
    expect(isHookUrl('mailto:me@example.com')).toBe(false)
  })
})

describe('deliveryOutcome', () => {
  const now = new Date('2026-10-06T10:00:00Z')

  it('says what came back', () => {
    expect(deliveryOutcome(DELIVERY, now)).toBe('Delivered — 200 in 84 ms')
  })

  it('says when it will try again, and why it has to', () => {
    const retrying: HookDelivery = {
      ...DELIVERY,
      state: 'pending',
      last_status_code: 503,
      last_error: 'HTTP 503',
      next_attempt_at: '2026-10-06T10:10:00Z',
    }
    expect(deliveryOutcome(retrying, now)).toBe('Retrying in 10 min — HTTP 503')
  })

  it('says when it gave up', () => {
    const failed: HookDelivery = {
      ...DELIVERY,
      state: 'failed',
      attempt_count: 7,
      last_error: 'ConnectError',
    }
    expect(deliveryOutcome(failed, now)).toBe('Failed after 7 attempts — ConnectError')
  })

  it('calls an untried delivery queued', () => {
    expect(
      deliveryOutcome({ ...DELIVERY, state: 'pending', attempt_count: 0, attempts: [] }, now),
    ).toBe('Queued')
  })
})
