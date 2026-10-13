/**
 * One column of the board, or — in lanes — one half of one.
 *
 * Every column is a fixed height, whatever it is holding. A column that grew
 * with its cards meant dropping one moved every other column on the row out
 * from under the pointer — the board rearranging itself is a worse cost than a
 * short column having some empty space in it.
 *
 * Any column folds down to a rail. A folded column is still a drop target:
 * "Done" is exactly the column you stop looking at and keep moving cards into,
 * and a board that made you unfold it first would be asking you to undo the
 * tidying to use it.
 */

import {
  useDraggable,
  useDroppable,
  type DraggableAttributes,
  type DraggableSyntheticListeners,
} from '@dnd-kit/core'
import type { BoardColumn, Task } from '../../api/client'
import { Button } from '../../components/ui'
import { splitOnHold } from '../boardHiding'
import { ColumnCards, countedCards } from './ColumnCards'
import { columnDragId, laneCellDropId } from './dropTargets'
import styles from './Column.module.css'

export interface ColumnProps {
  column: BoardColumn
  tasks: Task[]
  isFirst: boolean
  collapsed: boolean
  /**
   * Ids of the outcome sections folded away, board-wide — see `useBoardView`.
   * A section's id is its column's plus its index, which is what a fold
   * survives a rename by naming instead of the label.
   */
  collapsedOutcomes: string[]
  /**
   * Whether the card currently in the air may not be dropped here — its
   * template's own stages rule this column out. False the rest of the time,
   * including while nothing is being dragged.
   */
  forbidden: boolean
  onToggleCollapse: () => void
  /** Fold or unfold one outcome section, by its id — see `collapsedOutcomes`. */
  onToggleOutcome: (id: string) => void
  /**
   * Open the whole form, which is where a card is written. Null for a reader
   * whose role does not allow cards, which is what takes the "+" off the
   * column rather than leaving one that refuses.
   */
  onAddTask: (() => void) | null
  onEdit: () => void
  /**
   * Whether this column is keeping its on-hold cards back — decided by
   * `hidesHold` out of this column's own foot and what the board is being
   * asked. False means they are drawn in place, wherever in the stack they sit.
   */
  hidingHold: boolean
  /** Open the column's foot, or shut it again. */
  onToggleHold: () => void
  /**
   * Which half of a column this is drawing.
   *
   * `all` is the ordinary board: one box with the heading, the cards and the
   * composer in it. Grouped by goal, the column is split — `head` draws the
   * heading once above every lane, and `cards` draws one lane's share of the
   * cards, over and over down the board. Splitting the component rather than
   * repeating a whole column per lane is what keeps the fold, the reordering
   * handle and the column's own rules in one place.
   */
  part?: 'all' | 'head' | 'cards'
  /** Which lane this cell belongs to; only a `cards` part has one. */
  laneKey?: string
}

export function Column({
  column,
  tasks,
  isFirst,
  collapsed,
  collapsedOutcomes,
  forbidden,
  onToggleCollapse,
  onToggleOutcome,
  onAddTask,
  onEdit,
  hidingHold,
  onToggleHold,
  part = 'all',
  laneKey,
}: ColumnProps) {
  const isLaneCell = part === 'cards'
  // A lane's cell is its own drop target — the lane says which goal, the
  // column says which column, and a drop reads both off the one id.
  const dropId = isLaneCell && laneKey ? laneCellDropId(laneKey, column.id) : column.id
  const { setNodeRef: setDropRef, isOver } = useDroppable({ id: dropId })
  // A column is draggable on the whole card (so it visually moves as one
  // piece) but only the handle carries the listeners — otherwise every
  // button in the header would start a drag instead of doing its own job.
  const {
    attributes,
    listeners,
    setNodeRef: setDragRef,
    transform,
    isDragging,
  } = useDraggable({
    id: columnDragId(column.id),
    // A lane's cells are not the column, they are one lane's share of it —
    // and eight copies of a column all claiming to be draggable is eight
    // things dnd-kit would have to be told apart by.
    disabled: isLaneCell,
  })
  const counted = countedCards(tasks.length)
  /**
   * The cards this column draws, and the ones it is sitting on.
   *
   * `counted` above stays the whole column either way: the heading is the
   * column's size, the foot is what of it is put down, and the two together
   * are why the gap between the heading's number and the cards you can count
   * is never a surprise.
   */
  const { shown, onHold } = splitOnHold(tasks, hidingHold)

  const setRefs = (node: HTMLElement | null) => {
    setDropRef(node)
    if (!isLaneCell) setDragRef(node)
  }
  const dragStyle = transform
    ? { transform: `translate3d(${transform.x}px, ${transform.y}px, 0)` }
    : undefined

  return (
    <section
      ref={setRefs}
      style={dragStyle}
      className={[
        styles.column,
        part === 'head' && styles.headOnly,
        isLaneCell && styles.cardsOnly,
        collapsed && styles.collapsed,
        // Not `over` when the drop would be refused: highlighting a column the
        // card cannot land in is the board promising something it will not do.
        isOver && !forbidden && styles.over,
        forbidden && styles.forbidden,
        isDragging && styles.columnDragging,
      ]
        .filter(Boolean)
        .join(' ')}
      aria-label={
        collapsed ? `${column.name}, ${counted}, collapsed` : `${column.name}, ${counted}`
      }
    >
      {/* Folded and unfolded are two fillings of one box, not two boxes. The
          box is what animates — it is the same element either way, so its
          width has somewhere to travel from — and the keys are what make the
          filling inside it a swap React remounts, so the fade runs each time
          rather than only on the first. */}
      {collapsed && isLaneCell ? (
        // A folded column still has a lane's worth of cards in it; what it
        // does not have is room to draw them. The cell stays as a drop target
        // so a card can be put into a column that is folded away, which is
        // exactly what folding one is for.
        <div key="railcell" className={styles.railCell} aria-hidden="true">
          {tasks.length ? <span className={styles.count}>{tasks.length}</span> : null}
        </div>
      ) : collapsed ? (
        <ColumnRail
          key="rail"
          column={column}
          count={tasks.length}
          onExpand={onToggleCollapse}
          // The first column is where new work lands, so its "+" survives the
          // fold: otherwise tidying the board away takes the add button with
          // it.
          onAddTask={isFirst ? onAddTask : null}
        />
      ) : (
        <div key="open" className={styles.unfolded}>
          {isLaneCell ? null : (
            <ColumnHead
              column={column}
              count={tasks.length}
              dragAttributes={attributes}
              dragListeners={listeners}
              onCollapse={onToggleCollapse}
              onEdit={onEdit}
            />
          )}

          {part === 'head' ? null : (
            <ColumnCards
              column={column}
              tasks={shown}
              dropId={dropId}
              collapsedOutcomes={collapsedOutcomes}
              onToggleOutcome={onToggleOutcome}
            />
          )}

          {/* The foot, under the cards and above the composer: the held work
              is the bottom of this column, so the line that lets it out is
              too. Drawn only when there is something behind it — a column
              with nothing on hold has nothing to say here, and a row of "0 on
              hold" down an untroubled board is noise on every column at
              once. */}
          {part !== 'head' && onHold.length > 0 ? (
            <HoldFoot
              columnName={column.name}
              held={onHold.length}
              hiding={hidingHold}
              onToggle={onToggleHold}
            />
          ) : null}

          {isFirst && part === 'all' && onAddTask ? (
            <button className={styles.addTask} onClick={onAddTask}>
              + Add a task
            </button>
          ) : (
            <div className={styles.foot} />
          )}
        </div>
      )}
    </section>
  )
}

/** A column folded down to a rail: its count and its name, turned on their side. */
function ColumnRail({
  column,
  count,
  onExpand,
  onAddTask,
}: {
  column: BoardColumn
  count: number
  onExpand: () => void
  /** Null unless the rail keeps the column's "+". */
  onAddTask: (() => void) | null
}) {
  return (
    <div className={styles.rail}>
      <button
        type="button"
        className={styles.railBody}
        aria-expanded={false}
        aria-label={`Expand ${column.name}`}
        onClick={onExpand}
      >
        <span aria-hidden="true">›</span>
        <span className={styles.count}>{count}</span>
        <span className={styles.railName}>{column.name}</span>
      </button>
      {onAddTask ? (
        <button className={styles.railAdd} onClick={onAddTask} aria-label="Add a task">
          +
        </button>
      ) : null}
    </div>
  )
}

/**
 * An open column's heading: the handle it is reordered by, its name and
 * count, and its fold and edit buttons.
 *
 * The drag wiring is handed in rather than set up here, because it belongs to
 * the whole column — see `Column` — and only the handle carries it.
 */
function ColumnHead({
  column,
  count,
  dragAttributes,
  dragListeners,
  onCollapse,
  onEdit,
}: {
  column: BoardColumn
  count: number
  dragAttributes: DraggableAttributes
  dragListeners: DraggableSyntheticListeners
  onCollapse: () => void
  onEdit: () => void
}) {
  return (
    <div className={styles.head}>
      <div className={styles.headRow}>
        {/* The name is the handle: there is nothing else in the heading worth
            aiming at to move a column, and a grip beside it was one more mark
            on every column saying what the name could say itself. */}
        <h3>
          <button
            type="button"
            className={styles.dragHandle}
            {...dragAttributes}
            {...dragListeners}
            aria-label={`Reorder ${column.name}. Press space to pick up, then the left and right arrow keys to move it.`}
            title="Drag to reorder"
          >
            {column.name}
          </button>
        </h3>
        <span className={styles.headActions}>
          <span className={styles.count}>{count}</span>
          <Button
            variant="ghost"
            small
            aria-expanded
            aria-label={`Collapse ${column.name}`}
            onClick={onCollapse}
          >
            ‹
          </Button>
          <Button variant="ghost" small onClick={onEdit} aria-label={`Edit ${column.name}`}>
            ···
          </Button>
        </span>
      </div>
      <p title={column.description || undefined}>{column.description}</p>
    </div>
  )
}

/** The line at the foot of a column that lets its on-hold cards out, and puts them back. */
function HoldFoot({
  columnName,
  held,
  hiding,
  onToggle,
}: {
  columnName: string
  held: number
  hiding: boolean
  onToggle: () => void
}) {
  const cards = held === 1 ? 'card' : 'cards'

  return (
    <button
      type="button"
      className={styles.holdFoot}
      aria-expanded={!hiding}
      onClick={onToggle}
      title={
        hiding
          ? `Show the ${cards} on hold in ${columnName}`
          : `Hide the ${cards} on hold in ${columnName}`
      }
    >
      <span aria-hidden="true">{hiding ? '+' : '−'}</span>
      {held} on hold
    </button>
  )
}
