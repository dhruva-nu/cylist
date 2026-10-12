/**
 * Dragging from the keyboard.
 *
 * A focused card is picked up with space and walked between columns with the
 * arrow keys, which is the same operation as a pointer drag through the same
 * code path — only the keys and the steps are decided here.
 */

import type {
  ClientRect,
  KeyboardCodes,
  KeyboardCoordinateGetter,
  ScreenReaderInstructions,
  UniqueIdentifier,
} from '@dnd-kit/core'

/**
 * Which keys drive a keyboard drag.
 *
 * Enter is deliberately not among them, though dnd-kit offers it by default:
 * Enter is what opens a card, and a card that cannot be opened from the
 * keyboard is a worse trade than one that cannot be dragged. Space does both
 * ends of the drag instead.
 *
 * Tab cancels rather than finishes. dnd-kit's default is to drop on Tab, which
 * means leaving the board mid-drag quietly commits a move nobody asked for;
 * cancelling still rules out the other failure, a drag left running after
 * focus has gone somewhere else.
 */
export const DRAG_KEYS: KeyboardCodes = {
  start: ['Space'],
  cancel: ['Escape', 'Tab'],
  end: ['Space'],
}

export const SCREEN_READER_INSTRUCTIONS: ScreenReaderInstructions = {
  draggable:
    'Press enter to open this card. Press space to pick it up, then use the left and right ' +
    'arrow keys to move it between columns. Press space again to drop it, or escape to leave ' +
    'it where it was.',
}

/** The column nearest a point, for when nothing is being hovered yet. */
function nearestColumn(columns: [UniqueIdentifier, ClientRect][], centre: number): number {
  let best = 0
  let bestDistance = Number.POSITIVE_INFINITY

  columns.forEach(([, rect], index) => {
    const distance = Math.abs(rect.left + rect.width / 2 - centre)
    if (distance < bestDistance) {
      bestDistance = distance
      best = index
    }
  })

  return best
}

/** Which way an arrow key walks a picked-up card: a column right, a column left, or nowhere. */
function columnStepFor(code: string): number {
  if (code === 'ArrowRight') return 1
  if (code === 'ArrowLeft') return -1
  return 0
}

/**
 * Move a picked-up card one whole column per key press.
 *
 * dnd-kit's stock getter nudges 25 pixels an arrow, which is a dozen presses
 * to cross a 300-pixel column and leaves the card wherever the twelfth one
 * happened to land. A board has one axis worth travelling and a small number
 * of places to stop on it, so left and right jump to the next column's centre
 * and up and down do nothing at all.
 */
export const moveByColumn: KeyboardCoordinateGetter = (event, { context, currentCoordinates }) => {
  const step = columnStepFor(event.code)
  if (step === 0) return

  const { collisionRect, droppableContainers, droppableRects, over } = context
  if (!collisionRect) return

  const columns = [...droppableRects]
    .filter(([id]) => droppableContainers.get(id)?.disabled !== true)
    .sort(([, left], [, right]) => left.left - right.left)
  if (columns.length === 0) return

  const hovered = columns.findIndex(([id]) => id === over?.id)
  const from =
    hovered >= 0 ? hovered : nearestColumn(columns, collisionRect.left + collisionRect.width / 2)
  const to = Math.min(columns.length - 1, Math.max(0, from + step))
  if (to === from) return

  const target = columns[to]
  if (!target) return

  const [, rect] = target
  // Centred on the target, so the collision detection has an unambiguous
  // winner however wide the card happens to be.
  return { x: rect.left + (rect.width - collisionRect.width) / 2, y: currentCoordinates.y }
}
