import { Link } from '@tanstack/react-router'
import type { CSSProperties } from 'react'
import type { Goal } from '../../api/client'
import { GoalStatusPill } from '../../components/GoalMarks'
import { Button } from '../../components/ui'
import styles from './LaneHead.module.css'

/**
 * A goal's lane heading: what the lane is, and how much is in it.
 *
 * A full-width bar above the lane's cells rather than a label to one side.
 * A side rail would take 200px off every column on the board for a name that
 * is read once, and on a board scrolled sideways it would be the first thing
 * to scroll out of sight — which is the one thing about a lane you always need
 * to be able to see.
 */
export function LaneHead({
  goal,
  projectKey,
  count,
  folded,
  rowId,
  onToggleFold,
  onAdd,
}: {
  /** Null for the lane everything on no goal falls into. */
  goal: Goal | null
  projectKey: string
  count: number
  folded: boolean
  /** The row this heading opens and closes, for `aria-controls`. */
  rowId: string
  onToggleFold: () => void
  /** Null where the reader's role does not allow cards — see `Column`. */
  onAdd: (() => void) | null
}) {
  const name = goal?.name ?? 'No goal'
  // A settled goal is only in lanes at all while cards are still on it — see
  // `lanesFor`. Those cards are work that has outlived the goal they were
  // written under, which is the one thing about the lane worth saying, so the
  // heading is drawn back and carries the pill rather than reading exactly
  // like the lanes still being worked on.
  const settled = goal !== null && goal.status !== 'open'

  return (
    <div
      className={[
        styles.laneHead,
        goal ? '' : styles.laneHeadLoose,
        settled ? styles.laneSettled : '',
      ]
        .filter(Boolean)
        .join(' ')}
      style={goal ? ({ '--goal': goal.colour } as CSSProperties) : undefined}
    >
      {/* The fold, on the heading rather than beside it: the heading is the
          whole of a folded lane, and the control that brings it back has to be
          on the part that is still there. */}
      <button
        type="button"
        className={styles.laneFold}
        aria-expanded={!folded}
        aria-controls={rowId}
        aria-label={folded ? `Open ${name}` : `Fold ${name} away`}
        title={folded ? `Open ${name}` : `Fold ${name} away`}
        onClick={onToggleFold}
      >
        <span aria-hidden="true">{folded ? '▸' : '▾'}</span>
      </button>
      {goal ? (
        <>
          <Link
            to="/p/$projectKey/goals/$goalRef"
            params={{ projectKey, goalRef: goal.reference }}
            className={styles.laneName}
          >
            <span className={styles.laneDot} aria-hidden="true" />
            {goal.name}
            <span className={styles.laneRef}>{goal.reference}</span>
          </Link>
          <GoalStatusPill status={goal.status} />
        </>
      ) : (
        <span className={styles.laneName}>
          <span className={styles.laneDot} aria-hidden="true" />
          No goal
        </span>
      )}
      <span className={styles.count}>{count}</span>
      {onAdd ? (
        <Button
          variant="ghost"
          small
          onClick={onAdd}
          aria-label={goal ? `Add a task on ${goal.name}` : 'Add a task on no goal'}
        >
          + Task
        </Button>
      ) : null}
    </div>
  )
}
