/**
 * Today's work: a button in the sidebar, and the card it pops out.
 *
 * What is wanted by now on the board you are looking at, your own cards first
 * — the list you would otherwise assemble by reading a column and doing the
 * date arithmetic yourself. Which cards those are and what order they come in
 * is `today.ts`; this file is only how they are drawn and where the rows go.
 *
 * The button carries the count, and that is what makes a button the right
 * shape here rather than a section that folds. The one thing a glance at the
 * sidebar has to answer is "is there anything on me today" — a number, red if
 * any of it is late — and the list behind it is what you read once the number
 * has told you to. So the number is always on screen and the list is a click
 * away, which is the same bargain a folded section offered without the fold
 * left over.
 *
 * Rows open the card. There is no route for one — it is a dialog wherever it
 * is opened from — so `OpenCard` puts it over this list, and closing it leaves
 * the list where it was. They used to be links to the board filtered to the
 * reference, which was the honest destination while a card could only be
 * opened from the board it sits on; now that one can be opened from anywhere,
 * a detour through a search box is just a detour.
 *
 * Scoped to the project you are in. The panel is in the frame and so is on
 * screen everywhere, but the board's own definition of finished is a
 * project's last column — a list spanning every project would be answering a
 * different question, and there is no endpoint that answers it in one
 * request. Off a project the button says so and does nothing.
 */

import { useQuery } from '@tanstack/react-query'
import { useMatchRoute } from '@tanstack/react-router'
import { useState, type ReactNode } from 'react'
import { api, type Task } from '../api/client'
import { formatDue, formatDueLong, localToday } from './dates'
import { Modal, ModalBody } from './Modal'
import { OpenCard } from './OpenCard'
import { SidebarAction } from './SidebarSection'
import { Button, PriorityIcon } from './ui'
import { todaysWork, todaysWorkCount, type TodayWork } from './today'
import styles from './Today.module.css'

/** The gloss rather than the bare level, because a row shows the icon and this
 * is what a pointer or a screen reader gets instead of it. Worded as the board
 * words them — see `PRIORITY_LABELS` in `ProjectBoard.tsx`. */
const PRIORITY_LABELS = {
  p0: 'P0 — drop what you are doing',
  p1: 'P1 — as soon as P0 is clear',
  p2: 'P2 — this week',
  p3: 'P3 — some time',
} as const

const STATUS_LABELS = {
  active: 'Active',
  hold: 'On hold',
  blocked: 'Blocked',
  cancelled: 'Cancelled',
} as const

export function Today() {
  const matchRoute = useMatchRoute()
  const match = matchRoute({ to: '/p/$projectKey', fuzzy: true })
  const projectKey = match ? match.projectKey : null

  const [listing, setListing] = useState(false)
  const [reading, setReading] = useState<string | null>(null)

  return (
    /* The count rides on the strip. It is the reason to glance at the sidebar
       at all, so it stays visible whether or not the list behind it has been
       asked for. */
    <SidebarAction
      name="Today"
      note={<Count projectKey={projectKey} />}
      disabled={projectKey === null}
      title={projectKey === null ? 'Open a project to see what is due on it today' : undefined}
      onOpen={() => setListing(true)}
    >
      {listing && projectKey !== null ? (
        <TodayDialog
          projectKey={projectKey}
          onOpenCard={setReading}
          onClose={() => setListing(false)}
        />
      ) : null}

      {/* A sibling of the list rather than a child of it, so the card comes
          out on top by being second and closing it leaves the list alone. */}
      {reading !== null && projectKey !== null ? (
        <OpenCard
          projectKey={projectKey}
          reference={reading}
          onOpen={setReading}
          onClose={() => setReading(null)}
        />
      ) : null}
    </SidebarAction>
  )
}

function TodayDialog({
  projectKey,
  onOpenCard,
  onClose,
}: {
  projectKey: string
  onOpenCard: (reference: string) => void
  onClose: () => void
}) {
  return (
    <Modal title="Today" onClose={onClose} footer={<Button onClick={onClose}>Close</Button>}>
      <ModalBody>
        <List projectKey={projectKey} onOpen={onOpenCard} />
      </ModalBody>
    </Modal>
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
 * The same count, for a sidebar that has been collapsed to its rail.
 *
 * It works out the project itself rather than being handed one, because the
 * rail renders none of this file's other parts and there is nothing up there
 * that already knows. Exported for exactly one caller — see the note in
 * `Sidebar.tsx` about a count nobody can see.
 */
export function TodayCount() {
  const matchRoute = useMatchRoute()
  const match = matchRoute({ to: '/p/$projectKey', fuzzy: true })

  return <Count projectKey={match ? match.projectKey : null} />
}

/**
 * How much is in there, on the button.
 *
 * The point of the count is that the list is not on screen: a button with
 * three overdue cards behind it has to say so, or asking for the list is
 * something you would have to remember to do. Same on the rail, where the
 * button is not on screen either. Overdue turns it red, because that is the
 * part that changes what you do next.
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

function List({ projectKey, onOpen }: { projectKey: string; onOpen: (reference: string) => void }) {
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
            <Row key={task.id} task={task} onOpen={onOpen} late />
          ))}
        </Group>
      ) : null}
      {work.due.length ? (
        <Group name={`Due today · ${formatDue(localToday())}`}>
          {work.due.map((task) => (
            <Row key={task.id} task={task} onOpen={onOpen} />
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
 * the board's own two-part shape. A card wanted today says nothing about its
 * date, because the group heading already did; an overdue one says how far
 * past it is, which is the only thing that distinguishes it from the card
 * above.
 *
 * A button rather than the link it used to be. The list is a dialog now, and
 * a link inside one navigates the page behind it and leaves the dialog
 * standing over a screen that has moved on.
 */
function Row({
  task,
  onOpen,
  late = false,
}: {
  task: Task
  onOpen: (reference: string) => void
  late?: boolean
}) {
  // A card that is active, on the baseline priority and not late has nothing
  // to add — and an empty row still takes the gap above it, which reads as a
  // mark that failed to draw.
  const marked = task.priority !== 'p3' || task.status !== 'active' || late

  return (
    <li>
      <button
        type="button"
        className={styles.row}
        title={`Open ${task.reference}`}
        onClick={() => onOpen(task.reference)}
      >
        <span className={styles.head}>
          <span className={styles.reference}>{task.reference}</span>
          <span className={styles.title}>{task.title}</span>
        </span>
        {marked ? (
          <span className={styles.marks}>
            {/* P3 is the baseline every card starts on, so flagging it here
                would be noise on every row — the same reasoning the board's
                cards use. */}
            {task.priority !== 'p3' ? (
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
            {/* The date that is actually owed, which is the one the row was
                picked for — a card whose review column was due on Monday is
                late by Monday, not by whenever it is meant to ship. */}
            {late && task.next_due_date ? (
              <span className={styles.late} title={formatDueLong(task.next_due_date)}>
                {formatDue(task.next_due_date)}
              </span>
            ) : null}
          </span>
        ) : null}
      </button>
    </li>
  )
}
