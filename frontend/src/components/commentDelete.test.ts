import { describe, expect, it } from 'vitest'
import type { Identity, Person, TaskComment } from '../api/client'
import { mayDelete, type Viewer } from './commentDelete'

const ADITI: Person = {
  id: 'person-aditi',
  name: 'Aditi K',
  kind: 'team',
  title: 'Backend engineer',
  responsibilities: 'Payments and webhooks.',
  email: 'aditi@cylist.dev',
  colour: '#1d7d46',
  archived_at: null,
  created_at: '2026-09-01T09:00:00Z',
  is_agent: false,
  has_account: true,
  invite_is_pending: false,
}

const SANJAY: Person = { ...ADITI, id: 'person-sanjay', name: 'Sanjay F', kind: 'client' }

const ME: Identity = {
  token_id: 'token-1',
  label: 'Web session',
  channel: 'web',
  scopes: ['read', 'write'],
  person: ADITI,
}

const COMMENT: TaskComment = {
  id: 'comment-1',
  task_id: 'task-1',
  author: ADITI,
  body: 'Keep amounts as minor units.',
  kind: 'comment',
  meta: {},
  created_at: '2026-09-02T11:00:00Z',
}

const member: Viewer = { identity: ME, amAdmin: false, mayComment: true }

describe('who may take a comment off a timeline', () => {
  it('lets somebody take back their own', () => {
    expect(mayDelete(COMMENT, member)).toBe(true)
  })

  it('does not let them take off somebody else’s', () => {
    expect(mayDelete({ ...COMMENT, author: SANJAY }, member)).toBe(false)
  })

  it('lets an admin take off anybody’s', () => {
    const admin: Viewer = { ...member, amAdmin: true }
    expect(mayDelete({ ...COMMENT, author: SANJAY }, admin)).toBe(true)
  })

  it('keeps a status change whoever is looking', () => {
    const change: TaskComment = {
      ...COMMENT,
      kind: 'status_change',
      meta: { from: 'active', to: 'hold', reason: 'Waiting on finance.', tagged: [] },
    }
    expect(mayDelete(change, member)).toBe(false)
    expect(mayDelete(change, { ...member, amAdmin: true })).toBe(false)
  })

  it('leaves an unattributed comment to the admin', () => {
    const unattributed: TaskComment = { ...COMMENT, author: null }
    expect(mayDelete(unattributed, member)).toBe(false)
    expect(mayDelete(unattributed, { ...member, amAdmin: true })).toBe(true)
  })

  it('does not offer it to a role that may not comment', () => {
    expect(mayDelete(COMMENT, { ...member, mayComment: false })).toBe(false)
  })

  it('offers nothing to a session that is nobody', () => {
    const bootstrap: Viewer = { ...member, identity: { ...ME, person: null } }
    expect(mayDelete(COMMENT, bootstrap)).toBe(false)
  })
})
