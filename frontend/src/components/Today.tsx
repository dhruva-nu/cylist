/**
 * Today's work, in the sidebar.
 *
 * What is wanted by now on the board you are looking at, your own cards first
 * — the list you would otherwise assemble by reading a column and doing the
 * date arithmetic yourself. Which cards those are and what order they come in
 * is `today.ts`; this file is only how they are drawn and where the rows go.
 *
 * Rows are links to the board filtered to the card, not to the card itself:
 * there is no route for one card — it opens as a dialog over its own board —
 * so the honest destination is the board with that card picked out of it. The
 * search box the board already has is what does the picking.
 *
 * Scoped to the project you are in. The panel is in the frame and so is on
 * screen everywhere, but the day report beside it is a project's day and the
 * board's own definition of finished is a project's last column — a list
 * spanning every project would be answering a different question from its
 * neighbours, and there is no endpoint that answers it in one request. Off a
 * project it says so rather than showing an empty list, which would read as a
 * day with nothing in it.
 */

import { useQuery } from '@tanstack/react-query'
import { Link, useMatchRoute } from '@tanstack/react-router'
import { type ReactNode } from 'react'
import { api, type Task } from '../api/client'
import { formatDue, formatDueLong, localToday } from './dates'
import { SidebarSection } from './SidebarSection'
import { PriorityIcon } from './ui'
import { todaysWork, todaysWorkCount, type TodayWork } from './today'
import styles from './Today.module.css'

const PRIORITY_LABELS = {
  urgent: 'Urgent',
  asap: 'ASAP',
  week: 'This week',
  someday: 'Someday',
} as const

const STATUS_LABELS = {
  active: 'Active',
  hold: 'On hold',
  blocked: 'Blocked',
  cancelled: 'Cancelled',
} as const

export function Today({ folded, onToggle }: { folded: boolean; onToggle: () => void }) {
  const matchRoute = useMatchRoute()
  const match = matchRoute({ to: '/p/$projectKey', fuzzy: true })
  const projectKey = match ? match.projectKey : null

  return (
    <SidebarSection
      name="Today"
      note={<Count projectKey={projectKey} />}
      folded={folded}
      onToggle={onToggle}
    >
      {projectKey === null ? (
        <p className={styles.aside}>Open a project to see what is due on it today.</p>
      ) : (
        <List projectKey={projectKey} />
      )}
    </SidebarSection>
  )
}

/**
 * The board, its columns and who you are, turned into today's two groups.
 *
 * All three are keys the rest of the app already holds — the board fetches the
 * same tasks and columns, and `/me` is fetched once by the authentication gate
 * — so opening this beside a board costs nothing but the arithmetic.
 */
function useTodaysWork(projectKey: string | null): TodayWork | null {
  const enabled = projectKey !== null
  const tasks = useQuery({
    queryKey: ['tasks', projectKey],
    queryFn: () => api.listTasks(projectKey ?? ''),
    enabled,
  })
  const board = useQuery({
    queryKey: ['board', projectKey],
    queryFn: () => api.listColumns(projectKey ?? ''),
    enabled,
  })
  const identity = useQuery({ queryKey: ['me'], queryFn: api.me })

  if (!tasks.data || !board.data) return null
  return todaysWork(tasks.data, board.data.columns, identity.data?.person ?? null, localToday())
}

/**
 * How much is in there, on the heading.
 *
 * The point of the count is the folded section: a panel folded away with three
 * overdue cards behind it has to say so, or folding it is how you stop seeing
 * your own work. Overdue turns it red, because that is the part that changes
 * what you do next.
 */
function Count({ projectKey }: { projectKey: string | null }) {
  const work = useTodaysWork(projectKey)
  if (!work) return null

  const total = todaysWorkCount(work)
  if (total === 0) return null

  return (
    <span className={work.overdue.length ? styles.countLate : styles.count}>
      {total}
      <span className="visually-hidden">
        {total === 1 ? ' card due' : ' cards due'}
        {work.overdue.length ? `, ${work.overdue.length} overdue` : ''}
      </span>
    </span>
  )
}

function List({ projectKey }: { projectKey: string }) {
  const work = useTodaysWork(projectKey)

  if (!work) return <p className={styles.aside}>Reading the board…</p>

  if (todaysWorkCount(work) === 0) {
    return <p className={styles.aside}>Nothing due today. Nothing late, either.</p>
  }

  return (
    <div className={styles.groups}>
      {work.overdue.length ? (
        <Group name="Overdue" late>
          {work.overdue.map((task) => (
            <Row key={task.id} task={task} projectKey={projectKey} late />
          ))}
        </Group>
      ) : null}
      {work.due.length ? (
        <Group name={`Due today · ${formatDue(localToday())}`}>
          {work.due.map((task) => (
            <Row key={task.id} task={task} projectKey={projectKey} />
          ))}
        </Group>
      ) : null}
    </div>
  )
}

function Group({
  name,
  late = false,
  children,
}: {
  name: string
  late?: boolean
  children: ReactNode
}) {
  return (
    <div>
      <h3 className={late ? styles.groupNameLate : styles.groupName}>{name}</h3>
      <ul className={styles.rows}>{children}</ul>
    </div>
  )
}

/**
 * One card: what it is, and how it stands.
 *
 * The reference and the title on the first line, the marks on the second —
 * the board's own two-part shape, at the width a sidebar has. A card wanted
 * today says nothing about its date, because the group heading already did;
 * an overdue one says how far past it is, which is the only thing that
 * distinguishes it from the card above.
 */
function Row({
  task,
  projectKey,
  late = false,
}: {
  task: Task
  projectKey: string
  late?: boolean
}) {
  // A card that is active, on the baseline priority and not late has nothing
  // to add — and an empty row still takes the gap above it, which reads as a
  // mark that failed to draw.
  const marked = task.priority !== 'someday' || task.status !== 'active' || late

  return (
    <li>
      <Link
        to="/p/$projectKey/board"
        params={{ projectKey }}
        // The board's search box takes the reference, so the card arrives
        // picked out of its column rather than left for you to find.
        search={{ q: task.reference }}
        className={styles.row}
      >
        <span className={styles.head}>
          <span className={styles.reference}>{task.reference}</span>
          <span className={styles.title}>{task.title}</span>
        </span>
        {marked ? (
          <span className={styles.marks}>
            {/* Someday is the baseline every card starts on, so flagging it here
                would be noise on every row — the same reasoning the board's
                cards use. */}
            {task.priority !== 'someday' ? (
              <span
                className={`${styles.prio} ${styles[`priority_${task.priority}`]}`}
                title={PRIORITY_LABELS[task.priority]}
              >
                <PriorityIcon priority={task.priority} size={13} />
                <span className="visually-hidden">{PRIORITY_LABELS[task.priority]}</span>
              </span>
            ) : null}
            {/* Blocked and on hold are in this list on purpose — a blocked
                card that was wanted yesterday is the most useful thing here —
                so each says which it is rather than sitting among the
                actionable ones looking like one of them. */}
            {task.status === 'active' ? null : (
              <span className={`${styles.pill} ${styles[`pill_${task.status}`]}`}>
                {STATUS_LABELS[task.status]}
              </span>
            )}
            {late && task.due_date ? (
              <span className={styles.late} title={formatDueLong(task.due_date)}>
                {formatDue(task.due_date)}
              </span>
            ) : null}
          </span>
        ) : null}
      </Link>
    </li>
  )
}
