/**
 * The ids the board's drag and drop speaks in.
 *
 * dnd-kit knows a drop target and a draggable by one string each, so whatever
 * a drop has to tell the board — which column, which section of a divided
 * column, whether it was a column or a card being carried — is spelled
 * into that string. Every id is built and read here, so the spelling is one
 * thing rather than a convention kept in step across a dozen components.
 *
 * A drop target's id is its column, prefixed by whatever else narrows it: the
 * section of the column it is. Every part but the last one qualifies, and the
 * column is always the last.
 */

import type { UniqueIdentifier } from '@dnd-kit/core'

const PART_SEPARATOR = '::'
const OUTCOME_PREFIX = 'out:'

/**
 * A column's own draggable id is prefixed to keep it out of the task id
 * namespace — the two are otherwise both plain UUIDs.
 */
const COLUMN_DRAG_PREFIX = 'column:'

/** One section of a divided column, built on the drop id of the column it is in. */
export function outcomeDropId(index: number, columnDropId: string): string {
  return `${OUTCOME_PREFIX}${index}${PART_SEPARATOR}${columnDropId}`
}

/** A drop target's column, whichever kind of target it is. */
export function columnIdOf(over: string): string {
  return over.split(PART_SEPARATOR).at(-1) ?? over
}

/** The outcome a drop target lands on, as an index, or null for a plain one. */
export function outcomeIndexOf(over: string): number | null {
  const marked = over.split(PART_SEPARATOR).find((part) => part.startsWith(OUTCOME_PREFIX))
  if (marked === undefined) return null
  const index = Number(marked.slice(OUTCOME_PREFIX.length))
  return Number.isInteger(index) ? index : null
}

/** The id a column's drag handle is given. */
export function columnDragId(columnId: string): string {
  return `${COLUMN_DRAG_PREFIX}${columnId}`
}

export function isColumnDrag(id: UniqueIdentifier): boolean {
  return typeof id === 'string' && id.startsWith(COLUMN_DRAG_PREFIX)
}

/** The column a column drag is carrying, from the id its handle was given. */
export function columnIdFromDrag(id: UniqueIdentifier): string {
  return String(id).slice(COLUMN_DRAG_PREFIX.length)
}
