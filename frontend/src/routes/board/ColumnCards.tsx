/**
 * The cards in an open column, or in one lane's share of it.
 *
 * A divided column is still one column: one heading, one count, one place on
 * the board. What it has instead of a single stack of cards is a stack per way
 * the work can have ended, each its own drop target, so which one a card is in
 * is something you set by putting it there.
 */

import { useDroppable } from '@dnd-kit/core'
import type { BoardColumn, Task } from '../../api/client'
import { Button } from '../../components/ui'
import { outcomeDropId } from './dropTargets'
import { TaskCard } from './TaskCard'
import styles from './Column.module.css'

/** "1 card", "3 cards". */
export function countedCards(count: number): string {
  return `${count} ${count === 1 ? 'card' : 'cards'}`
}

export function ColumnCards({
  column,
  tasks,
  dropId,
  collapsedOutcomes,
  onToggleOutcome,
}: {
  column: BoardColumn
  tasks: Task[]
  /** The column's own drop target, which each section's id is built on. */
  dropId: string
  collapsedOutcomes: string[]
  onToggleOutcome: (id: string) => void
}) {
  if (!column.outcomes.length) return <CardList tasks={tasks} />

  return (
    <div className={styles.sections}>
      {column.outcomes.map((label, index) => {
        const outcomeId = `${column.id}:${index}`
        return (
          <OutcomeSection
            key={index}
            label={label}
            dropId={outcomeDropId(index, dropId)}
            // Cards written before the column was divided, and any left
            // behind by a section that was renamed away, sit in the
            // first: better an honest heading than a stack with none.
            tasks={tasks.filter((task) => (task.outcome_index ?? 0) === index)}
            collapsed={collapsedOutcomes.includes(outcomeId)}
            onToggleCollapse={() => onToggleOutcome(outcomeId)}
          />
        )
      })}
    </div>
  )
}

/** A stack of cards, top to bottom, as a column or a section of one holds them. */
function CardList({ tasks }: { tasks: Task[] }) {
  return (
    <div className={styles.cards}>
      {tasks.map((task) => (
        <TaskCard key={task.id} task={task} />
      ))}
    </div>
  )
}

/**
 * One section of a divided column: how the work ended, and what ended that way.
 *
 * Its own drop target, and its own heading with its own count — a column's
 * count is still the sum of them, but "3 done, 1 cancelled" is the sentence
 * dividing the column was for. The heading stays when the section is empty,
 * because an empty section is the drop target that puts the first card in it.
 *
 * Bordered and tinted as a panel of its own, the way a column reads as a
 * panel against the board behind it — a rule between two stretches of the
 * same colour stops reading as a division the moment there are three of
 * them; a box does not.
 *
 * Its cards can be folded away behind the heading, the same fold a whole
 * column has and for the same reason: a section nobody is triaging today is
 * still a heading and a count worth keeping in view. It stays a drop target
 * either way, so a card can be put into a folded section without opening it
 * first.
 */
function OutcomeSection({
  label,
  dropId,
  tasks,
  collapsed,
  onToggleCollapse,
}: {
  label: string
  dropId: string
  tasks: Task[]
  collapsed: boolean
  onToggleCollapse: () => void
}) {
  const { setNodeRef, isOver } = useDroppable({ id: dropId })
  const counted = countedCards(tasks.length)

  return (
    <section
      ref={setNodeRef}
      className={`${styles.section} ${isOver ? styles.sectionOver : ''}`}
      aria-label={collapsed ? `${label}, ${counted}, collapsed` : `${label}, ${counted}`}
    >
      <h4 className={styles.sectionHead}>
        <Button
          variant="ghost"
          small
          aria-expanded={!collapsed}
          aria-label={collapsed ? `Expand ${label}` : `Collapse ${label}`}
          onClick={onToggleCollapse}
        >
          <span aria-hidden="true">{collapsed ? '▸' : '▾'}</span>
        </Button>
        <span className={styles.sectionLabel}>{label}</span>
        <span className={styles.count}>{tasks.length}</span>
      </h4>
      {collapsed ? null : <CardList tasks={tasks} />}
    </section>
  )
}
