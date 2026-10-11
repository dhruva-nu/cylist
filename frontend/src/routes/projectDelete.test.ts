import { describe, expect, it } from 'vitest'
import { confirms } from './projectDelete'

describe('confirming a project delete', () => {
  it('accepts the key', () => {
    expect(confirms('ATL', 'ATL')).toBe(true)
  })

  it('forgives case and surrounding space', () => {
    expect(confirms('ATL', ' atl ')).toBe(true)
  })

  it('refuses another project, however similar', () => {
    expect(confirms('ATL', 'ATLAS')).toBe(false)
    expect(confirms('ATL', 'AT')).toBe(false)
  })

  it('refuses the project name, which is not what was asked for', () => {
    expect(confirms('ATL', 'Atlas Billing Migration')).toBe(false)
  })

  it('refuses nothing at all', () => {
    expect(confirms('ATL', '')).toBe(false)
    expect(confirms('ATL', '   ')).toBe(false)
  })
})
