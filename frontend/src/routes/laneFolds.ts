/**
 * Which lanes a quick filter folds away, and which the reader folded.
 *
 * A filtered board is a board most of whose lanes are empty: pick one goal out
 * of the dropdown and every other lane is a heading over a row of nothing,
 * with the cards you asked for pushed a screen down. So while a filter is on,
 * the filter decides the folds — a lane with a match is open, a lane without
 * one is folded down to its heading, and the reader's own folds are set aside
 * rather than overwritten, waiting for the filter to be cleared.
 *
 * Set aside, not ignored: a fold is still a gesture the reader is allowed to
 * make on a filtered board. Those flips are kept apart from the remembered
 * folds as a list of lanes whose answer is the opposite of the filter's, which
 * is what makes the remembered list survive filtering untouched — and what
 * makes the flips themselves worth throwing away the moment the filter
 * changes, since they were answers to a question no longer being asked.
 */
export interface LaneFolds {
  /** Whether a quick filter is narrowing the board at all. */
  filtering: boolean
  /** The lanes the reader has folded, remembered across visits. */
  folded: readonly string[]
  /** The lanes whose fold has been flipped since the filter was last set. */
  flipped: readonly string[]
}

/** A lane, as the folding cares about it: a name, and whether anything is in it. */
export interface LaneFill {
  key: string
  empty: boolean
}

/** Whether a lane is folded away right now, filter and reader both counted. */
export function laneFolded(lane: LaneFill, folds: LaneFolds): boolean {
  if (!folds.filtering) return folds.folded.includes(lane.key)
  return folds.flipped.includes(lane.key) ? !lane.empty : lane.empty
}

/** The same list with one lane's fold flipped — added if absent, dropped if not. */
export function flip(keys: readonly string[], key: string): string[] {
  return keys.includes(key) ? keys.filter((one) => one !== key) : [...keys, key]
}

/**
 * The flips that fold every lane away, or open every one of them back up.
 *
 * "Fold all" on a filtered board cannot just list every lane the way it does
 * off one, because the filter has already answered for each of them: a lane
 * needs a flip only where the filter's answer is not the one being asked for.
 */
export function flipsForAll(lanes: readonly LaneFill[], folded: boolean): string[] {
  return lanes.filter((lane) => lane.empty !== folded).map((lane) => lane.key)
}
