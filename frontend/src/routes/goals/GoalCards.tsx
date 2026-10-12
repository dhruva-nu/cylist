/** A goal's cards as its page lists them: one row each, and the filters over them. */

import type { BoardColumn, Task } from '../../api/client'
import { Avatar, Button, StatusIcon, TypeIcon } from '../../components/ui'
import styles from './GoalPage.module.css'

/** One card on the goal: a button that opens it, and a × that takes it off. */
export function GoalCardRow({
  task,
  unlinking,
  onOpen,
  onUnlink,
}: {
  task: Task
  /** Whether a card is already being taken off, which holds every × still. */
  unlinking: boolean
  onOpen: () => void
  onUnlink: () => void
}) {
  return (
    <li className={styles.card}>
      <button type="button" className={styles.cardOpen} onClick={onOpen}>
        <span className={styles.cardMark} title={task.status}>
          {task.status === 'active' ? (
            <TypeIcon type={task.type} size={15} />
          ) : (
            <StatusIcon status={task.status} size={15} />
          )}
        </span>
        <span className={styles.cardRef}>{task.reference}</span>
        <span className={`${styles.cardTitle} ${task.status === 'cancelled' ? styles.struck : ''}`}>
          {task.title}
        </span>
        <Avatar name={task.assignee.name} colour={task.assignee.colour} />
      </button>
      <Button
        variant="ghost"
        small
        disabled={unlinking}
        aria-label={`Take ${task.reference} off this goal`}
        onClick={onUnlink}
      >
        ×
      </Button>
    </li>
  )
}

/**
 * What the list is narrowed to: a word, and a column.
 *
 * Two filters rather than the board's one search box. The board's box has to
 * parse `col:` and `who:` because it is filtering a grid you cannot see the
 * whole of; a goal's cards are a list of a dozen under three headings, and a
 * row of column chips answers "just show me what is in review" in one click
 * with the count already on it.
 *
 * The chips are toggles rather than a radio group on purpose: "All" is one of
 * them, and pressing the chip you are already on turns it off — which is the
 * gesture people try first and the one a radio group refuses.
 */
export function Filters({
  columns,
  tasks,
  searchText,
  onSearchText,
  onlyColumn,
  onColumn,
  total,
  showing,
}: {
  columns: BoardColumn[]
  /** The cards the word matched — what the column chips count. */
  tasks: Task[]
  searchText: string
  onSearchText: (searchText: string) => void
  /** The column the list is narrowed to, or null for all of them. */
  onlyColumn: string | null
  onColumn: (columnId: string | null) => void
  total: number
  showing: number
}) {
  const countIn = (columnId: string) => tasks.filter((task) => task.column_id === columnId).length

  return (
    <div className={styles.filters}>
      <div className={styles.search}>
        <input
          type="search"
          value={searchText}
          onChange={(event) => onSearchText(event.target.value)}
          placeholder="Find a card — ATL-41, or part of a title"
          aria-label="Find a card on this goal"
        />
        <span className={styles.showing} role="status">
          {showing === total ? `${total} cards` : `${showing} of ${total}`}
        </span>
      </div>

      <div className={styles.chips}>
        <button
          type="button"
          aria-pressed={onlyColumn === null}
          className={onlyColumn === null ? styles.chipOn : undefined}
          onClick={() => onColumn(null)}
        >
          All columns
        </button>
        {columns.map((column) => {
          const inColumn = countIn(column.id)
          const on = onlyColumn === column.id
          return (
            <button
              key={column.id}
              type="button"
              aria-pressed={on}
              className={[on ? styles.chipOn : '', inColumn === 0 ? styles.chipEmpty : '']
                .filter(Boolean)
                .join(' ')}
              // Pressing the chip you are on takes the filter off again.
              onClick={() => onColumn(on ? null : column.id)}
            >
              {column.name}
              <span className={styles.chipCount}>{inColumn}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
