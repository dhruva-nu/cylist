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
 * Each row is a URL *and* a state (CYLIST-76), which is what makes that
 * arithmetic worth pinning down twice over: removing the second of three and
 * getting the first two back looks, on screen, exactly like removing the third
 * — and now the survivors carry something that can be left behind on the wrong
 * row.
 *
 * Kept out of the component and out of the API client because both ends need
 * it: the editor calls it on every keystroke, and `taskPayload` calls it again
 * on the way out so that what is saved is what the user is left looking at.
 */

import type { PrRef, PrState } from '../api/client'

/**
 * The state a pull request starts in, and so the state a new row starts in.
 *
 * Here rather than inline so the editor, the form's defaults and the tests all
 * read the same word for it. The server applies the same default to an entry
 * that arrives without one.
 */
export const NEW_PR_STATE: PrState = 'raised'

/**
 * The references worth sending: trimmed, blanks dropped, duplicates dropped.
 *
 * Blank rather than refused, because a row somebody added and has not typed
 * into yet is not a mistake to block a save over — it is the state every new
 * row starts in. Duplicates keep the first, because the same pull request
 * named twice is one pull request, and showing it twice on the card would only
 * invite the reader to look for a difference between them — which, with a
 * state on each, there could genuinely be. The first's state is the one kept,
 * the same way its position is.
 *
 * Order is kept: it is the order somebody put them in, and a card's first pull
 * request is usually its main one.
 *
 * This mirrors `_clean_pr_refs` in `app/schemas/tasks.py`, which is what
 * actually enforces it — the server would clean a list this missed. Doing it
 * here too is what stops the dialog showing a row that the save then removes.
 */
export function cleanPrRefs(refs: PrRef[]): PrRef[] {
  const kept: PrRef[] = []
  for (const ref of refs) {
    const url = ref.url.trim()
    if (!url || kept.some((other) => other.url === url)) continue
    kept.push({ url, state: ref.state })
  }
  return kept
}

/**
 * The list with an empty row on the end, ready to be typed into.
 *
 * Empty rather than prompting for the reference first: the row *is* the
 * prompt, and it is where the cursor goes. It starts at `raised` because that
 * is where a pull request starts — a row that opened on no state would be a
 * second question asked of somebody who has only pasted a link.
 */
export function addPrRef(refs: PrRef[]): PrRef[] {
  return [...refs, { url: '', state: NEW_PR_STATE }]
}

/** The list without the row at `index`, and unchanged if there is none. */
export function removePrRefAt(refs: PrRef[], index: number): PrRef[] {
  return refs.filter((_, position) => position !== index)
}

/** The list with the row at `index` retyped, and unchanged if there is none. */
export function setPrRefAt(refs: PrRef[], index: number, url: string): PrRef[] {
  if (index < 0 || index >= refs.length) return refs
  return refs.map((ref, position) => (position === index ? { ...ref, url } : ref))
}

/**
 * The list with the row at `index` moved along, and unchanged if there is none.
 *
 * Separate from `setPrRefAt` rather than one function taking a whole row: the
 * two gestures are a person typing an address and a person saying a pull
 * request has landed, they happen in different places — the editor, and the
 * card's own detail view — and only one of them has to survive a keystroke.
 */
export function setPrStateAt(refs: PrRef[], index: number, state: PrState): PrRef[] {
  if (index < 0 || index >= refs.length) return refs
  return refs.map((ref, position) => (position === index ? { ...ref, state } : ref))
}

/**
 * Whether two lists say the same thing, for deciding there is nothing to save.
 *
 * Compared after cleaning, so retyping a reference with a trailing space, or
 * adding a row and leaving it empty, is correctly nothing at all. A state that
 * changed is a real edit, the same as an address that did.
 */
export function samePrRefs(a: PrRef[], b: PrRef[]): boolean {
  const left = cleanPrRefs(a)
  const right = cleanPrRefs(b)
  return (
    left.length === right.length &&
    left.every((ref, index) => ref.url === right[index]?.url && ref.state === right[index]?.state)
  )
}

/** The order a pull request moves through, earliest first. */
const PR_PROGRESS: readonly PrState[] = ['raised', 'approved', 'merged']

/**
 * The state of the pull request furthest behind, which is the one a card with
 * several of them is drawn in: a card is not merged while any of its work is
 * still waiting for review. Null on a card with none.
 */
export function leastAdvancedPrState(refs: readonly PrRef[]): PrState | null {
  if (!refs.length) return null
  return refs.reduce<PrState>(
    (least, ref) =>
      PR_PROGRESS.indexOf(ref.state) < PR_PROGRESS.indexOf(least) ? ref.state : least,
    'merged',
  )
}
