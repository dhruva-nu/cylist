/** The dial's arithmetic: how far round each board sits, the short way. */

import { describe, expect, it } from 'vitest'

import { dialPlace, stepsFrom } from './BoardDial'

describe('stepsFrom', () => {
  it('counts forward and back from the selected board', () => {
    expect(stepsFrom(3, 1, 7)).toBe(2)
    expect(stepsFrom(0, 1, 7)).toBe(-1)
  })

  it('wraps round the short way', () => {
    // The last board is one step back from the first, not six forward.
    expect(stepsFrom(6, 0, 7)).toBe(-1)
    expect(stepsFrom(0, 6, 7)).toBe(1)
  })
})

describe('dialPlace', () => {
  it('draws the selected board whole, and fades the far ones out', () => {
    expect(dialPlace(0).opacity).toBe(1)
    expect(dialPlace(1).opacity).toBeLessThan(1)
    expect(dialPlace(-3).opacity).toBe(0)
  })
})
