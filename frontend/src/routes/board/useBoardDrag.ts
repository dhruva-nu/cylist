/**
 * Dragging on the board: a card into a column, a lane or a section of one, and
 * a column's handle along the row.
 *
 * A card created from a template goes only where that template allows. While
 * such a card is in the air the columns it cannot be dropped in are greyed
 * and refuse the drop, so the rule is visible before the server has to state
 * it — see `forbiddenColumnsFor`. The rule itself lives on the server; this is
 * only the board saying out loud what it already knows.
 */

import {
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type Announcements,
  type DragEndEvent,
  type DragStartEvent,
  type UniqueIdentifier,
} from '@dnd-kit/core'
import { useState } from 'react'
import type { BoardColumn, Task, Template } from '../../api/client'
import {
  columnIdFromDrag,
  columnIdOf,
  isColumnDrag,
  laneKeyOf,
  laneKeyOfTask,
  NO_GOAL_LANE,
  outcomeIndexOf,
} from './dropTargets'
import { DRAG_KEYS, moveByColumn } from './keyboardDrag'
import { forbiddenColumnsFor } from './narrowBoard'
import type { Reopening } from './ReopenDialog'
import type { Move } from './useBoardMutations'

export function useBoardDrag({
  columns,
  tasks,
  byColumn,
  templates,
  moveTask,
  reorderColumns,
  announce,
}: {
  columns: BoardColumn[]
  /** Every card on the board, drawn or filtered away — a drag names what it carries by id. */
  tasks: Task[]
  /** The cards drawn in each column, which is where a dropped card is placed after. */
  byColumn: Map<string, Task[]>
  templates: Template[]
  moveTask: (move: Move) => void
  reorderColumns: (columnIds: string[]) => void
  announce: (message: string) => void
}) {
  /** What is in the air, so the overlay knows what to draw. */
  const [draggingId, setDraggingId] = useState<UniqueIdentifier | null>(null)
  /**
   * A drag that would take a finished card back onto the board, held until it
   * is confirmed.
   *
   * The one move worth asking about. Every other drag says where work has got
   * to; this one un-says it — the card stops being done, and its history
   * records that it was reopened. A card nudged out of the last column by a
   * slipped drop would rewrite that quietly, so the drop is held here and the
   * card stays where it was until somebody says yes.
   */
  const [reopening, setReopening] = useState<Reopening | null>(null)

  // A short drag threshold so a card can still be clicked open: without it
  // every press would start a drag and no click would ever land.
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { keyboardCodes: DRAG_KEYS, coordinateGetter: moveByColumn }),
  )

  /**
   * The board's last column, which is what "done" means for a card: arriving
   * there writes the card's finishing time and leaving there clears it. A
   * position rather than a name — a column called Done with a column to its
   * right is not the end of the board.
   */
  const doneColumn = columns.at(-1)

  const taskById = (id: UniqueIdentifier) => tasks.find((task) => task.id === id)
  const columnById = (id: string) => columns.find((column) => column.id === id)
  const forbiddenFor = (task: Task | null | undefined) =>
    forbiddenColumnsFor(task, templates, columns)

  const nameOfColumnId = (id: string) => columnById(id)?.name ?? 'the column'
  const nameOfTarget = (id: UniqueIdentifier | undefined) =>
    // Through `columnIdOf`, because in lanes the target is a lane's cell in a
    // column rather than the column itself — and "over lane:…::uuid" is not a
    // sentence anybody wants read to them.
    typeof id === 'string' ? nameOfColumnId(columnIdOf(id)) : 'nowhere'
  const nameOfDragged = (id: UniqueIdentifier) =>
    isColumnDrag(id) ? nameOfColumnId(columnIdFromDrag(id)) : (taskById(id)?.title ?? 'the card')

  // Spoken by dnd-kit's own live region, so a keyboard drag is followed rather
  // than merely performed. Shared between task cards and column handles: both
  // drag over the same set of column drop targets, so one set of wording
  // covers either, naming whichever is actually being moved.
  const announcements: Announcements = {
    onDragStart: ({ active }) =>
      `Picked up ${nameOfDragged(active.id)}. Use the left and right arrow keys to choose a column.`,
    onDragOver: ({ active, over }) =>
      over ? `${nameOfDragged(active.id)} is over ${nameOfTarget(over.id)}.` : undefined,
    onDragEnd: ({ active, over }) => {
      if (!over) return `${nameOfDragged(active.id)} was left where it was.`
      if (isColumnDrag(active.id)) {
        return `${nameOfDragged(active.id)} moved next to ${nameOfTarget(over.id)}.`
      }
      // A refused drop has to be announced as refused. dnd-kit's own wording
      // is "dropped into", which is the one thing that did not happen.
      const card = taskById(active.id)
      if (typeof over.id === 'string' && forbiddenFor(card).has(over.id)) {
        return (
          `${card?.template_name ?? 'This card'} cards do not go to ${nameOfTarget(over.id)}. ` +
          `${nameOfDragged(active.id)} is back where it was.`
        )
      }
      return `Dropped ${nameOfDragged(active.id)} into ${nameOfTarget(over.id)}.`
    },
    onDragCancel: ({ active }) => `Cancelled. ${nameOfDragged(active.id)} is back where it was.`,
  }

  // Only a card gets an overlay. A column is dragged where it stands: it is
  // not inside anything that scrolls or clips, so it has no need of one.
  const draggedTask =
    draggingId !== null && !isColumnDrag(draggingId) ? (taskById(draggingId) ?? null) : null

  /** A column's handle let go over another column: the board is reordered. */
  function dropColumn(columnId: string, overId: string) {
    const fromIndex = columns.findIndex((column) => column.id === columnId)
    // Every drop target names a column; in lanes it names the lane as well.
    const toIndex = columns.findIndex((column) => column.id === columnIdOf(overId))
    if (fromIndex < 0 || toIndex < 0 || fromIndex === toIndex) return

    const reordered = [...columns]
    const [moved] = reordered.splice(fromIndex, 1)
    if (!moved) return
    reordered.splice(toIndex, 0, moved)
    reorderColumns(reordered.map((column) => column.id))
    announce(`${moved.name} moved ${toIndex < fromIndex ? 'left' : 'right'}.`)
  }

  /** A card let go over a drop target: a move, a change of goal, both, or nothing. */
  function dropCard(taskId: UniqueIdentifier, overId: string) {
    const task = taskById(taskId)
    if (!task) return

    // Every drop target names a column; in lanes it names the lane as well.
    const droppedIn = columnIdOf(overId)

    // Refused here as well as by the server: a card that snaps back with an
    // error banner is how you find out afterwards, and the column already
    // greyed itself the moment the card was picked up. Said out loud by
    // `announcements.onDragEnd` rather than here — dnd-kit speaks on every
    // drop, and two live regions describing one gesture is one of them
    // talking over the other.
    if (forbiddenFor(task).has(droppedIn)) return

    const lane = laneKeyOf(overId)
    const changingGoal = lane !== null && lane !== laneKeyOfTask(task)
    const changingColumn = task.column_id !== droppedIn
    // The section of a divided column the card was dropped on. A card put in a
    // different section of the column it is already in has not moved by the
    // board's reckoning, but it has changed how the work ended — which is a
    // change worth making, so it counts here the way a column does.
    const landedOn = outcomeIndexOf(overId)
    const changingOutcome = landedOn !== null && landedOn !== task.outcome_index
    // A card put back where it came from is not a move, and a lane it was
    // already in is not a change of goal: neither is worth a request.
    if (!changingColumn && !changingGoal && !changingOutcome) return

    const move: Move = {
      taskId: task.id,
      taskRef: task.reference,
      // The outcome travels with the move, so a card only changing section
      // still names the column it is staying in.
      columnId: changingColumn || changingOutcome ? droppedIn : null,
      position: byColumn.get(droppedIn)?.length ?? 0,
      outcomeIndex: landedOn,
      outcome: landedOn === null ? null : (columnById(droppedIn)?.outcomes[landedOn] ?? null),
      ...(changingGoal ? { goalId: lane === NO_GOAL_LANE ? null : lane } : {}),
    }

    if (changingColumn && doneColumn && task.column_id === doneColumn.id) {
      setReopening({ move, task, from: doneColumn.name, to: nameOfColumnId(droppedIn) })
      return
    }

    moveTask(move)
  }

  function onDragStart(event: DragStartEvent) {
    setDraggingId(event.active.id)
  }

  function onDragEnd(event: DragEndEvent) {
    // Cleared here rather than in each branch below: every one of them ends
    // the drag, and an overlay left on screen is a card stuck to the pointer.
    setDraggingId(null)

    const overId = event.over?.id
    if (typeof overId !== 'string') return

    if (isColumnDrag(event.active.id)) dropColumn(columnIdFromDrag(event.active.id), overId)
    else dropCard(event.active.id, overId)
  }

  function onDragCancel() {
    setDraggingId(null)
  }

  function confirmReopening() {
    if (!reopening) return
    moveTask(reopening.move)
    announce(`${reopening.task.reference} reopened into ${reopening.to}.`)
    setReopening(null)
  }

  return {
    sensors,
    announcements,
    onDragStart,
    onDragEnd,
    onDragCancel,
    draggedTask,
    /** Greyed while a card is in the air, and empty the rest of the time. */
    forbiddenColumns: forbiddenFor(draggedTask),
    reopening,
    confirmReopening,
    cancelReopening: () => setReopening(null),
  }
}
