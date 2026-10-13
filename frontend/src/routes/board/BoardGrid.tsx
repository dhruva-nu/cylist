/**
 * The columns, laid out: either one row of them, or — in lanes — a heading row
 * with a row of cells under it for every goal.
 *
 * Width answers to how many columns are open, not to what is in them, so the
 * columns share the row out between them and fold one to give the rest more.
 */

import { Fragment, type RefObject } from 'react'
import type { BoardColumn, Task } from '../../api/client'
import { Column, type ColumnProps } from './Column'
import { LaneHead } from './LaneHead'
import { tasksInLane, type Lane } from './narrowBoard'
import styles from './BoardGrid.module.css'

/** What every drawing of a column is handed, whichever part of it is drawn. */
export type SharedColumnProps = Omit<ColumnProps, 'tasks' | 'part' | 'laneKey'>

/** Everything the board needs to be drawn in lanes. */
export interface LanesLayout {
  lanes: Lane[]
  /** The keys of the lanes folded down to their heading. */
  folded: string[]
  projectKey: string
  onToggleFold: (lane: Lane) => void
  /** Write a new card on a lane's goal. Null where the reader's role does not allow cards. */
  onAddTask: ((lane: Lane) => void) | null
}

export function BoardGrid({
  boardRef,
  columns,
  byColumn,
  columnProps,
  lanes,
}: {
  /** The one element that scrolls while a card is dragged — see the board's `autoScroll`. */
  boardRef: RefObject<HTMLDivElement>
  columns: BoardColumn[]
  byColumn: Map<string, Task[]>
  columnProps: (column: BoardColumn) => SharedColumnProps
  /** Null for the ordinary board, one row of columns. */
  lanes: LanesLayout | null
}) {
  const tasksIn = (column: BoardColumn) => byColumn.get(column.id) ?? []
  const addTask = lanes?.onAddTask ?? null

  if (!lanes) {
    return (
      <div ref={boardRef} className={styles.board}>
        {columns.map((column, i) => (
          <Fragment key={column.id}>
            {i > 0 ? <div className={styles.rule} aria-hidden="true" /> : null}
            <Column {...columnProps(column)} tasks={tasksIn(column)} />
          </Fragment>
        ))}
      </div>
    )
  }

  /** The grid the header row and every lane share, so their columns line up. */
  const laneTracks = {
    gridTemplateColumns: columns
      .map((column) => (columnProps(column).collapsed ? '52px' : 'minmax(280px, 400px)'))
      .join(' '),
  }

  return (
    <div ref={boardRef} className={styles.lanes}>
      {/* The column headings, once, above every lane — with the drag handle
          and the fold still on them. Repeating them down each lane would say
          the same five words as many times as there are goals, and put the
          board's own controls in five places. */}
      <div className={styles.laneRow} style={laneTracks}>
        {columns.map((column) => (
          <Column key={column.id} part="head" {...columnProps(column)} tasks={tasksIn(column)} />
        ))}
      </div>

      {lanes.lanes.map((lane) => {
        const folded = lanes.folded.includes(lane.key)
        const rowId = `lane-${lane.key}`
        const count = columns.reduce(
          (total, column) => total + tasksInLane(tasksIn(column), lane.key).length,
          0,
        )
        return (
          <section key={lane.key} className={styles.lane} aria-label={lane.goal?.name ?? 'No goal'}>
            <LaneHead
              goal={lane.goal}
              projectKey={lanes.projectKey}
              count={count}
              folded={folded}
              rowId={rowId}
              onToggleFold={() => lanes.onToggleFold(lane)}
              onAdd={addTask ? () => addTask(lane) : null}
            />
            {folded ? null : (
              <div id={rowId} className={styles.laneRow} style={laneTracks}>
                {columns.map((column) => (
                  <Column
                    key={column.id}
                    part="cards"
                    laneKey={lane.key}
                    {...columnProps(column)}
                    tasks={tasksInLane(tasksIn(column), lane.key)}
                  />
                ))}
              </div>
            )}
          </section>
        )
      })}
    </div>
  )
}
