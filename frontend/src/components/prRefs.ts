/**
 * The list of pull requests on a card, as the dialog edits it.
 *
 * A card names several pull requests more often than it names one: a change
 * that took a backend pull request and the frontend one that calls it is still
 * one card. The list is edited in place and sent whole — adding one is a longer
 * list, removing one a shorter list — so the only logic worth naming is what
 * happens to the rest of the list when one row moves, which is exactly the
 * logic that goes wrong quietly.
 *
 * Kept out of the component and out of the API client because both ends need
 * it: the editor calls it on every keystroke, and `taskPayload` calls it again
 * on the way out so that what is saved is what the user is left looking at.
 */

/**
 * The references worth sending: trimmed, blanks dropped, duplicates dropped.
 *
 * Blank rather than refused, because a row somebody added and has not typed
 * into yet is not a mistake to block a save over — it is the state every new
 * row starts in. Duplicates keep the first, because the same pull request
 * named twice is one pull request, and showing it twice on the card would only
 * invite the reader to look for a difference between them.
 *
 * Order is kept: it is the order somebody put them in, and a card's first pull
 * request is usually its main one.
 *
 * This mirrors `_clean_pr_refs` in `app/schemas/tasks.py`, which is what
 * actually enforces it — the server would clean a list this missed. Doing it
 * here too is what stops the dialog showing a row that the save then removes.
 */
export function cleanPrRefs(refs: string[]): string[] {
  const kept: string[] = []
  for (const ref of refs) {
    const trimmed = ref.trim()
    if (trimmed && !kept.includes(trimmed)) kept.push(trimmed)
  }
  return kept
}

/**
 * The list with an empty row on the end, ready to be typed into.
 *
 * Empty rather than prompting for the reference first: the row *is* the
 * prompt, and it is where the cursor goes.
 */
export function addPrRef(refs: string[]): string[] {
  return [...refs, '']
}

/** The list without the row at `index`, and unchanged if there is none. */
export function removePrRefAt(refs: string[], index: number): string[] {
  return refs.filter((_, position) => position !== index)
}

/** The list with the row at `index` retyped, and unchanged if there is none. */
export function setPrRefAt(refs: string[], index: number, value: string): string[] {
  if (index < 0 || index >= refs.length) return refs
  return refs.map((ref, position) => (position === index ? value : ref))
}

/**
 * Whether two lists say the same thing, for deciding there is nothing to save.
 *
 * Compared after cleaning, so retyping a reference with a trailing space, or
 * adding a row and leaving it empty, is correctly nothing at all.
 */
export function samePrRefs(a: string[], b: string[]): boolean {
  const left = cleanPrRefs(a)
  const right = cleanPrRefs(b)
  return left.length === right.length && left.every((ref, index) => ref === right[index])
}
