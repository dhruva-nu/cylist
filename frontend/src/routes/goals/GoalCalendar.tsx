import { Link } from '@tanstack/react-router'
import { useMemo, useState, type CSSProperties } from 'react'
import type { Goal } from '../../api/client'
import { GOAL_STATUS_LABELS } from '../../components/GoalMarks'
import { Button } from '../../components/ui'
import { WEEKDAYS, dayName, goalsByDate, monthName, monthWeeks, openingMonth } from './goalCalendar'
import styles from './ProjectGoals.module.css'

/**
 * The same goals, laid on the days they are wanted by.
 *
 * Every goal is drawn, settled ones included but greyed: a month whose targets
 * were all met should look like a month whose targets were all met, not an
 * empty one. A goal with no target date is not on any day, so those go in a
 * strip underneath rather than being quietly left out — a calendar that hides
 * a third of the list is how a deadline gets missed.
 */
export function GoalCalendar({ goals, projectKey }: { goals: Goal[]; projectKey: string }) {
  const [month, setMonth] = useState(() => openingMonth(goals))
  const { dated, undated } = useMemo(() => goalsByDate(goals), [goals])
  const weeks = useMemo(() => monthWeeks(month.getFullYear(), month.getMonth()), [month])

  const moveMonth = (by: number) =>
    setMonth(new Date(month.getFullYear(), month.getMonth() + by, 1))
  const today = new Date()
  const onThisMonth =
    month.getFullYear() === today.getFullYear() && month.getMonth() === today.getMonth()
  const targetedThisMonth = weeks
    .flat()
    .filter((day) => day.inMonth)
    .reduce((count, day) => count + (dated.get(day.iso)?.length ?? 0), 0)

  return (
    <section className={styles.calendar} aria-label="Goals by target date">
      <div className={styles.monthBar}>
        <h2 className={styles.month}>{monthName(month.getFullYear(), month.getMonth())}</h2>
        <div className={styles.monthNav}>
          <Button small onClick={() => moveMonth(-1)} aria-label="Previous month">
            ‹
          </Button>
          <Button
            small
            onClick={() => setMonth(new Date(today.getFullYear(), today.getMonth(), 1))}
            disabled={onThisMonth}
          >
            Today
          </Button>
          <Button small onClick={() => moveMonth(1)} aria-label="Next month">
            ›
          </Button>
        </div>
      </div>

      <div className={styles.monthScroll}>
        <div className={styles.weeks}>
          {WEEKDAYS.map((weekday) => (
            <div key={weekday} className={styles.weekday} aria-hidden="true">
              {weekday}
            </div>
          ))}
          {weeks.flat().map((day) => {
            const goalsOnDay = dated.get(day.iso) ?? []
            return (
              <div
                key={day.iso}
                className={[
                  styles.day,
                  day.inMonth ? '' : styles.outside,
                  day.isToday ? styles.today : '',
                ]
                  .filter(Boolean)
                  .join(' ')}
              >
                <span className={styles.dayNumber} title={dayName(day.iso)}>
                  {day.day}
                </span>
                {goalsOnDay.map((goal) => (
                  <GoalPin key={goal.id} goal={goal} projectKey={projectKey} />
                ))}
              </div>
            )
          })}
        </div>
      </div>

      {/* Said in words as well as drawn, because "nothing here" and "nothing
          loaded" look identical on an empty grid. */}
      {targetedThisMonth === 0 ? (
        <p className={styles.quiet}>Nothing is targeted in this month.</p>
      ) : null}

      {undated.length ? (
        <div className={styles.undated}>
          <h3>No target date</h3>
          <div className={styles.undatedRow}>
            {undated.map((goal) => (
              <GoalPin key={goal.id} goal={goal} projectKey={projectKey} />
            ))}
          </div>
        </div>
      ) : null}
    </section>
  )
}

/** A goal as it appears on a day: its colour, its reference, its name. */
function GoalPin({ goal, projectKey }: { goal: Goal; projectKey: string }) {
  const said = pinProgress(goal)

  return (
    <Link
      to="/p/$projectKey/goals/$goalRef"
      params={{ projectKey, goalRef: goal.reference }}
      className={`${styles.pin} ${goal.status === 'open' ? '' : styles.pinSettled}`}
      style={{ '--goal': goal.colour } as CSSProperties}
      title={`${goal.reference} — ${goal.name} (${said})`}
    >
      <span className={styles.pinRef}>{goal.reference}</span>
      <span className={styles.pinName}>{goal.name}</span>
    </Link>
  )
}

/** How far along a pinned goal is, as its tooltip says it: a count, or how it ended. */
function pinProgress(goal: Goal): string {
  if (goal.status !== 'open') return GOAL_STATUS_LABELS[goal.status].toLowerCase()
  const { total, done } = goal.progress
  return total === 0 ? 'no cards yet' : `${done} of ${total} done`
}
