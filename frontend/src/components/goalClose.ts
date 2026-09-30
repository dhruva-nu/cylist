/**
 * What the button that closes a goal offers, and whether it may be pressed.
 *
 * Kept apart from the page that draws it because the interesting part is not
 * the button: it is the rule the server enforces — a goal cannot be achieved
 * while a card on it is still open — said in the interface before the request
 * is made rather than after it comes back 422. The counts that rule reads are
 * already on the goal, so the page can answer the question itself, and a
 * button that explains why it is held beats one that refuses when pressed.
 *
 * Dropping a goal is deliberately not here. Giving up on a goal is a different
 * decision from finishing one, it is never refused, and it belongs on the form
 * with the rest of what a goal is rather than as a second button beside this.
 */

import type { Goal, GoalStatus } from '../api/client'

export interface GoalClosure {
  /** The status the button writes. */
  next: GoalStatus
  /** What the button says. */
  label: string
  /** The whole sentence, for the button's tooltip and its accessible name. */
  description: string
  /** Why the button is held, or null when it may be pressed. */
  refusal: string | null
  /** What is said once the goal has moved. */
  announcement: string
}

/**
 * The one state change this button makes, for the goal as it currently stands.
 *
 * An open goal closes; a settled one — achieved or dropped alike — reopens.
 * One button rather than three, because at any moment there is only one thing
 * a reader wants from it, and a row of statuses to choose between is the
 * dropdown on the form that this exists to save them opening.
 */
export function goalClosure(goal: Goal): GoalClosure {
  if (goal.status !== 'open') {
    return {
      next: 'open',
      label: 'Reopen',
      description: `Reopen ${goal.name} and put it back among the live goals`,
      refusal: null,
      announcement: `${goal.name} is open again.`,
    }
  }

  return {
    next: 'achieved',
    label: 'Mark achieved',
    description: `Close ${goal.name} — the work under it is done`,
    refusal: openCardsRefusal(goal),
    announcement: `${goal.name} marked achieved.`,
  }
}

/**
 * Why an open goal cannot be closed yet, in the server's own terms.
 *
 * `progress.open` is the same count `_refuse_open_cards` refuses on: cards
 * neither cancelled nor in the board's last column. A goal with no cards at
 * all is not held — an epic somebody decided against writing cards for is
 * still an epic they may declare finished.
 */
function openCardsRefusal(goal: Goal): string | null {
  const { open } = goal.progress
  if (open === 0) return null
  const cards = open === 1 ? '1 card is' : `${open} cards are`
  const them = open === 1 ? 'it' : 'each of them'
  return `${cards} still open on this goal. Finish or cancel ${them} — or take ${them} off the goal — before marking it achieved.`
}
