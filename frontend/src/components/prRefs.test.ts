/**
 * The pull-request list a card carries, as the dialog edits it.
 *
 * CYLIST-63. The half of this feature that can be wrong quietly is the list
 * arithmetic: removing the second of three and getting the first and second
 * back looks, on screen, exactly like removing the third. So every rule about
 * which rows survive an edit is pinned here rather than left to be noticed.
 *
 * CYLIST-76 doubles that: each row now carries a state as well as an address,
 * so a row can survive an edit and come back wearing the wrong one.
 */

import { describe, expect, it } from 'vitest'

import type { PrRef, PrState } from '../api/client'
import {
  addPrRef,
  cleanPrRefs,
  removePrRefAt,
  samePrRefs,
  setPrRefAt,
  setPrStateAt,
} from './prRefs'

/** One row, at `raised` unless the test is about some other state. */
function pr(url: string, state: PrState = 'raised'): PrRef {
  return { url, state }
}

describe('cleanPrRefs', () => {
  it('keeps the order they were given in', () => {
    expect(cleanPrRefs([pr('#9'), pr('#1'), pr('#5')])).toEqual([pr('#9'), pr('#1'), pr('#5')])
  })

  it('trims each one', () => {
    expect(cleanPrRefs([pr('  #212  ')])).toEqual([pr('#212')])
  })

  it('keeps the state beside the address it was typed against', () => {
    expect(cleanPrRefs([pr(' #212 ', 'merged')])).toEqual([pr('#212', 'merged')])
  })

  it('drops a row nobody has typed into', () => {
    expect(cleanPrRefs([pr('#212'), pr(''), pr('   ')])).toEqual([pr('#212')])
  })

  it('drops a blank row whatever state it is on', () => {
    expect(cleanPrRefs([pr('', 'merged')])).toEqual([])
  })

  it('keeps the first of a repeated reference and drops the rest', () => {
    expect(cleanPrRefs([pr('#212'), pr('#219'), pr('#212')])).toEqual([pr('#212'), pr('#219')])
  })

  it('keeps the first of a repeat with its own state, not the later one', () => {
    expect(cleanPrRefs([pr('#212', 'merged'), pr('#212', 'raised')])).toEqual([
      pr('#212', 'merged'),
    ])
  })

  it('treats a reference repeated with different spacing as the same one', () => {
    expect(cleanPrRefs([pr('#212'), pr(' #212 ')])).toEqual([pr('#212')])
  })

  it('leaves a whole URL whole', () => {
    const link = 'https://github.com/acme/atlas/pull/219'
    expect(cleanPrRefs([pr(link, 'approved')])).toEqual([pr(link, 'approved')])
  })

  it('is nothing at all for a list of nothing but blanks', () => {
    expect(cleanPrRefs([pr(''), pr('  ')])).toEqual([])
  })
})

describe('addPrRef', () => {
  it('puts an empty row on the end to type into', () => {
    expect(addPrRef([pr('#212')])).toEqual([pr('#212'), pr('')])
  })

  it('starts the new row where a pull request starts', () => {
    expect(addPrRef([])).toEqual([{ url: '', state: 'raised' }])
  })

  it('does not disturb what is already there', () => {
    const refs = [pr('#212', 'merged'), pr('#219')]
    addPrRef(refs)
    expect(refs).toEqual([pr('#212', 'merged'), pr('#219')])
  })
})

describe('removePrRefAt', () => {
  it('takes the one in the middle and closes the gap', () => {
    expect(removePrRefAt([pr('#212'), pr('#219'), pr('#231')], 1)).toEqual([pr('#212'), pr('#231')])
  })

  it('leaves the survivors wearing their own states', () => {
    const refs = [pr('#212', 'merged'), pr('#219'), pr('#231', 'approved')]
    expect(removePrRefAt(refs, 1)).toEqual([pr('#212', 'merged'), pr('#231', 'approved')])
  })

  it('takes the first', () => {
    expect(removePrRefAt([pr('#212'), pr('#219')], 0)).toEqual([pr('#219')])
  })

  it('takes the last', () => {
    expect(removePrRefAt([pr('#212'), pr('#219')], 1)).toEqual([pr('#212')])
  })

  it('leaves the list alone when there is no such row', () => {
    expect(removePrRefAt([pr('#212')], 4)).toEqual([pr('#212')])
  })

  it('empties a list of one', () => {
    expect(removePrRefAt([pr('#212')], 0)).toEqual([])
  })
})

describe('setPrRefAt', () => {
  it('retypes the row named and no other', () => {
    expect(setPrRefAt([pr('#212'), pr('#219')], 1, '#231')).toEqual([pr('#212'), pr('#231')])
  })

  it('leaves the row on the state it was already on', () => {
    expect(setPrRefAt([pr('#212', 'merged')], 0, '#231')).toEqual([pr('#231', 'merged')])
  })

  it('leaves the list alone when there is no such row', () => {
    expect(setPrRefAt([pr('#212')], 3, '#231')).toEqual([pr('#212')])
  })

  it('allows a row to be emptied, which is how it is cleared before removing', () => {
    expect(setPrRefAt([pr('#212')], 0, '')).toEqual([pr('')])
  })
})

describe('setPrStateAt', () => {
  it('moves the row named along and no other', () => {
    expect(setPrStateAt([pr('#212'), pr('#219')], 0, 'merged')).toEqual([
      pr('#212', 'merged'),
      pr('#219'),
    ])
  })

  it('leaves the address alone', () => {
    const link = 'https://github.com/acme/atlas/pull/219'
    expect(setPrStateAt([pr(link)], 0, 'approved')).toEqual([pr(link, 'approved')])
  })

  it('leaves the list alone when there is no such row', () => {
    expect(setPrStateAt([pr('#212')], 2, 'merged')).toEqual([pr('#212')])
  })

  it('does not disturb what is already there', () => {
    const refs = [pr('#212')]
    setPrStateAt(refs, 0, 'merged')
    expect(refs).toEqual([pr('#212')])
  })
})

describe('samePrRefs', () => {
  it('is true for the same references', () => {
    expect(samePrRefs([pr('#212'), pr('#219')], [pr('#212'), pr('#219')])).toBe(true)
  })

  it('ignores an empty row somebody added and left', () => {
    expect(samePrRefs([pr('#212')], [pr('#212'), pr('')])).toBe(true)
  })

  it('ignores spacing', () => {
    expect(samePrRefs([pr('#212')], [pr(' #212 ')])).toBe(true)
  })

  it('is false when one is added', () => {
    expect(samePrRefs([pr('#212')], [pr('#212'), pr('#219')])).toBe(false)
  })

  it('is false when one is removed', () => {
    expect(samePrRefs([pr('#212'), pr('#219')], [pr('#212')])).toBe(false)
  })

  it('is false when one has moved along, which is the edit CYLIST-76 added', () => {
    expect(samePrRefs([pr('#212')], [pr('#212', 'merged')])).toBe(false)
  })

  it('is false when they are in a different order, which is a real edit', () => {
    expect(samePrRefs([pr('#212'), pr('#219')], [pr('#219'), pr('#212')])).toBe(false)
  })
})
