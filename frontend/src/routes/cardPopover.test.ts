/**
 * When the answer popover under a board card opens and closes, and where it
 * lands. A popover that will not open for a keyboard, or that opens over
 * every card a drag passes, is a wrong answer nobody files a bug for.
 */

import { describe, expect, it } from 'vitest'

import {
  isPopoverOpen,
  placePopover,
  POPOVER_CLOSED,
  POPOVER_OVERLAP,
  popoverReducer,
  type PopoverEvent,
  type PopoverState,
} from './cardPopover'

const ANSWERABLE = { answerable: true, dragging: false }

function run(...events: PopoverEvent[]): PopoverState {
  return events.reduce(popoverReducer, POPOVER_CLOSED)
}

const mouseOnCard: PopoverEvent = { type: 'pointerEnter', part: 'card', pointerType: 'mouse' }
const mouseOffCard: PopoverEvent = { type: 'pointerLeave', part: 'card' }
const mouseOnPopover: PopoverEvent = {
  type: 'pointerEnter',
  part: 'popover',
  pointerType: 'mouse',
}
const mouseOffPopover: PopoverEvent = { type: 'pointerLeave', part: 'popover' }

describe('the popover opening', () => {
  it('is shut to begin with', () => {
    expect(isPopoverOpen(POPOVER_CLOSED, ANSWERABLE)).toBe(false)
  })

  it('opens when a mouse is on the card', () => {
    expect(isPopoverOpen(run(mouseOnCard), ANSWERABLE)).toBe(true)
  })

  it('opens for a pen the way it does for a mouse', () => {
    const pen: PopoverEvent = { type: 'pointerEnter', part: 'card', pointerType: 'pen' }
    expect(isPopoverOpen(run(pen), ANSWERABLE)).toBe(true)
  })

  it('never opens for a finger: a tap opens the card and its agent panel instead', () => {
    const finger: PopoverEvent = { type: 'pointerEnter', part: 'card', pointerType: 'touch' }
    expect(isPopoverOpen(run(finger), ANSWERABLE)).toBe(false)
  })

  it('opens when the card is reached from the keyboard', () => {
    expect(isPopoverOpen(run({ type: 'focusIn', part: 'card', keyboard: true }), ANSWERABLE)).toBe(
      true,
    )
  })

  it('does not open when a click focused the card — the click is opening the task', () => {
    expect(isPopoverOpen(run({ type: 'focusIn', part: 'card', keyboard: false }), ANSWERABLE)).toBe(
      false,
    )
  })

  it('only opens on a card with something to answer', () => {
    expect(isPopoverOpen(run(mouseOnCard), { answerable: false, dragging: false })).toBe(false)
  })

  it('never opens during a drag', () => {
    expect(isPopoverOpen(run(mouseOnCard), { answerable: true, dragging: true })).toBe(false)
  })
})

describe('the popover staying open', () => {
  it('stays open while the pointer moves from the card onto it', () => {
    expect(isPopoverOpen(run(mouseOnCard, mouseOnPopover, mouseOffCard), ANSWERABLE)).toBe(true)
  })

  it('stays open while focus is inside it, wherever the pointer went', () => {
    const state = run(
      mouseOnCard,
      { type: 'focusIn', part: 'popover', keyboard: false },
      mouseOffCard,
    )
    expect(isPopoverOpen(state, ANSWERABLE)).toBe(true)
  })

  it('shuts once the pointer has left both and focus is elsewhere', () => {
    expect(
      isPopoverOpen(run(mouseOnCard, mouseOnPopover, mouseOffCard, mouseOffPopover), ANSWERABLE),
    ).toBe(false)
    expect(
      isPopoverOpen(
        run({ type: 'focusIn', part: 'card', keyboard: true }, { type: 'focusOut' }),
        ANSWERABLE,
      ),
    ).toBe(false)
  })
})

describe('the popover being dismissed', () => {
  it('shuts on Escape even with the pointer still on the card', () => {
    expect(isPopoverOpen(run(mouseOnCard, { type: 'escape' }), ANSWERABLE)).toBe(false)
  })

  it('stays shut after Escape until the reader has left and come back', () => {
    const escaped = run(mouseOnCard, { type: 'escape' })
    // Wiggling about on the same card does not undo the Escape...
    expect(isPopoverOpen(popoverReducer(escaped, mouseOnCard), ANSWERABLE)).toBe(false)
    // ...leaving it and coming back does.
    expect(
      isPopoverOpen(run(mouseOnCard, { type: 'escape' }, mouseOffCard, mouseOnCard), ANSWERABLE),
    ).toBe(true)
  })

  it('keeps an Escape from the keyboard while focus stays on the card', () => {
    const state = run(
      { type: 'focusIn', part: 'card', keyboard: true },
      { type: 'escape' },
      { type: 'focusIn', part: 'card', keyboard: true },
    )
    expect(isPopoverOpen(state, ANSWERABLE)).toBe(false)
  })

  it('shuts once answered', () => {
    expect(isPopoverOpen(run(mouseOnCard, mouseOnPopover, { type: 'answered' }), ANSWERABLE)).toBe(
      false,
    )
  })
})

describe('the popover leaving the page', () => {
  it('opens again for the next question after one was answered with a click', () => {
    // The click lands on the popover, which then unmounts under the pointer:
    // no pointerleave ever arrives for it.
    const answered = run(mouseOnCard, mouseOnPopover, mouseOffCard, { type: 'answered' })
    const gone = popoverReducer(answered, { type: 'popoverGone', focusOnCard: false })
    expect(isPopoverOpen(gone, ANSWERABLE)).toBe(false)
    expect(isPopoverOpen(popoverReducer(gone, mouseOnCard), ANSWERABLE)).toBe(true)
  })

  it('forgets focus that was inside it unless it went back to the card', () => {
    const inside = run(mouseOnCard, { type: 'focusIn', part: 'popover', keyboard: true })
    expect(popoverReducer(inside, { type: 'popoverGone', focusOnCard: false }).focused).toBe(false)
    expect(popoverReducer(inside, { type: 'popoverGone', focusOnCard: true }).focused).toBe(true)
  })
})

describe('placePopover', () => {
  const viewport = { width: 1280, height: 800 }

  it('hangs under the card, as wide as it, tucked under its edge', () => {
    const card = { top: 100, left: 300, width: 320, height: 120 }
    expect(placePopover(card, 200, viewport)).toEqual({
      top: 220 - POPOVER_OVERLAP,
      left: 300,
      width: 320,
      side: 'below',
    })
  })

  it('is never narrower than its choices need', () => {
    const card = { top: 100, left: 300, width: 200, height: 120 }
    expect(placePopover(card, 200, viewport).width).toBe(280)
  })

  it('stays inside the window at a phone width', () => {
    const phone = { width: 400, height: 800 }
    const card = { top: 100, left: 200, width: 300, height: 120 }
    const placed = placePopover(card, 200, phone)
    expect(placed.left + placed.width).toBeLessThanOrEqual(400 - 8)
    expect(placed.left).toBeGreaterThanOrEqual(8)
  })

  it('goes above a card near the foot of the window', () => {
    const card = { top: 600, left: 300, width: 320, height: 120 }
    expect(placePopover(card, 300, viewport)).toEqual({
      top: 600 + POPOVER_OVERLAP - 300,
      left: 300,
      width: 320,
      side: 'above',
    })
  })

  it('stays below when there is less room above than below', () => {
    const card = { top: 40, left: 300, width: 320, height: 600 }
    expect(placePopover(card, 400, viewport).side).toBe('below')
  })
})
