/**
 * The columns, laid out in one row.
 *
 * Width answers to how many columns are open, not to what is in them, so the
 * columns share the row out between them and fold one to give the rest more.
 */

import { Fragment, type ReactNode, type RefObject } from 'react'
import type { BoardColumn, Task } from '../../api/client'
import { Column, type ColumnProps } from './Column'
import styles from './BoardGrid.module.css'

/** What every drawing of a column is handed. */
export type SharedColumnProps = Omit<ColumnProps, 'tasks'>

export function BoardGrid({
  boardRef,
  columns,
  byColumn,
  columnProps,
  dial,
}: {
  /** The one element that scrolls while a card is dragged — see the board's `autoScroll`. */
  boardRef: RefObject<HTMLDivElement>
  columns: BoardColumn[]
  byColumn: Map<string, Task[]>
  columnProps: (column: BoardColumn) => SharedColumnProps
  /**
   * The board switcher, drawn halfway down the left edge, or null where there
   * is only the one board. Outside the row that scrolls, so it stays put
   * while the columns move under it.
   */
  dial: ReactNode
}) {
  return (
    <div className={styles.stage}>
      {dial}
      <div
        ref={boardRef}
        className={[styles.board, dial && styles.withDial].filter(Boolean).join(' ')}
      >
        {columns.map((column, i) => (
          <Fragment key={column.id}>
            {i > 0 ? <div className={styles.rule} aria-hidden="true" /> : null}
            <Column {...columnProps(column)} tasks={byColumn.get(column.id) ?? []} />
          </Fragment>
        ))}
      </div>
    </div>
  )
}
