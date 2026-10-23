import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

describe('router', () => {
  it('imports no page statically, so each is its own chunk', () => {
    // One static import of a page pulls it, and everything it imports, back
    // into the bundle every visitor downloads before seeing anything.
    const source = readFileSync('src/router.tsx', 'utf8')

    expect(source.match(/^import .* from '\.\/routes\/.*'$/gm) ?? []).toEqual([])
  })
})
