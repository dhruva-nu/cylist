/**
 * A project's goals — the epics its cards are written under.
 *
 * A goal is not a card and this is not a board: there is nothing to drag, no
 * column to be in, and no order but the one the server sends (open first, the
 * nearest deadline at the top). What each one shows is how much of it is left,
 * because that is the only question a list of epics is ever asked.
 *
 * There are two ways to read the same goals. The list answers "how far along
 * is each of these"; the calendar answers "what is coming, and when" — the
 * question the list can only answer by making you compare a column of dates in
 * your head. Neither is a different page, because they are the same goals.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from '@tanstack/react-router'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, type FiledItem, type Goal, type Person } from '../api/client'
import { GoalDialog } from '../components/GoalDialog'
import { GOAL_STATUS_LABELS, GoalProgressBar, GoalTargetMark } from '../components/GoalMarks'
import { useProjectFiles } from '../components/projectFiles'
import { PageHead } from '../components/Shell'
import {
  Avatar,
  Button,
  EmptyState,
  ErrorBanner,
  LiveRegion,
  Tagged,
  cardStyles,
  useAnnouncer,
} from '../components/ui'
import { WEEKDAYS, dayName, goalsByDate, monthName, monthWeeks, openingMonth } from './goalCalendar'
import styles from './ProjectGoals.module.css'

type View = 'list' | 'calendar'

const VIEWS: { value: View; label: string }[] = [
  { value: 'list', label: 'List' },
  { value: 'calendar', label: 'Calendar' },
]

/**
 * Which of the two the reader is on, remembered per project.
 *
 * Per project because it is a fact about the project rather than about the
 * reader: a handful of goals with no dates on them is a list, and a roadmap of
 * twenty with a target each is a calendar. localStorage is wrapped because
 * reading it throws outright in a private window, and a page that will not
 * render is a worse outcome than one that forgets a preference.
 */
function useGoalView(projectKey: string) {
  const key = `cylist.goals.view.${projectKey}`
  const read = useCallback((): View => {
    try {
      return window.localStorage.getItem(`cylist.goals.view.${projectKey}`) === 'calendar'
        ? 'calendar'
        : 'list'
    } catch {
      return 'list'
    }
  }, [projectKey])

  const [view, setView] = useState<View>(read)

  useEffect(() => setView(read()), [projectKey, read])

  useEffect(() => {
    try {
      window.localStorage.setItem(key, view)
    } catch {
      // The choice still holds for this visit; it just is not remembered.
    }
  }, [key, view])

  return { view, setView }
}

export function ProjectGoals() {
  const { projectKey } = useParams({ from: '/p/$projectKey/goals' })
  const queryClient = useQueryClient()
  const { message, announce } = useAnnouncer()
  const [editing, setEditing] = useState<Goal | 'new' | null>(null)
  const { view, setView } = useGoalView(projectKey)

  const goals = useQuery({
    queryKey: ['goals', projectKey],
    queryFn: () => api.listGoals(projectKey),
  })
  /** For the `>` tags in a goal's description, drawn as links to the files. */
  const files = useProjectFiles(projectKey)
  const members = useQuery({
    queryKey: ['members', projectKey],
    queryFn: () => api.listMembers(projectKey),
  })

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['goals', projectKey] }),
      queryClient.invalidateQueries({ queryKey: ['goal'] }),
      queryClient.invalidateQueries({ queryKey: ['tasks', projectKey] }),
      queryClient.invalidateQueries({ queryKey: ['project-summary', projectKey] }),
    ])
  }

  if (goals.isPending) return <EmptyState>Loading goals…</EmptyState>
  if (goals.error) return <ErrorBanner>{goals.error.message}</ErrorBanner>

  const all = goals.data
  const open = all.filter((goal) => goal.status === 'open')
  const settled = all.filter((goal) => goal.status !== 'open')

  return (
    <>
      <PageHead
        title="Goals"
        actions={
          <>
            <ViewToggle view={view} onPick={setView} />
            <Button variant="go" onClick={() => setEditing('new')}>
              + New goal
            </Button>
          </>
        }
      >
        What the board's cards are work towards. A card wears its goal's colour on the rail down its
        left-hand side, so a column shows which goals it is made of before a word of it is read.
      </PageHead>

      <LiveRegion message={message} />

      {all.length === 0 ? (
        <EmptyState>
          No goals yet. A goal is a heading — “Search revamp”, “SOC 2 readiness” — that a board's
          cards are written under.
        </EmptyState>
      ) : null}

      {view === 'calendar' && all.length ? (
        <GoalCalendar goals={all} projectKey={projectKey} />
      ) : null}

      {view === 'list' && open.length ? (
        <div className={styles.grid}>
          {open.map((goal) => (
            <GoalCard
              key={goal.id}
              goal={goal}
              projectKey={projectKey}
              members={members.data?.members ?? []}
              files={files}
            />
          ))}
        </div>
      ) : null}

      {view === 'list' && settled.length ? (
        <>
          {/* Kept on the page rather than hidden behind a filter: a goal that
              was reached is the most useful thing a goals page holds three
              months later, and one that was dropped is the second. */}
          <h2 className={styles.settledHead}>Settled</h2>
          <div className={styles.grid}>
            {settled.map((goal) => (
              <GoalCard
                key={goal.id}
                goal={goal}
                projectKey={projectKey}
                members={members.data?.members ?? []}
                files={files}
              />
            ))}
          </div>
        </>
      ) : null}

      {editing ? (
        <GoalDialog
          projectKey={projectKey}
          goal={editing === 'new' ? null : editing}
          members={members.data?.members ?? []}
          announce={announce}
          onDone={refresh}
          onClose={() => setEditing(null)}
        />
      ) : null}
    </>
  )
}

/**
 * List or calendar.
 *
 * A radio group rather than two buttons: it is one choice with one answer, so
 * a screen reader should say "1 of 2" and the arrow keys should move between
 * them — the same control the vault uses for its kinds and the header for its
 * themes.
 */
function ViewToggle({ view, onPick }: { view: View; onPick: (view: View) => void }) {
  const group = useRef<HTMLDivElement>(null)

  function onKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    const step = event.key === 'ArrowRight' || event.key === 'ArrowDown' ? 1 : -1
    if (!['ArrowRight', 'ArrowDown', 'ArrowLeft', 'ArrowUp'].includes(event.key)) return

    event.preventDefault()
    const at = VIEWS.findIndex((option) => option.value === view)
    const next = VIEWS[(at + step + VIEWS.length) % VIEWS.length]
    if (next === undefined) return

    onPick(next.value)
    // Focus follows the choice, or the group's single tab stop is left on the
    // option the reader has just moved away from.
    group.current?.querySelector<HTMLElement>(`[data-view="${next.value}"]`)?.focus()
  }

  return (
    <div
      ref={group}
      role="radiogroup"
      aria-label="How to read the goals"
      className={styles.segmented}
      onKeyDown={onKeyDown}
    >
      {VIEWS.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          data-view={option.value}
          aria-checked={option.value === view}
          tabIndex={option.value === view ? 0 : -1}
          className={option.value === view ? styles.on : undefined}
          onClick={() => onPick(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

/**
 * The same goals, laid on the days they are wanted by.
 *
 * Every goal is drawn, settled ones included but greyed: a month whose targets
 * were all met should look like a month whose targets were all met, not an
 * empty one. A goal with no target date is not on any day, so those go in a
 * strip underneath rather than being quietly left out — a calendar that hides
 * a third of the list is how a deadline gets missed.
 */
function GoalCalendar({ goals, projectKey }: { goals: Goal[]; projectKey: string }) {
  const [month, setMonth] = useState(() => openingMonth(goals))
  const { dated, undated } = useMemo(() => goalsByDate(goals), [goals])
  const weeks = useMemo(() => monthWeeks(month.getFullYear(), month.getMonth()), [month])

  const step = (by: number) => setMonth(new Date(month.getFullYear(), month.getMonth() + by, 1))
  const today = new Date()
  const onThisMonth =
    month.getFullYear() === today.getFullYear() && month.getMonth() === today.getMonth()
  const shown = weeks
    .flat()
    .filter((day) => day.inMonth)
    .reduce((count, day) => count + (dated.get(day.iso)?.length ?? 0), 0)

  return (
    <section className={styles.calendar} aria-label="Goals by target date">
      <div className={styles.monthBar}>
        <h2 className={styles.month}>{monthName(month.getFullYear(), month.getMonth())}</h2>
        <div className={styles.monthNav}>
          <Button small onClick={() => step(-1)} aria-label="Previous month">
            ‹
          </Button>
          <Button
            small
            onClick={() => setMonth(new Date(today.getFullYear(), today.getMonth(), 1))}
            disabled={onThisMonth}
          >
            Today
          </Button>
          <Button small onClick={() => step(1)} aria-label="Next month">
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
            const on = dated.get(day.iso) ?? []
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
                {on.map((goal) => (
                  <GoalPin key={goal.id} goal={goal} projectKey={projectKey} />
                ))}
              </div>
            )
          })}
        </div>
      </div>

      {/* Said in words as well as drawn, because "nothing here" and "nothing
          loaded" look identical on an empty grid. */}
      {shown === 0 ? <p className={styles.quiet}>Nothing is targeted in this month.</p> : null}

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
  const { total, done } = goal.progress
  const said =
    goal.status === 'open'
      ? total === 0
        ? 'no cards yet'
        : `${done} of ${total} done`
      : GOAL_STATUS_LABELS[goal.status].toLowerCase()

  return (
    <Link
      to="/p/$projectKey/goals/$goalRef"
      params={{ projectKey, goalRef: goal.reference }}
      className={`${styles.pin} ${goal.status === 'open' ? '' : styles.pinSettled}`}
      style={{ '--goal': goal.colour } as React.CSSProperties}
      title={`${goal.reference} — ${goal.name} (${said})`}
    >
      <span className={styles.pinRef}>{goal.reference}</span>
      <span className={styles.pinName}>{goal.name}</span>
    </Link>
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
      style={{ '--goal': goal.colour } as React.CSSProperties}
    >
      <div className={styles.goalHead}>
        <span className={styles.reference}>{goal.reference}</span>
        {goal.status === 'open' ? null : (
          <span className={`${styles.pill} ${styles[goal.status]}`}>
            {GOAL_STATUS_LABELS[goal.status]}
          </span>
        )}
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
