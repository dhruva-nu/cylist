/**
 * How this reader has chosen to look at a board: what is folded away, whether
 * the board is in lanes, whether the cancelled cards are shown, and which
 * columns have opened their on-hold cards.
 *
 * All of it is remembered per project rather than board-wide: folding "Done"
 * away on one project says nothing about what you want to see on another. It
 * is only ever a way of reading the board — nothing about the cards changes.
 */

import { useCallback, useEffect, useState } from 'react'
import { readStored, writeStored } from '../../storage'

/**
 * A piece of state kept in localStorage under a key that carries the project.
 *
 * The key travels with the value it was read for, so walking from one board
 * to another re-reads the new board's answer instead of carrying the last
 * one's across — and never writes the old board's value under the new key on
 * the way. The re-read happens during render, which is React's own pattern
 * for state that has to follow a prop, rather than in an effect that would
 * draw the stale value first.
 *
 * Read and written through `storage.ts`, which never throws: a board that
 * will not render is a worse outcome than one that forgets a preference.
 */
function useStoredState<T>(
  storageKey: string,
  parse: (stored: string | null) => T,
): [T, (update: (current: T) => T) => void] {
  const read = useCallback((key: string): T => parse(readStored(key)), [parse])

  const [stored, setStored] = useState(() => ({ key: storageKey, value: read(storageKey) }))
  if (stored.key !== storageKey) setStored({ key: storageKey, value: read(storageKey) })

  useEffect(() => writeStored(stored.key, JSON.stringify(stored.value)), [stored])

  const update = useCallback((change: (current: T) => T) => {
    setStored((current) => ({ key: current.key, value: change(current.value) }))
  }, [])

  return [stored.value, update]
}

function parseIdList(stored: string | null): string[] {
  try {
    const parsed: unknown = JSON.parse(stored ?? '')
    return Array.isArray(parsed) ? parsed.filter((id): id is string => typeof id === 'string') : []
  } catch {
    return []
  }
}

function parseFlag(stored: string | null): boolean {
  return stored === 'true'
}

/**
 * A list of ids, remembered per project — what is folded away.
 *
 * Several things fold on this board — a column, a section of one, a lane, a
 * column's on-hold foot — and they are all the same list of ids under
 * different storage keys.
 */
function useFolded(storageKey: string) {
  const [folded, update] = useStoredState(storageKey, parseIdList)

  const toggle = useCallback(
    (id: string) => {
      update((current) =>
        current.includes(id) ? current.filter((one) => one !== id) : [...current, id],
      )
    },
    [update],
  )

  const setFolded = useCallback((ids: string[]) => update(() => ids), [update])

  return { folded, setFolded, toggle }
}

/** One way of looking at a board, on or off — whether it is in lanes, say. Off by default. */
function useBoardToggle(storageKey: string): [boolean, (on: boolean) => void] {
  const [on, update] = useStoredState(storageKey, parseFlag)
  const setOn = useCallback((next: boolean) => update(() => next), [update])
  return [on, setOn]
}

export function useBoardView(projectKey: string) {
  const { folded: collapsedColumns, toggle: toggleColumn } = useFolded(
    `cylist.board.collapsed.${projectKey}`,
  )
  /**
   * Which outcome sections are folded away, keyed by `${columnId}:${index}` —
   * an id built the same way a section's drop target is, so a fold survives
   * exactly what the section itself survives: a rename, another card landing
   * in it, anything short of the section no longer existing.
   */
  const { folded: collapsedOutcomes, toggle: toggleOutcome } = useFolded(
    `cylist.board.collapsedOutcomes.${projectKey}`,
  )
  /**
   * Which lanes are folded away, when the board is in lanes.
   *
   * The same fold a column has, turned ninety degrees: a board split by goal
   * is as tall as the sum of its goals, and the goal you are not working on
   * this week is a screen of scrolling between the two you are. A folded lane
   * keeps its heading — the name and the count are the part you still need —
   * and drops its row of cells.
   *
   * A folded lane is not a drop target, which is the one way it differs from a
   * folded column. A column keeps a rail you can drop onto because a card put
   * into "Done" is still a card put somewhere definite; a lane's cells are one
   * goal's share of every column at once, and there is no single place a card
   * dropped on a folded lane could honestly land.
   */
  const {
    folded: foldedLanes,
    setFolded: setFoldedLanes,
    toggle: toggleLane,
  } = useFolded(`cylist.board.lanes.folded.${projectKey}`)
  const [lanesOn, setLanesOn] = useBoardToggle(`cylist.board.lanes.${projectKey}`)
  /**
   * Whether the cancelled cards have been asked back onto the board.
   *
   * Stored the way round it is because the hide is the default (CYLIST-62):
   * work that is not going to happen is not what a board is for, and a board
   * carrying a year of it is one you would otherwise hide them on every visit.
   * Off is therefore the ordinary value, and `useBoardToggle` starts off.
   */
  const [showCancelled, setShowCancelled] = useBoardToggle(`cylist.board.cancelled.${projectKey}`)
  /**
   * Which columns have been opened at the foot to show the cards on hold.
   *
   * A list of the columns showing them rather than of the columns hiding them,
   * so the empty list — a reader who has never touched this — is every column
   * holding its held work back, which is the default the card asked for.
   *
   * Kept per column, and shared by a column's lane cells: in lanes a column is
   * drawn once per goal, and opening the foot is a question about the column
   * ("what has stalled in review?"), not about one goal's corner of it.
   */
  const { folded: holdShown, toggle: toggleHold } = useFolded(
    `cylist.board.holdShown.${projectKey}`,
  )

  return {
    collapsedColumns,
    toggleColumn,
    collapsedOutcomes,
    toggleOutcome,
    foldedLanes,
    setFoldedLanes,
    toggleLane,
    lanesOn,
    setLanesOn,
    showCancelled,
    setShowCancelled,
    holdShown,
    toggleHold,
  }
}
