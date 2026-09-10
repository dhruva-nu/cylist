/**
 * What a card says about the agent on it.
 *
 * The border is the half of the feature that can be wrong quietly: a card that
 * says nothing when an agent has a question for you looks exactly like a card
 * with no agent on it. So every state, every label, and the rule for when the
 * board keeps polling are pinned here.
 */

import { describe, expect, it } from 'vitest'

import type { AgentPresence } from '../api/client'
import { agentIndicator, hasLiveAgent, silentFor, waitingDetail } from './agentState'

const NOW = new Date('2026-09-08T12:00:00Z')

function presence(overrides: Partial<AgentPresence> = {}): AgentPresence {
  return {
    state: 'working',
    count: 1,
    reason: null,
    client_name: 'CYLIST-37',
    since: '2026-09-08T11:50:00Z',
    last_seen_at: '2026-09-08T11:59:30Z',
    ...overrides,
  }
}

describe('agentIndicator', () => {
  it('is nothing for a card with no agent', () => {
    expect(agentIndicator(null, NOW)).toBeNull()
  })

  it('says one agent is working', () => {
    expect(agentIndicator(presence(), NOW)).toEqual({
      className: 'agentWorking',
      label: 'Agent working',
    })
  })

  it('counts when more than one is working', () => {
    expect(agentIndicator(presence({ count: 2 }), NOW)?.label).toBe('2 agents working')
  })

  it('says an agent needs you, and what for', () => {
    expect(agentIndicator(presence({ state: 'waiting', reason: 'permission' }), NOW)).toEqual({
      className: 'agentWaiting',
      label: 'Agent needs you',
      detail: 'permission',
    })
  })

  it('reads the end of a turn as waiting for a reply', () => {
    expect(agentIndicator(presence({ state: 'waiting', reason: 'turn_ended' }), NOW)?.detail).toBe(
      'waiting for your reply',
    )
    expect(agentIndicator(presence({ state: 'waiting', reason: null }), NOW)?.detail).toBe(
      'waiting for your reply',
    )
  })

  it('says which of several sessions matters', () => {
    const found = agentIndicator(presence({ state: 'waiting', count: 2, reason: 'idle' }), NOW)
    expect(found?.label).toBe('2 agents · one needs you')
    expect(found?.detail).toBe('idle')
  })

  it('says an agent finished', () => {
    expect(agentIndicator(presence({ state: 'done', reason: 'session_ended' }), NOW)).toEqual({
      className: 'agentDone',
      label: 'Agent finished',
    })
    expect(agentIndicator(presence({ state: 'done', count: 3 }), NOW)?.label).toBe(
      '3 agents finished',
    )
  })

  it('draws a lost connection as an outline, not as a finish', () => {
    const found = agentIndicator(
      presence({
        state: 'done',
        reason: 'connection_lost',
        last_seen_at: '2026-09-08T11:48:00Z',
      }),
      NOW,
    )
    expect(found).toEqual({
      className: 'agentStale',
      label: 'Agent disconnected',
      detail: 'last seen 12m ago',
    })
  })
})

describe('silentFor', () => {
  it('counts minutes, then hours, then days', () => {
    expect(silentFor('2026-09-08T11:48:00Z', NOW)).toBe('12m')
    expect(silentFor('2026-09-08T09:30:00Z', NOW)).toBe('2h')
    expect(silentFor('2026-09-05T12:00:00Z', NOW)).toBe('3d')
  })

  it('never goes negative when clocks disagree', () => {
    expect(silentFor('2026-09-08T12:05:00Z', NOW)).toBe('0m')
  })
})

describe('waitingDetail', () => {
  it('words every reason', () => {
    expect(waitingDetail('permission')).toBe('permission')
    expect(waitingDetail('idle')).toBe('idle')
    expect(waitingDetail('question')).toBe('a question')
    expect(waitingDetail('turn_ended')).toBe('waiting for your reply')
  })
})

describe('hasLiveAgent', () => {
  it('is false for a board with no agents', () => {
    expect(hasLiveAgent([{ agent_session: null }, { agent_session: null }])).toBe(false)
  })

  it('is true while any card is working or waiting', () => {
    expect(hasLiveAgent([{ agent_session: null }, { agent_session: presence() }])).toBe(true)
    expect(hasLiveAgent([{ agent_session: presence({ state: 'waiting' }) }])).toBe(true)
  })

  it('stops once a session that was cut off has been recorded as over', () => {
    // A dropped agent is `done` now, not a working row nobody has heard
    // from — so there is nothing left to wait for.
    expect(
      hasLiveAgent([{ agent_session: presence({ state: 'done', reason: 'connection_lost' }) }]),
    ).toBe(false)
  })

  it('is false once every session is over', () => {
    expect(hasLiveAgent([{ agent_session: presence({ state: 'done' }) }])).toBe(false)
  })
})
