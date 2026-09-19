import { describe, expect, it } from 'vitest'
import type { Permission, PermissionInfo, RolePermissions } from '../api/client'
import {
  can,
  countPeople,
  explain,
  inOrder,
  isEveryoneElse,
  isFixed,
  summarise,
  toggled,
} from './projectPermissions'

const CATALOGUE: PermissionInfo[] = (['tasks', 'comments', 'goals', 'files'] as Permission[]).map(
  (key) => ({ key, label: key, summary: '' }),
)

function line(overrides: Partial<RolePermissions> = {}): RolePermissions {
  return {
    role_id: 'r1',
    name: 'Reviewer',
    colour: '#1D7D46',
    is_admin: false,
    member_count: 1,
    permissions: [],
    ...overrides,
  }
}

describe('can', () => {
  it('reads the reader’s own permissions', () => {
    expect(can(['tasks', 'comments'], 'tasks')).toBe(true)
    expect(can(['comments'], 'tasks')).toBe(false)
  })

  it('says yes while the query is still in flight', () => {
    // A button that flickers out of existence on every page load is worse
    // than one that refuses once.
    expect(can(undefined, 'tasks')).toBe(true)
  })

  it('says no for a role that allows nothing', () => {
    expect(can([], 'tasks')).toBe(false)
  })
})

describe('toggled', () => {
  it('adds a permission and keeps the row sorted', () => {
    expect(toggled(['tasks'], 'comments', true)).toEqual(['comments', 'tasks'])
  })

  it('removes one', () => {
    expect(toggled(['comments', 'tasks'], 'tasks', false)).toEqual(['comments'])
  })

  it('never doubles one up', () => {
    expect(toggled(['tasks'], 'tasks', true)).toEqual(['tasks'])
  })

  it('leaves the original alone', () => {
    const before: Permission[] = ['tasks']
    toggled(before, 'goals', true)
    expect(before).toEqual(['tasks'])
  })
})

describe('summarise', () => {
  it('counts against the catalogue the server sent', () => {
    expect(summarise(line({ permissions: ['tasks', 'goals'] }), CATALOGUE)).toBe('2 of 4')
  })

  it('says Everything when the whole catalogue is ticked', () => {
    expect(
      summarise(line({ permissions: ['tasks', 'comments', 'goals', 'files'] }), CATALOGUE),
    ).toBe('Everything')
  })

  it('says Everything for the admin role whatever is stored', () => {
    expect(summarise(line({ is_admin: true, permissions: [] }), CATALOGUE)).toBe('Everything')
  })

  it('says Nothing for a role that allows nothing', () => {
    expect(summarise(line(), CATALOGUE)).toBe('Nothing')
  })
})

describe('the two lines that are not ordinary roles', () => {
  it('knows the baseline by its missing id', () => {
    expect(isEveryoneElse(line({ role_id: null }))).toBe(true)
    expect(isEveryoneElse(line())).toBe(false)
  })

  it('will not let the admin role be ticked', () => {
    expect(isFixed(line({ is_admin: true }))).toBe(true)
    expect(isFixed(line())).toBe(false)
  })

  it('explains both, and says nothing about an ordinary role', () => {
    expect(explain(line({ is_admin: true }))).toContain('everything')
    expect(explain(line({ role_id: null }))).toContain('not on it')
    expect(explain(line())).toBeNull()
  })
})

describe('countPeople', () => {
  it('reads as a sentence would', () => {
    expect(countPeople(0)).toBe('nobody')
    expect(countPeople(1)).toBe('1 person')
    expect(countPeople(4)).toBe('4 people')
  })
})

describe('inOrder', () => {
  it('puts the admin first and the baseline last, whatever order they arrive in', () => {
    const grid = {
      catalogue: CATALOGUE,
      mine: [] as Permission[],
      may_manage: true,
      roles: [
        line({ role_id: null, name: 'Everyone else' }),
        line({ role_id: 'r2', name: 'QA' }),
        line({ role_id: 'r0', name: 'Admin', is_admin: true }),
        line({ role_id: 'r1', name: 'Reviewer' }),
      ],
    }

    expect(inOrder(grid).map((role) => role.name)).toEqual([
      'Admin',
      'QA',
      'Reviewer',
      'Everyone else',
    ])
  })
})
