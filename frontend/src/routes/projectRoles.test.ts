import { describe, expect, it } from 'vitest'
import type { RoleSummary } from '../api/client'
import { nameIsTaken, whyUndeletable } from './projectRoles'

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
