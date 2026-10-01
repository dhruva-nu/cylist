import { describe, expect, it } from 'vitest'
import { agentScopes, connectLine } from './connectLine'

describe('connectLine', () => {
  const line = connectLine('https://box.tailnet.ts.net', 'cyl_abc123')

  it('points Claude Code at /mcp on this origin, over HTTP', () => {
    expect(line).toContain('--transport http')
    expect(line).toContain(' cylist https://box.tailnet.ts.net/mcp ')
  })

  it('puts the variadic --header last, where it cannot swallow the name or address', () => {
    expect(line.endsWith('--header "Authorization: Bearer cyl_abc123"')).toBe(true)
  })

  it('registers for every project on the machine, not just the current directory', () => {
    expect(line).toContain('--scope user')
  })
})

describe('agentScopes', () => {
  it('mints what the server says an agent is given', () => {
    expect(agentScopes({ agent_scopes: ['read', 'write', 'vault:read', 'vault:reveal'] })).toEqual([
      'read',
      'write',
      'vault:read',
      'vault:reveal',
    ])
  })

  it('falls back to the board alone before the server has answered', () => {
    expect(agentScopes(undefined)).toEqual(['read', 'write'])
    expect(agentScopes({ agent_scopes: [] })).toEqual(['read', 'write'])
  })

  it('never hands an agent admin, and drops scopes it does not know', () => {
    expect(agentScopes({ agent_scopes: ['read', 'admin', 'vault:everything'] })).toEqual(['read'])
  })
})
