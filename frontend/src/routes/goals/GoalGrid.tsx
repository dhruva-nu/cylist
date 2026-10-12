import { Link } from '@tanstack/react-router'
import type { CSSProperties } from 'react'
import type { FiledItem, Goal, Person } from '../../api/client'
import { GoalProgressBar, GoalStatusPill, GoalTargetMark } from '../../components/GoalMarks'
import { Avatar, Tagged, cardStyles } from '../../components/ui'
import styles from './ProjectGoals.module.css'

/** A grid of goal cards — the open ones, or the settled ones under their heading. */
export function GoalGrid({
  goals,
  projectKey,
  members,
  files,
}: {
  goals: Goal[]
  projectKey: string
  members: Person[]
  files: readonly FiledItem[]
}) {
  return (
    <div className={styles.grid}>
      {goals.map((goal) => (
        <GoalCard
          key={goal.id}
          goal={goal}
          projectKey={projectKey}
          members={members}
          files={files}
        />
      ))}
    </div>
  )
}

function GoalCard({
  goal,
  projectKey,
  members,
  files,
}: {
  goal: Goal
  projectKey: string
  /** Only to draw the description's `@` tags as tags. */
  members: Person[]
  /** Likewise its `>` tags, which are drawn as links to the files. */
  files: readonly FiledItem[]
}) {
  const { total, done, open } = goal.progress

  return (
    <Link
      to="/p/$projectKey/goals/$goalRef"
      params={{ projectKey, goalRef: goal.reference }}
      className={`${cardStyles.card} ${cardStyles.clickable} ${styles.goal}`}
      // The rail, on the card that names the colour as well as on the cards
      // that wear it: the goals page is where you learn what the colour on the
      // board means.
      style={{ '--goal': goal.colour } as CSSProperties}
    >
      <div className={styles.goalHead}>
        <span className={styles.reference}>{goal.reference}</span>
        <GoalStatusPill status={goal.status} />
        <GoalTargetMark goal={goal} />
      </div>

      <h3>{goal.name}</h3>
      {goal.description ? (
        <p>
          <Tagged text={goal.description} members={members} files={files} />
        </p>
      ) : null}

      <GoalProgressBar goal={goal} />

      <div className={styles.foot}>
        <span className={styles.counts}>
          {total === 0 ? 'No cards yet' : `${done} of ${total} done`}
          {open ? <span className={styles.left}>{open} left</span> : null}
        </span>
        <Avatar name={goal.owner.name} colour={goal.owner.colour} />
      </div>
    </Link>
  )
}
