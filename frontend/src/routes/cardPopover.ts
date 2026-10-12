/**
 * When the answer popover under a board card is open, and where it goes
 * (CYLIST-86).
 *
 * The card asks for no button: the popover opens when the pointer is on a
 * card whose agent is waiting on a question or a permission. Hover alone
 * shuts out two kinds of reader, so each gets its own rule:
 *
 * - **Keyboard.** Focusing the card from the keyboard opens it, the way
 *   `:focus-within` does in the design, and the popover sits straight after
 *   the card in the document, so Tab walks from the card into its choices
 *   and on to the next card. Escape closes it and puts focus back on the
 *   card. A *click* also focuses the card, but a click is a pointer, so focus
 *   that did not come from the keyboard (`:focus-visible` false) does not
 *   open it.
 * - **Touch.** There is no hover on glass, and a tap on a card already
 *   means "open it" — so a touch pointer never opens the popover, and the tap
 *   opens the task, whose agent panel holds the same question and the same
 *   buttons (CYLIST-87).
 *
 * Kept as a reducer over plain values so every one of those rules is a test
 * rather than a hope; the component does no more than feed it events.
 */

export type PopoverPart = 'card' | 'popover'

export interface PopoverState {
  /** The pointer is over the card. */
  overCard: boolean
  /** The pointer is over the popover. */
  overPopover: boolean
  /** Focus is on the card (from the keyboard) or anywhere inside the popover. */
  focused: boolean
  /**
   * Closed by Escape or by answering, and kept closed until the reader has
   * left both the card and the popover — otherwise the pointer still resting
   * on the card would open it again the moment it shut.
   */
  dismissed: boolean
}

export const POPOVER_CLOSED: PopoverState = {
  overCard: false,
  overPopover: false,
  focused: false,
  dismissed: false,
}

export type PopoverEvent =
  | { type: 'pointerEnter'; part: PopoverPart; pointerType: string }
  | { type: 'pointerLeave'; part: PopoverPart }
  /** `keyboard` is whether the element focused matches `:focus-visible`. */
  | { type: 'focusIn'; part: PopoverPart; keyboard: boolean }
  /** Focus went somewhere outside both the card and the popover. */
  | { type: 'focusOut' }
  | { type: 'escape' }
  | { type: 'answered' }
  /**
   * The popover left the page — answered, dismissed, answered somewhere else,
   * or a drag began. An element taken out from under the pointer never hears
   * the pointer leave, nor reliably loses focus, so this says it for it;
   * without it a popover answered with a click would believe the pointer was
   * still on it forever, and never open again. `focusOnCard` is whether focus
   * landed back on the card rather than nowhere.
   */
  | { type: 'popoverGone'; focusOnCard: boolean }

/**
 * How long the pointer may be off both before the popover shuts: enough to
 * cross from the card to the popover on a diagonal without it closing under
 * you. The component holds the timer; the reducer only hears the leave once
 * the timer has run.
 */
export const POPOVER_LEAVE_GRACE_MS = 150

export function popoverReducer(state: PopoverState, event: PopoverEvent): PopoverState {
  const next = step(state, event)
  // Once the reader has left everything, a dismissal has done its job.
  if (next.dismissed && !next.overCard && !next.overPopover && !next.focused) {
    return { ...next, dismissed: false }
  }
  return next
}

function step(state: PopoverState, event: PopoverEvent): PopoverState {
  switch (event.type) {
    case 'pointerEnter':
      // A finger has no hover; its tap opens the card instead.
      if (event.pointerType === 'touch') return state
      return event.part === 'card' ? { ...state, overCard: true } : { ...state, overPopover: true }
    case 'pointerLeave':
      return event.part === 'card'
        ? { ...state, overCard: false }
        : { ...state, overPopover: false }
    case 'focusIn':
      // Anything focused inside the popover is somebody using it. On the card
      // itself only keyboard focus counts — a click focuses it too, and that
      // click is opening the task.
      if (event.part === 'popover') return { ...state, focused: true }
      return event.keyboard ? { ...state, focused: true } : state
    case 'focusOut':
      return { ...state, focused: false }
    case 'escape':
    case 'answered':
      return { ...state, dismissed: true }
    case 'popoverGone':
      return { ...state, overPopover: false, focused: state.focused && event.focusOnCard }
  }
}

/** Whether to draw it. */
export function isPopoverOpen(
  state: PopoverState,
  { answerable, dragging }: { answerable: boolean; dragging: boolean },
): boolean {
  // Never over a drag: a card in the air, or the cards it passes over, are
  // not being read.
  if (!answerable || dragging || state.dismissed) return false
  return state.overCard || state.overPopover || state.focused
}

export interface Box {
  top: number
  left: number
  width: number
  height: number
}

export interface Placement {
  top: number
  left: number
  width: number
  /** Below the card, or above it when there is no room below. */
  side: 'below' | 'above'
}

/** The gutter kept between the popover and the edge of the window. */
const EDGE = 8
/** Narrower than this and the choices wrap mid-word. */
const MIN_WIDTH = 280
/**
 * How far the popover tucks under the card. Its top padding is transparent
 * and at least this deep, so there is no gap between the two for the pointer
 * to fall through.
 */
export const POPOVER_OVERLAP = 6

/**
 * Where the popover goes: under the card, as wide as it (or a little wider
 * for a narrow card), kept inside the window, and flipped above the card
 * when the window has no room under it but has above.
 */
export function placePopover(
  card: Box,
  popoverHeight: number,
  viewport: { width: number; height: number },
): Placement {
  const width = Math.min(Math.max(card.width, MIN_WIDTH), viewport.width - EDGE * 2)
  const left = Math.min(Math.max(card.left, EDGE), viewport.width - EDGE - width)
  const below = card.top + card.height - POPOVER_OVERLAP
  const roomBelow = viewport.height - EDGE - below
  const roomAbove = card.top + POPOVER_OVERLAP - EDGE
  if (popoverHeight > roomBelow && roomAbove > roomBelow) {
    return { top: card.top + POPOVER_OVERLAP - popoverHeight, left, width, side: 'above' }
  }
  return { top: below, left, width, side: 'below' }
}
