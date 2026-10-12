/**
 * What a card on the board can do without being opened, handed down to every
 * card from the board itself.
 *
 * A context rather than props because a card sits four components below the
 * board — column, section, list, card — and none of the ones in between does
 * anything with these but pass them on. Threaded by hand, every one of them
 * carried the card's whole interface in its own, and the column's heading,
 * which draws no cards at all, had to be handed do-nothing versions to
 * satisfy it.
 */

import { createContext, useContext } from 'react'
import type { Person } from '../../api/client'

export interface CardActions {
  /** The project the board is for, so a card can name its board's queries. */
  projectKey: string
  /** The project's people, for the assignee picker and the `@` tags in a stage label. */
  members: Person[]
  openTask: (taskId: string) => void
  moveSubStatus: (taskId: string, index: number) => void
  /** Hand a card to someone else, by their id, from the board itself. */
  setAssignee: (taskId: string, taskRef: string, assigneeId: string) => void
}

export const CardActionsContext = createContext<CardActions | null>(null)

export function useCardActions(): CardActions {
  const actions = useContext(CardActionsContext)
  // A card drawn outside a board is a programming error rather than a state
  // the page can be in, so it is said loudly instead of drawn half-working.
  if (!actions) {
    throw new Error('A board card was drawn outside the board that provides its actions.')
  }
  return actions
}
