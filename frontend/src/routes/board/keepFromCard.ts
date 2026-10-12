/**
 * Stopping an event at a control inside a card, before it reaches the card.
 *
 * The card is both dnd-kit's drag handle and the button that opens the task,
 * so a pointer-down left to bubble starts a drag, and a key left to bubble
 * either opens the dialog (Enter) or drives a drag (Space, the arrows) — where
 * the reader was only using the control.
 */
export function stopAtCard(event: { stopPropagation: () => void }) {
  event.stopPropagation()
}

/** The two events every control on a card keeps to itself. */
export const keepFromCard = { onPointerDown: stopAtCard, onKeyDown: stopAtCard }
