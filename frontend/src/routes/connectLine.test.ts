import { describe, expect, it } from 'vitest'
import { connectLine } from './connectLine'

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
