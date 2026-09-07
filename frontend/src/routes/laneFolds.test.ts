/**
 * The folding of a filtered board.
 *
 * Which lane is open and which is folded is decided by two lists and a flag
 * rather than by the DOM, so the answers a reader would have to fold and
 * unfold a board to check can be checked here instead — in particular that
 * filtering leaves the remembered folds alone, which is the part that would go
 * wrong silently.
 */

import { describe, expect, it } from 'vitest'

import { flip, flipsForAll, laneFolded, type LaneFolds } from './laneFolds'

const SEARCH = { key: 'goal-search', empty: false }
const BILLING = { key: 'goal-billing', empty: true }
const NO_GOAL = { key: 'none', empty: true }

function folds(overrides: Partial<LaneFolds> = {}): LaneFolds {
  return { filtering: false, folded: [], flipped: [], ...overrides }
}

describe('laneFolded', () => {
  it('follows the reader off a filter, whatever a lane holds', () => {
    const state = folds({ folded: ['goal-search'] })
    expect(laneFolded(SEARCH, state)).toBe(true)
    expect(laneFolded(BILLING, state)).toBe(false)
  })

  it('folds the lanes a filter emptied and opens the ones it did not', () => {
    const state = folds({ filtering: true, folded: ['goal-search'] })
    expect(laneFolded(SEARCH, state)).toBe(false)
    expect(laneFolded(BILLING, state)).toBe(true)
  })

  it('lets a flip overrule the filter, either way', () => {
    const state = folds({ filtering: true, flipped: ['goal-search', 'goal-billing'] })
    expect(laneFolded(SEARCH, state)).toBe(true)
    expect(laneFolded(BILLING, state)).toBe(false)
  })

  it('gives the reader their own folds back when the filter is cleared', () => {
    const remembered = ['goal-search']
    const filtered = folds({ filtering: true, folded: remembered, flipped: ['goal-billing'] })
    expect(laneFolded(SEARCH, filtered)).toBe(false)

    const cleared = folds({ folded: remembered })
    expect(laneFolded(SEARCH, cleared)).toBe(true)
    expect(laneFolded(BILLING, cleared)).toBe(false)
  })
})

describe('flip', () => {
  it('adds a lane that is not in the list and drops one that is', () => {
    expect(flip([], 'none')).toEqual(['none'])
    expect(flip(['none', 'goal-search'], 'none')).toEqual(['goal-search'])
  })
})

describe('flipsForAll', () => {
  it('flips only the lanes the filter had answered the other way', () => {
    const lanes = [SEARCH, BILLING, NO_GOAL]
    expect(flipsForAll(lanes, true)).toEqual(['goal-search'])
    expect(flipsForAll(lanes, false)).toEqual(['goal-billing', 'none'])
  })

  it('leaves nothing flipped when every lane already agrees', () => {
    expect(flipsForAll([BILLING, NO_GOAL], true)).toEqual([])
  })
})
