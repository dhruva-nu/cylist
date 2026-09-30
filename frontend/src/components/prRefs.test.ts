/**
 * The pull-request list a card carries, as the dialog edits it.
 *
 * CYLIST-63. The half of this feature that can be wrong quietly is the list
 * arithmetic: removing the second of three and getting the first and second
 * back looks, on screen, exactly like removing the third. So every rule about
 * which rows survive an edit is pinned here rather than left to be noticed.
 */

import { describe, expect, it } from 'vitest'

import { addPrRef, cleanPrRefs, removePrRefAt, samePrRefs, setPrRefAt } from './prRefs'

describe('cleanPrRefs', () => {
  it('keeps the order they were given in', () => {
    expect(cleanPrRefs(['#9', '#1', '#5'])).toEqual(['#9', '#1', '#5'])
  })

  it('trims each one', () => {
    expect(cleanPrRefs(['  #212  '])).toEqual(['#212'])
  })

  it('drops a row nobody has typed into', () => {
    expect(cleanPrRefs(['#212', '', '   '])).toEqual(['#212'])
  })

  it('keeps the first of a repeated reference and drops the rest', () => {
    expect(cleanPrRefs(['#212', '#219', '#212'])).toEqual(['#212', '#219'])
  })

  it('treats a reference repeated with different spacing as the same one', () => {
    expect(cleanPrRefs(['#212', ' #212 '])).toEqual(['#212'])
  })

  it('leaves a whole URL whole', () => {
    const link = 'https://github.com/acme/atlas/pull/219'
    expect(cleanPrRefs([link])).toEqual([link])
  })

  it('is nothing at all for a list of nothing but blanks', () => {
    expect(cleanPrRefs(['', '  '])).toEqual([])
  })
})

describe('addPrRef', () => {
  it('puts an empty row on the end to type into', () => {
    expect(addPrRef(['#212'])).toEqual(['#212', ''])
  })

  it('does not disturb what is already there', () => {
    const refs = ['#212', '#219']
    addPrRef(refs)
    expect(refs).toEqual(['#212', '#219'])
  })
})

describe('removePrRefAt', () => {
  it('takes the one in the middle and closes the gap', () => {
    expect(removePrRefAt(['#212', '#219', '#231'], 1)).toEqual(['#212', '#231'])
  })

  it('takes the first', () => {
    expect(removePrRefAt(['#212', '#219'], 0)).toEqual(['#219'])
  })

  it('takes the last', () => {
    expect(removePrRefAt(['#212', '#219'], 1)).toEqual(['#212'])
  })

  it('leaves the list alone when there is no such row', () => {
    expect(removePrRefAt(['#212'], 4)).toEqual(['#212'])
  })

  it('empties a list of one', () => {
    expect(removePrRefAt(['#212'], 0)).toEqual([])
  })
})

describe('setPrRefAt', () => {
  it('retypes the row named and no other', () => {
    expect(setPrRefAt(['#212', '#219'], 1, '#231')).toEqual(['#212', '#231'])
  })

  it('leaves the list alone when there is no such row', () => {
    expect(setPrRefAt(['#212'], 3, '#231')).toEqual(['#212'])
  })

  it('allows a row to be emptied, which is how it is cleared before removing', () => {
    expect(setPrRefAt(['#212'], 0, '')).toEqual([''])
  })
})

describe('samePrRefs', () => {
  it('is true for the same references', () => {
    expect(samePrRefs(['#212', '#219'], ['#212', '#219'])).toBe(true)
  })

  it('ignores an empty row somebody added and left', () => {
    expect(samePrRefs(['#212'], ['#212', ''])).toBe(true)
  })

  it('ignores spacing', () => {
    expect(samePrRefs(['#212'], [' #212 '])).toBe(true)
  })

  it('is false when one is added', () => {
    expect(samePrRefs(['#212'], ['#212', '#219'])).toBe(false)
  })

  it('is false when one is removed', () => {
    expect(samePrRefs(['#212', '#219'], ['#212'])).toBe(false)
  })

  it('is false when they are in a different order, which is a real edit', () => {
    expect(samePrRefs(['#212', '#219'], ['#219', '#212'])).toBe(false)
  })
})
