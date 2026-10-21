/**
 * Where a card dropped into a column sorts while the move is in the air.
 *
 * A card's `position` is the server's sort key, with gaps between neighbours,
 * not its index — so the index a drop asks for cannot be written into the
 * cache as the card's `position`, or anything sorting by it would put the card
 * near the top of a column whose keys run into the thousands. The key here is
 * only drawn with: the server picks its own, and the refresh after the write
 * replaces this one.
 */

import type { Task } from '../../api/client'

/**
 * A key that sorts a card `index` cards from the top of `columnId`, among the
 * cards there other than `movingId`. An index past the bottom is the bottom.
 */
export function sortKeyAt(
  tasks: Task[],
  columnId: string,
  index: number,
  movingId: string,
): number {
  const keys = tasks
    .filter((task) => task.column_id === columnId && task.id !== movingId)
    .map((task) => task.position ?? 0)
    .sort((a, b) => a - b)
  const above = index > 0 ? keys[Math.min(index, keys.length) - 1] : undefined
  const below = keys[index]
  if (above === undefined && below === undefined) return 0
  if (above === undefined) return (below ?? 0) - 1
  if (below === undefined) return above + 1
  return (above + below) / 2
}
