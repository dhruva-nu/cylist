import type { FiledItem, GoalDetail, Person } from '../../api/client'
import { GoalProgressBar, GoalTargetMark } from '../../components/GoalMarks'
import { Avatar, Tagged } from '../../components/ui'
import styles from './GoalPage.module.css'

/** Where the reason an open goal cannot be closed yet is written out in full. */
export const REFUSAL_ID = 'goal-close-refusal'

export function GoalAside({
  goal,
  refusal,
  members,
  files,
}: {
  goal: GoalDetail
  /** Why it cannot be closed yet, written under the counts it is counted from. */
  refusal: string | null
  /** Only to draw the description's `@` tags as tags. */
  members: Person[]
  /** Likewise its `>` tags, drawn as links to the files. */
  files: readonly FiledItem[]
}) {
  const { total, done, open, blocked, on_hold: onHold, cancelled } = goal.progress

  return (
    <aside className={styles.side} aria-label="About this goal">
      <section className={styles.summary}>
        <GoalProgressBar goal={goal} large />
        <div className={styles.stats}>
          <Stat label="Cards" value={total} />
          <Stat label="Done" value={done} />
          <Stat label="Left" value={open} />
          {blocked ? <Stat label="Blocked" value={blocked} tone="blocked" /> : null}
          {onHold ? <Stat label="On hold" value={onHold} tone="hold" /> : null}
          {cancelled ? <Stat label="Cancelled" value={cancelled} /> : null}
        </div>
        {refusal ? (
          <p id={REFUSAL_ID} className={styles.refusal}>
            {refusal}
          </p>
        ) : null}
        <div className={styles.facts}>
          <div className={styles.owner}>
            <Avatar name={goal.owner.name} colour={goal.owner.colour} />
            <div>
              <span className={styles.statLabel}>Owner</span>
              <b>{goal.owner.name}</b>
            </div>
          </div>
          <GoalTargetMark goal={goal} />
        </div>
      </section>

      <section className={styles.about}>
        <h2>What it is</h2>
        <p>
          {goal.description ? (
            <Tagged text={goal.description} members={members} files={files} />
          ) : (
            'No description yet.'
          )}
        </p>
      </section>
    </aside>
  )
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: 'blocked' | 'hold' }) {
  return (
    <div className={`${styles.stat} ${tone ? styles[tone] : ''}`}>
      <span className={styles.statLabel}>{label}</span>
      <b>{value}</b>
    </div>
  )
}
