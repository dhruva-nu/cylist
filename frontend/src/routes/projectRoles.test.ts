import { describe, expect, it } from 'vitest'
import type { Identity, Member, RoleSummary } from '../api/client'
import { isProjectAdmin, myMembership, nameIsTaken, whyUndeletable } from './projectRoles'

const ADMIN: RoleSummary = {
  id: 'role-admin',
  name: 'Admin',
  description: '',
  colour: '#1D7D46',
  is_admin: true,
  member_count: 1,
  created_at: '2026-01-01T00:00:00Z',
}

const REVIEWER: RoleSummary = {
  ...ADMIN,
  id: 'role-reviewer',
  name: 'Reviewer',
  is_admin: false,
  member_count: 0,
}

function member(id: string, role: RoleSummary | null): Member {
  return {
    id,
    name: id,
    kind: 'team',
    title: 'Backend engineer',
    responsibilities: '',
    email: null,
    colour: '#1D7D46',
    archived_at: null,
    created_at: '2026-01-01T00:00:00Z',
    has_account: true,
    invite_is_pending: false,
    role,
  }
}

function signedInAs(id: string | null): Identity {
  return {
    token_id: 'token',
    label: 'Web session',
    channel: 'web',
    scopes: ['read', 'write', 'admin'],
    person: id === null ? null : member(id, null),
  }
}

describe('myMembership', () => {
  it('finds the reader among the members', () => {
    const members = [member('aditi', null), member('dhruva', ADMIN)]

    expect(myMembership(members, signedInAs('dhruva'))?.id).toBe('dhruva')
  })

  it('is undefined when the reader is not on this project', () => {
    expect(myMembership([member('aditi', null)], signedInAs('dhruva'))).toBeUndefined()
  })

  it('is undefined for a credential that belongs to nobody', () => {
    expect(myMembership([member('aditi', ADMIN)], signedInAs(null))).toBeUndefined()
  })
})

describe('isProjectAdmin', () => {
  it('is true for somebody wearing the admin role', () => {
    expect(isProjectAdmin([member('dhruva', ADMIN)], signedInAs('dhruva'))).toBe(true)
  })

  it('is false for a member wearing another role', () => {
    expect(isProjectAdmin([member('dhruva', REVIEWER)], signedInAs('dhruva'))).toBe(false)
  })

  it('is false for a member with no role at all', () => {
    expect(isProjectAdmin([member('dhruva', null)], signedInAs('dhruva'))).toBe(false)
  })

  it('does not read the admin scope', () => {
    /**
     * The scope says what the credential may do anywhere; this asks who the
     * person is on one board. The identity above carries `admin` throughout,
     * so a version that consulted it would pass every test in this block.
     */
    expect(isProjectAdmin([member('aditi', ADMIN)], signedInAs('dhruva'))).toBe(false)
  })

  it('is false while the member list is still loading', () => {
    expect(isProjectAdmin([], signedInAs('dhruva'))).toBe(false)
  })
})

describe('nameIsTaken', () => {
  it('catches an exact repeat', () => {
    expect(nameIsTaken([ADMIN, REVIEWER], 'Reviewer')).toBe(true)
  })

  it('ignores case and surrounding space', () => {
    expect(nameIsTaken([REVIEWER], '  reviewer ')).toBe(true)
  })

  it('lets a different name through', () => {
    expect(nameIsTaken([REVIEWER], 'Designer')).toBe(false)
  })

  it('says nothing about an empty box', () => {
    expect(nameIsTaken([REVIEWER], '   ')).toBe(false)
  })

  it('does not count the role being renamed against itself', () => {
    expect(nameIsTaken([REVIEWER], 'Reviewer', REVIEWER.id)).toBe(false)
  })
})

describe('whyUndeletable', () => {
  it('protects the admin role', () => {
    expect(whyUndeletable(ADMIN)).toMatch(/Admin role/)
  })

  it('protects a role somebody is wearing, and says how many', () => {
    expect(whyUndeletable({ ...REVIEWER, member_count: 3 })).toBe('Move 3 people off it first.')
  })

  it('words one holder as a person rather than a count', () => {
    expect(whyUndeletable({ ...REVIEWER, member_count: 1 })).toBe('Move somebody off it first.')
  })

  it('allows a role nobody holds', () => {
    expect(whyUndeletable(REVIEWER)).toBeNull()
  })
})
