/**
 * Answering an agent from the board or the task: what gets sent, how each
 * choice is drawn, what a refusal reads as, and what the card and the task
 * look like the moment the answer lands.
 */

import { describe, expect, it } from 'vitest'

import {
  ApiError,
  type AgentPresence,
  type AgentPrompt,
  type AgentSessionRead,
  type Task,
  type TaskDetail,
} from '../api/client'
import {
  answerFailure,
  choiceTone,
  detailWithPromptAnswered,
  mayAnswer,
  notYours,
  promptClosedUnderUs,
  promptHeading,
  replyAnswer,
  sessionWithPromptAnswered,
  tasksWithPromptAnswered,
} from './promptAnswer'

const NOW = new Date('2026-10-12T12:00:00Z')

function prompt(overrides: Partial<AgentPrompt> = {}): AgentPrompt {
  return {
    id: 'p1',
    agent_session_id: 's1',
    task_id: 't1',
    kind: 'question',
    text: 'Grey dot, or no dot?',
    tool: null,
    options: [
      { key: '1', label: 'Grey' },
      { key: '2', label: 'None' },
    ],
    allows_reply: true,
    owner_id: 'person-me',
    owner: 'Dhruva N',
    status: 'open',
    answer: null,
    answer_key: null,
    answered_by: null,
    answered_by_id: null,
    answered_at: null,
    closed_reason: null,
    asked_at: '2026-10-12T11:55:00Z',
    expires_at: '2026-10-12T12:25:00Z',
    closed_at: null,
    ...overrides,
  }
}

const permission = prompt({
  kind: 'permission',
  text: 'git push origin staging',
  tool: 'Bash',
  allows_reply: false,
  options: [
    { key: 'allow_once', label: 'Allow once' },
    { key: 'allow_session', label: 'Allow for this session' },
    { key: 'deny', label: 'Deny' },
  ],
})

const answered = prompt({
  status: 'answered',
  answer: 'Grey',
  answer_key: '1',
  answered_by: 'Dhruva',
  answered_at: '2026-10-12T12:00:00Z',
  closed_at: '2026-10-12T12:00:00Z',
})

function presence(overrides: Partial<AgentPresence> = {}): AgentPresence {
  return {
    state: 'waiting',
    count: 1,
    reason: 'question',
    client_name: 'CYLIST-81',
    note: 'Grey dot, or no dot?',
    since: '2026-10-12T11:55:00Z',
    last_seen_at: '2026-10-12T11:59:00Z',
    prompt: prompt(),
    ...overrides,
  }
}

function session(overrides: Partial<AgentSessionRead> = {}): AgentSessionRead {
  return {
    id: 's1',
    task_id: 't1',
    actor_label: 'laptop',
    client_name: 'CYLIST-81',
    state: 'waiting',
    reason: 'question',
    note: 'Grey dot, or no dot?',
    started_at: '2026-10-12T11:00:00Z',
    state_changed_at: '2026-10-12T11:55:00Z',
    last_seen_at: '2026-10-12T11:59:00Z',
    ended_at: null,
    dismissed_at: null,
    prompt: prompt(),
    ...overrides,
  }
}

function task(id: string, agent: AgentPresence | null): Task {
  return { id, agent_session: agent } as Task
}

describe('promptHeading', () => {
  it('says what kind of ask it is and how long it has waited', () => {
    expect(promptHeading(prompt(), NOW)).toBe('Question from the agent · 5m ago')
    expect(promptHeading(permission, NOW)).toBe('Permission asked · 5m ago')
    expect(promptHeading(prompt({ asked_at: '2026-10-12T11:59:40Z' }), NOW)).toBe(
      'Question from the agent · just now',
    )
  })
})

describe('replyAnswer', () => {
  it('sends what was typed, trimmed', () => {
    expect(replyAnswer(prompt(), '  hide it  ')).toEqual({ text: 'hide it' })
  })

  it('sends nothing for an empty box', () => {
    expect(replyAnswer(prompt(), '   ')).toBeNull()
  })

  it('never sends a typed reply to a permission', () => {
    expect(replyAnswer(permission, 'yes please')).toBeNull()
  })
})

describe('choiceTone', () => {
  it('draws a permission as yes, maybe and no', () => {
    expect(choiceTone(permission, 'allow_once')).toBe('go')
    expect(choiceTone(permission, 'allow_session')).toBe('plain')
    expect(choiceTone(permission, 'deny')).toBe('danger')
  })

  it("draws none of a question's choices as the safe one", () => {
    expect(choiceTone(prompt(), '1')).toBe('plain')
    expect(choiceTone(prompt(), '2')).toBe('plain')
  })
})

describe('answerFailure', () => {
  it("says the server's own words when somebody got there first", () => {
    const error = new ApiError(409, 'conflict', 'Already answered by Aditi: Grey.')
    expect(answerFailure(error)).toBe('Already answered by Aditi: Grey.')
    expect(promptClosedUnderUs(error)).toBe(true)
  })

  it('says the answer did not arrive for anything else', () => {
    const error = new ApiError(500, 'internal', 'Boom')
    expect(answerFailure(error)).toBe('The answer did not reach the agent: Boom')
    expect(promptClosedUnderUs(error)).toBe(false)
  })
})

describe('tasksWithPromptAnswered', () => {
  it('turns the answered card back to working, saying what you said', () => {
    const [card, other] = tasksWithPromptAnswered(
      [
        task('t1', presence()),
        task('t2', presence({ prompt: prompt({ id: 'p2', task_id: 't2' }) })),
      ],
      answered,
    )
    expect(card?.agent_session).toMatchObject({
      state: 'working',
      reason: null,
      note: null,
      since: '2026-10-12T12:00:00Z',
      prompt: { status: 'answered', answer: 'Grey' },
    })
    // Another card's prompt is nobody else's business.
    expect(other?.agent_session?.state).toBe('waiting')
  })

  it('leaves a card alone that has moved on to another prompt', () => {
    const moved = task('t1', presence({ prompt: prompt({ id: 'p9' }) }))
    expect(tasksWithPromptAnswered([moved], answered)[0]).toBe(moved)
  })

  it('leaves the state to the server when several sessions share the card', () => {
    const [card] = tasksWithPromptAnswered([task('t1', presence({ count: 2 }))], answered)
    expect(card?.agent_session?.state).toBe('waiting')
    expect(card?.agent_session?.prompt?.status).toBe('answered')
  })
})

describe('sessionWithPromptAnswered', () => {
  it('turns the session that asked back to working', () => {
    expect(sessionWithPromptAnswered(session(), answered)).toMatchObject({
      state: 'working',
      reason: null,
      note: null,
      state_changed_at: '2026-10-12T12:00:00Z',
      prompt: { status: 'answered' },
    })
  })

  it('leaves other sessions alone', () => {
    const other = session({ id: 's2', prompt: null })
    expect(sessionWithPromptAnswered(other, answered)).toBe(other)
  })

  it('does not bring an ended session back to life', () => {
    const ended = session({
      ended_at: '2026-10-12T11:59:30Z',
      state: 'done',
      reason: 'session_ended',
    })
    expect(sessionWithPromptAnswered(ended, answered).state).toBe('done')
  })
})

describe('detailWithPromptAnswered', () => {
  it("updates the task's sessions and its card presence together", () => {
    const detail = {
      ...task('t1', presence()),
      agent_sessions: [session()],
    } as TaskDetail
    const next = detailWithPromptAnswered(detail, answered)
    expect(next.agent_sessions[0]?.state).toBe('working')
    expect(next.agent_session?.state).toBe('working')
  })

  it("leaves another task's detail alone", () => {
    const detail = { ...task('t2', null), agent_sessions: [] } as unknown as TaskDetail
    expect(detailWithPromptAnswered(detail, answered)).toBe(detail)
  })
})

describe('mayAnswer', () => {
  it('lets only the owner answer a permission: the tool runs on their machine', () => {
    expect(mayAnswer(permission, 'person-me')).toBe(true)
    expect(mayAnswer(permission, 'person-aditi')).toBe(false)
    expect(mayAnswer(permission, null)).toBe(false)
  })

  it('lets anyone who may edit the card answer a question', () => {
    expect(mayAnswer(prompt(), 'person-aditi')).toBe(true)
  })

  it('says whose a permission is to everyone else', () => {
    expect(notYours(permission)).toBe(
      'Only Dhruva N can answer this: the tool runs on their machine.',
    )
    expect(notYours(prompt({ kind: 'permission', owner: null }))).toBe(
      'Only the person running this agent can answer it.',
    )
  })
})
