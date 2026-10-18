/**
 * Whose cards the board is showing (CYLIST-99): what a card's owners are, how
 * many cards each person owns, and how the pick is written into the URL.
 */

import type { Person, Task } from '../../api/client'

/**
 * Everybody a card answers to in the people filter: its owner, and whoever
 * owns one of its sub-tasks. A sub-task is not on the board, so a person whose
 * only share of a card is a sub-task would otherwise have no way to find it.
 */
export function ownersOf(task: Task): Person[] {
  return [task.assignee, ...task.subtask_assignees]
}

/** Whether a card belongs to any of the picked people. An empty pick is everyone. */
export function ownedByAny(task: Task, people: readonly string[]): boolean {
  if (!people.length) return true
  return ownersOf(task).some((owner) => people.includes(owner.id))
}

/**
 * How many of these cards each person owns, by person id — counted the way
 * the filter matches, so a card with three people on it counts once for each.
 */
export function cardsOwned(tasks: Task[]): Map<string, number> {
  const tally = new Map<string, number>()
  for (const task of tasks) {
    for (const id of new Set(ownersOf(task).map((owner) => owner.id))) {
      tally.set(id, (tally.get(id) ?? 0) + 1)
    }
  }
  return tally
}

/**
 * `?who=` for the whole board. Without it, no param means "you", so the board
 * needs a word for having been asked for everybody.
 */
export const EVERYONE = 'everyone'

/**
 * The pick a board opens on, from its `?who=`.
 *
 * Absent, it is you — if you are on this project; somebody who is not would
 * otherwise open every board they visit onto nothing. `everyone` is the empty
 * pick, and otherwise it is a comma-separated list of person ids, kept as
 * given: a link shared by somebody else shows the people it was shared with.
 */
export function peopleFromSearch(
  who: string | undefined,
  meId: string | null,
  members: Person[],
): string[] {
  if (who === undefined) {
    return meId !== null && members.some((member) => member.id === meId) ? [meId] : []
  }
  if (who === EVERYONE) return []
  return [...new Set(who.split(',').filter(Boolean))]
}

/** The `?who=` that says this pick — the inverse of `peopleFromSearch`. */
export function searchFromPeople(people: readonly string[]): string {
  return people.length ? people.join(',') : EVERYONE
}

/**
 * The face row's order: you first, then whoever owns the most cards on the
 * board, then everybody else by name.
 *
 * Counted off every card rather than the narrowed ones, so typing in the
 * search box does not shuffle the faces under the pointer.
 */
export function facesInOrder(
  members: Person[],
  owned: Map<string, number>,
  meId: string | null,
): Person[] {
  return [...members].sort(
    (a, b) =>
      Number(b.id === meId) - Number(a.id === meId) ||
      (owned.get(b.id) ?? 0) - (owned.get(a.id) ?? 0) ||
      a.name.localeCompare(b.name),
  )
}
