/**
 * The task dialog: every field of a card, plus its timeline.
 *
 * Opening a card lands on a detail view. Every field of the card is text
 * there — no input, select or textarea is rendered for any of them — so a card
 * can be read, and read out, with no way to change it by accident. Editing is
 * behind the pencil in the header (and the Edit button beside it in the
 * footer); pressing either swaps the same dialog over to the form below.
 * Cancel, Escape and the backdrop go back to the detail view rather than
 * throwing the whole dialog away, so a mis-click costs one keystroke instead
 * of your place on the board. A new task has nothing to read yet, so it opens
 * straight on the form.
 *
 * The one control on the detail view that is not Edit or Close is the tick box
 * beside a sub-task, and the line it sits on the right side of is *what a
 * control changes*, not whether one exists: ticking a sub-task off settles a
 * different item, and making that go through the pencil would mean opening a
 * form over every field of the parent to finish something that is not the
 * parent.
 *
 * The form is tabbed. Basics holds what the card *is* — its name, what done
 * looks like, how urgent, what kind, and which template it follows; Details
 * holds how it is tracked. On an existing card a third tab carries its
 * sub-tasks and its conversation, which are about other work and about people
 * rather than about this card's fields. Every panel stays mounted, so tabbing
 * away never discards what has been typed, and a tab carries a dot while
 * something required on it is still blank — a disabled Save button has to have
 * somewhere to point.
 *
 * Neither column nor status appears on a *new* card: work enters the board at
 * the first column and starts out active, and a control with one possible
 * value is a question that reads as though it had an answer.
 *
 * The status control is the interesting part. Choosing On hold or Blocked
 * reveals a reason box and a picker of people to tag, and neither the button
 * nor the server will let the change through without a reason — a red card
 * that does not say why is a question rather than information.
 *
 * Saving may be several requests: the fields, then a move, then the status
 * change, then a comment. They go in that order so the status entry lands on a
 * card that already reads the way it will after the save.
 *
 * Under the timeline sits the card's history: what was changed, when, and who
 * changed it. Closed until asked for, and read from the server's own wording
 * rather than reconstructed here — see `History`.
 *
 * Sub-tasks come in two kinds, and both are ticked off in place — from either
 * half of the dialog — rather than on the Save button: a checkbox that only
 * takes effect when you remember to press Save is a checkbox that lies. Neither
 * kind is on the board, so neither is finished by going anywhere: a tick box
 * carries a state, a sub-task carries a finishing time, and one gesture settles
 * either. What a sub-task's *fields* say is never edited here: "Split into a
 * sub-task" hands the job back to the board, which opens a second dialog for
 * it, because a sub-task needs every field a card needs.
 *
 * A sub-task opened here is the same dialog with one thing missing — the column
 * — and one thing in its place: whether it is finished. It is the only field a
 * card has that a sub-task does not, and the only field a sub-task has that a
 * card does not.
 */

import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useState, type KeyboardEvent, type ReactNode } from 'react'
import {
  api,
  type AgentSessionRead,
  type BoardColumn,
  type ChecklistItem,
  type ChecklistState,
  type ColumnDueDate,
  type Goal,
  type Person,
  type Task,
  type TaskComment,
  type TaskDetail,
  type TaskHistoryEntry,
  type TaskInput,
  type TaskPriority,
  type TaskStatus,
  type TaskType,
  type Template,
} from '../api/client'
import { agentIndicator, silentFor, waitingDetail } from '../routes/agentState'
import { GoalChip } from './GoalMarks'
import { Field, FieldPair, Modal, ModalBody } from './Modal'
import { MentionBox } from './Mentions'
import {
  Avatar,
  Button,
  ErrorBanner,
  PriorityIcon,
  SubStatusBar,
  Tagged,
  TaskRef,
  TypeIcon,
} from './ui'
import styles from './TaskDialog.module.css'

const STATUSES: { value: TaskStatus; label: string }[] = [
  { value: 'active', label: 'Active' },
  { value: 'hold', label: 'On hold' },
  { value: 'blocked', label: 'Blocked' },
  { value: 'cancelled', label: 'Cancelled' },
]

const TYPES: TaskType[] = ['feature', 'bug', 'chore']

/**
 * The four levels, and what each one asks of whoever picks the card up.
 *
 * The label is the level alone — it is what the chip and the board carry, and
 * a level is the whole of what P0 means to anyone who has read one before. The
 * gloss is for the picker, where somebody is choosing between them.
 */
const PRIORITIES: { value: TaskPriority; label: string; means: string }[] = [
  { value: 'p0', label: 'P0', means: 'drop what you are doing' },
  { value: 'p1', label: 'P1', means: 'as soon as P0 is clear' },
  { value: 'p2', label: 'P2', means: 'this week' },
  { value: 'p3', label: 'P3', means: 'some time' },
]

interface DialogProps {
  projectKey: string
  /** Null opens the dialog for a new task, which is locked to the first column. */
  taskId: string | null
  /**
   * Set on a new task to make it a sub-task of that reference. Ignored when
   * `taskId` is set: a card's parentage is decided when it is created.
   */
  parentRef?: string | null
  columns: BoardColumn[]
  firstColumn: BoardColumn
  /**
   * The project's task templates, for the picker on the form.
   *
   * Passed in rather than fetched here because the board needs them too: it
   * greys out the columns a card being dragged is not allowed in, which is the
   * same rule read from the other end.
   */
  templates: Template[]
  /**
   * The project's goals, for the picker on the form.
   *
   * Open ones and settled ones alike: a card can be moved onto a goal that has
   * already been achieved — that is how a straggler gets counted — and a
   * picker that hid the goal a card is already on would look like the card had
   * lost it.
   */
  goals: Goal[]
  /** A new card starts on this goal. The board's lanes pass their own. */
  defaultGoalId?: string | null
  /** Opens another card in this dialog's place — a sub-task, from the list. */
  onOpenTask?: ((taskRef: string) => void) | undefined
  /** Asks the board to open a new-sub-task dialog under this reference. */
  onSplit?: ((parentRef: string) => void) | undefined
  /**
   * Where to say what happened. The dialog closes on success, taking any live
   * region inside it with it before a screen reader could read one, so the
   * board keeps the region and the dialog only supplies the words.
   */
  announce: (message: string) => void
  onDone: () => Promise<void>
  onClose: () => void
}

export function TaskDialog(props: DialogProps) {
  const { projectKey, taskId, onClose } = props

  const task = useQuery({
    queryKey: ['task', taskId],
    queryFn: () => api.getTask(taskId ?? ''),
    enabled: taskId !== null,
  })
  const members = useQuery({
    queryKey: ['members', projectKey],
    queryFn: () => api.listMembers(projectKey),
  })

  const loading = (taskId !== null && task.isPending) || members.isPending
  const error = task.error ?? members.error

  if (loading || error || !members.data) {
    return (
      <Modal
        title={taskId ? 'Task' : 'New task'}
        onClose={onClose}
        footer={<Button onClick={onClose}>Close</Button>}
      >
        <ModalBody>
          {error ? <ErrorBanner>{error.message}</ErrorBanner> : <p>Loading…</p>}
        </ModalBody>
      </Modal>
    )
  }

  // Remounting per task keeps the state honest: a dialog opened on a different
  // card must not inherit the last one's half-typed edits, nor its edit mode.
  const detail = task.data ?? null
  if (detail === null) {
    return (
      <TaskForm
        key="new"
        {...props}
        task={null}
        members={members.data.members}
        onCancel={onClose}
      />
    )
  }

  return <TaskPanel key={detail.id} {...props} task={detail} members={members.data.members} />
}

/**
 * One card: read it, then choose to edit it.
 *
 * The mode lives here rather than in either half, so that leaving the form
 * unmounts it — cancelling really does discard the edits rather than leaving
 * them parked behind a flag.
 */
function TaskPanel(props: DialogProps & { task: TaskDetail; members: Person[] }) {
  const [editing, setEditing] = useState(false)

  if (editing) return <TaskForm {...props} onCancel={() => setEditing(false)} />
  return <TaskDetailView {...props} onEdit={() => setEditing(true)} />
}

/**
 * The detail view of a card: read it, and tick its sub-tasks off.
 *
 * Deliberately plain markup: a card's own fields are text here, not disabled
 * inputs. A disabled input still looks like somewhere to type and still has to
 * be kept in sync with a form; text cannot be saved by mistake at all. Nothing
 * on this view can alter a field of this card — Edit, Close, and the sub-task
 * list, which finishes work that belongs to other cards. See `SubtasksRead`.
 */
function TaskDetailView({
  projectKey,
  task,
  members,
  columns,
  onOpenTask,
  announce,
  onDone,
  onEdit,
  onClose,
}: DialogProps & { task: TaskDetail; members: Person[]; onEdit: () => void }) {
  const column = columns.find((candidate) => candidate.id === task.column_id)
  // A card that owes nothing is never late: once it is done, the day it was
  // wanted done is a fact about the past rather than something outstanding.
  const late = task.next_due_date !== null && isOverdue(task.due_date)
  const statusLabel = STATUSES.find((option) => option.value === task.status)?.label ?? task.status

  const edit = (
    <Button variant="ghost" small onClick={onEdit} aria-label="Edit task" title="Edit task">
      ✎
    </Button>
  )

  return (
    <Modal
      title={task.reference}
      onClose={onClose}
      headerActions={edit}
      footer={
        <>
          <Button onClick={onClose}>Close</Button>
          <Button variant="go" onClick={onEdit}>
            ✎ Edit
          </Button>
        </>
      }
    >
      <ModalBody>
        <h3 className={styles.readTitle}>{task.title}</h3>

        <div className={styles.chips}>
          {/* The same two icons the board card carries, with the words kept:
              a dialog has the room the card does not, and this is where you
              come to read the card rather than scan it. */}
          <span className={`${styles.chip} ${styles[`type_${task.type}`]}`}>
            <TypeIcon type={task.type} />
            {task.type}
          </span>
          <span className={`${styles.chip} ${styles[`priority_${task.priority}`]}`}>
            <PriorityIcon priority={task.priority} />
            {PRIORITIES.find((option) => option.value === task.priority)?.label ?? task.priority}
          </span>
          <span className={`${styles.chip} ${styles[`state_${task.status}`]}`}>{statusLabel}</span>
          {/* Last, and worded rather than iconised: a template is the only
              chip here that names something the project invented. */}
          {task.template_name ? <span className={styles.chip}>◇ {task.template_name}</span> : null}
        </div>

        {/* Under the chips rather than among them: the goal is the one thing
            on this card that is somewhere else as well, so it is a link, and a
            link sitting in a row of chips reads as a chip that is broken. */}
        {task.goal_id && task.goal_name && task.goal_colour ? (
          <ReadField label="Goal">
            <Link
              to="/p/$projectKey/goals/$goalRef"
              params={{ projectKey, goalRef: task.goal_reference ?? task.goal_id }}
              className={styles.goalLink}
              onClick={onClose}
            >
              <GoalChip name={task.goal_name} colour={task.goal_colour} />
              <span className={styles.goalRef}>{task.goal_reference}</span>
            </Link>
          </ReadField>
        ) : null}

        {task.sub_statuses.length ? (
          <ReadField label="Sub-status">
            <SubStatusBar
              labels={task.sub_statuses}
              index={task.sub_status_index ?? 0}
              members={members}
              wrap
            />
          </ReadField>
        ) : null}

        <ReadField label="Description">
          <p className={styles.prose}>
            <Tagged text={task.description} members={members} />
          </p>
        </ReadField>

        <div className={styles.readPair}>
          <ReadField label="Assignee">
            <span className={styles.person}>
              <Avatar name={task.assignee.name} colour={task.assignee.colour} />
              {task.assignee.name}
              <span className={styles.role}>· {task.assignee.role.split(',')[0]}</span>
            </span>
          </ReadField>
          <ReadField label="Due date">
            <span className={late ? styles.late : ''}>
              {late ? '⚠ ' : ''}
              {formatDue(task.due_date)}
            </span>
          </ReadField>
        </div>

        {/* The dates before the last column's, which is the one above. A date
            the card has already reached is kept rather than dropped — it is
            what the schedule was, and reading only the dates still ahead would
            make a card halfway through look like a card that was never
            scheduled — but it is said quietly and asks for nothing. */}
        {task.column_due_dates.length ? (
          <ReadField label="Column dates">
            <ul className={styles.columnDates}>
              {task.column_due_dates.map((entry) => (
                <ColumnDateRead key={entry.column_id} entry={entry} />
              ))}
            </ul>
          </ReadField>
        ) : null}

        <div className={styles.readPair}>
          {/* A sub-task is in no column, so the row that would name one says
              the thing that answers the same question for it instead. An empty
              Column would read as one that failed to load. */}
          {task.parent_id === null ? (
            // The column is where a card is; the date beside it is when being
            // there started meaning done. Shown only once there is one, so a
            // card still on its way says where it is and nothing more.
            <ReadField label="Column">
              {column?.name ?? '—'}
              {task.outcome ? <span className={styles.role}>· {task.outcome}</span> : null}
              {task.finished_at ? (
                <span className={styles.role}>
                  · finished {formatDue(task.finished_at.slice(0, 10))}
                </span>
              ) : null}
            </ReadField>
          ) : (
            <ReadField label="Finished">
              {task.finished_at ? formatDue(task.finished_at.slice(0, 10)) : 'Not yet'}
            </ReadField>
          )}
          <ReadField label="Waiting on">
            {task.waiting_on.length ? task.waiting_on.map((person) => person.name).join(', ') : '—'}
          </ReadField>
        </div>

        {task.jira_ref || task.pr_ref ? (
          <div className={styles.readPair}>
            <ReadField label="Jira">
              {task.jira_ref ? <TaskRef kind="jira" value={task.jira_ref} /> : '—'}
            </ReadField>
            <ReadField label="Pull request">
              {task.pr_ref ? <TaskRef kind="pr" value={task.pr_ref} /> : '—'}
            </ReadField>
          </div>
        ) : null}

        <ReadField label="Sub-tasks">
          <SubtasksRead
            task={task}
            members={members}
            onOpenTask={onOpenTask}
            announce={announce}
            onDone={onDone}
          />
        </ReadField>

        {task.agent_sessions.length ? (
          <ReadField label="Agents">
            <AgentSessions
              taskRef={task.reference}
              taskId={task.id}
              sessions={task.agent_sessions}
            />
          </ReadField>
        ) : null}

        <ReadField label="Timeline">
          <div className={styles.timeline}>
            {task.comments.length ? (
              task.comments.map((entry) => <Entry key={entry.id} entry={entry} members={members} />)
            ) : (
              <span className={styles.empty}>Nothing has been said about this card yet.</span>
            )}
          </div>
        </ReadField>

        <History taskId={task.id} />
      </ModalBody>
    </Modal>
  )
}

/**
 * The Claude Code sessions that have been on this card, and what became of them.
 *
 * The card's border says one thing about all of them — whichever needs a human
 * first. This is where the rest is: which session, since when, and how long
 * ago it last said anything. A card worked on from two terminals shows two
 * rows, which is the whole reason the border carries a count.
 *
 * Dismiss clears the finished ones. It appears only once there is something
 * to clear: a session still running cannot be dismissed, only stopped, and a
 * button that would refuse is worse than one that is not there. Editing the
 * card does the same thing without the button — the server treats a person
 * touching the card as having seen what the agent left.
 */
function AgentSessions({
  taskRef,
  taskId,
  sessions,
}: {
  taskRef: string
  taskId: string
  sessions: AgentSessionRead[]
}) {
  const queryClient = useQueryClient()
  const now = new Date()
  const finished = sessions.filter((session) => session.ended_at !== null)

  const dismiss = useMutation({
    mutationFn: () => api.dismissAgentSessions(taskRef),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['task', taskId] }),
        queryClient.invalidateQueries({ queryKey: ['tasks'] }),
        queryClient.invalidateQueries({ queryKey: ['board'] }),
      ])
    },
  })

  return (
    <div className={styles.agentSessions}>
      {dismiss.error ? <ErrorBanner>{dismiss.error.message}</ErrorBanner> : null}

      {sessions.map((session) => (
        <div key={session.id} className={styles.agentSession}>
          <div className={styles.agentSessionLine}>
            <span className={styles.agentSessionName}>
              {session.client_name ?? session.actor_label}
            </span>
            <span className={styles.agentSessionState}>{sessionState(session, now)}</span>
          </div>
          <div className={styles.agentSessionWhen}>
            {/* The record is what says a person did not do this — the same
                wording the history below uses. */}
            <span className={styles.agent}>agent</span>
            {session.actor_label}
            <span className={styles.role}>
              · started {formatWhen(session.started_at)} · last seen{' '}
              {silentFor(session.last_seen_at, now)} ago
            </span>
          </div>
        </div>
      ))}

      {finished.length ? (
        <div className={styles.agentSessionActions}>
          <Button small onClick={() => dismiss.mutate()} disabled={dismiss.isPending}>
            {dismiss.isPending ? 'Dismissing…' : 'Dismiss'}
          </Button>
          <span className={styles.empty}>
            {finished.length === 1
              ? 'Takes the finished session off the board.'
              : `Takes the ${finished.length} finished sessions off the board.`}
          </span>
        </div>
      ) : null}
    </div>
  )
}

/** One session's state, in the words the card's border uses. */
function sessionState(session: AgentSessionRead, now: Date): string {
  if (session.ended_at !== null) {
    return session.reason === 'moved' ? 'moved to another card' : 'finished'
  }
  if (session.state === 'waiting') return `needs you — ${waitingDetail(session.reason)}`
  if (session.is_stale) {
    const indicator = agentIndicator(
      {
        state: 'stale',
        count: 1,
        reason: session.reason,
        client_name: session.client_name,
        since: session.state_changed_at,
        last_seen_at: session.last_seen_at,
      },
      now,
    )
    return indicator?.label.toLowerCase() ?? 'silent'
  }
  return 'working'
}

/** A moment, to the minute, the way the history above renders one. */
function formatWhen(iso: string): string {
  return new Date(iso).toLocaleString('en-GB', {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/**
 * What has been done to this card: every change, when, and who made it.
 *
 * The counterpart to the timeline above it, and deliberately not merged into
 * it. The timeline is what people *said*; this is what was *done* — and the
 * two are read for different reasons. Someone scrolling a card to catch up
 * wants the conversation; someone asking "why does this say Thursday now?"
 * wants the record, and interleaving the two buries each in the other.
 *
 * Closed to begin with, fetched only when it is opened, and then ten entries
 * at a time. A card worked on for a month carries a long record that nobody
 * opened the dialog to read: loading it on every open would slow the common
 * case to serve the rare one, and loading all of it at once would bury the
 * only part most readers want — the last thing that happened.
 *
 * The wording comes from the server rather than from a verb-to-sentence map
 * here, so the board, the CLI and an agent reading the API all tell the same
 * story. This side only decides what the record looks like.
 */
function History({ taskId }: { taskId: string }) {
  const [open, setOpen] = useState(false)
  const [page, setPage] = useState(1)

  const history = useQuery({
    queryKey: ['task-history', taskId, page],
    queryFn: () => api.getTaskHistory(taskId, page),
    enabled: open,
    // The previous page stays on screen while the next one loads. A pager that
    // empties itself between clicks makes the dialog jump under the cursor.
    placeholderData: keepPreviousData,
  })

  const shown = history.data

  return (
    <details
      className={styles.history}
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary className={styles.historySummary}>
        <span className={styles.historyTitle}>History</span>
        <span className={styles.historyHint}>what changed, and who changed it</span>
      </summary>

      {history.error ? <ErrorBanner>{history.error.message}</ErrorBanner> : null}

      <div className={styles.historyList}>
        {shown === undefined && history.isFetching ? (
          <span className={styles.empty}>Loading…</span>
        ) : null}
        {shown?.total === 0 ? (
          <span className={styles.empty}>Nothing has happened to this card yet.</span>
        ) : null}
        {shown?.entries.map((entry) => (
          <HistoryEntry key={entry.id} entry={entry} />
        ))}
      </div>

      {shown && shown.pages > 1 ? (
        <Pager page={shown.page} pages={shown.pages} total={shown.total} onGo={setPage} />
      ) : null}
    </details>
  )
}

/**
 * Which page of the history is showing, and how to reach the others.
 *
 * Every page is a numbered button rather than only Previous and Next: a record
 * is usually read for a particular moment — "what did it look like in March" —
 * and stepping there one page at a time means loading everything in between.
 * A history long enough for that to become a wall of numbers is one nobody
 * navigates by number anyway, so past nine pages the middle is elided.
 */
function Pager({
  page,
  pages,
  total,
  onGo,
}: {
  page: number
  pages: number
  total: number
  onGo: (page: number) => void
}) {
  return (
    <nav className={styles.pager} aria-label="History pages">
      <Button
        small
        variant="ghost"
        disabled={page === 1}
        aria-label="Previous page"
        onClick={() => onGo(page - 1)}
      >
        ‹
      </Button>

      {pageNumbers(page, pages).map((number, index) =>
        number === null ? (
          <span key={`gap-${index}`} className={styles.pagerGap} aria-hidden="true">
            …
          </span>
        ) : (
          <button
            key={number}
            type="button"
            className={number === page ? styles.pageOn : styles.page}
            aria-label={`Page ${number} of ${pages}`}
            aria-current={number === page ? 'page' : undefined}
            onClick={() => onGo(number)}
          >
            {number}
          </button>
        ),
      )}

      <Button
        small
        variant="ghost"
        disabled={page === pages}
        aria-label="Next page"
        onClick={() => onGo(page + 1)}
      >
        ›
      </Button>

      <span className={styles.pagerCount}>{total} entries</span>
    </nav>
  )
}

/** How many page buttons fit before the middle has to be elided. */
const PAGER_WIDTH = 9

/**
 * The page numbers to draw, with `null` standing for an elision.
 *
 * The first and last are always reachable — the beginning and the end of a
 * record are the two moments anybody jumps to — and the rest of the room goes
 * to the pages either side of where you are.
 */
function pageNumbers(page: number, pages: number): (number | null)[] {
  if (pages <= PAGER_WIDTH) return Array.from({ length: pages }, (_, index) => index + 1)

  const span = PAGER_WIDTH - 4 // first, last, and an elision at each end
  const first = Math.min(Math.max(page - (span >> 1), 2), pages - span)
  const middle = Array.from({ length: span }, (_, index) => first + index)

  return [
    1,
    ...(first > 2 ? [null] : []),
    ...middle,
    ...(first + span <= pages - 1 ? [null] : []),
    pages,
  ]
}

/** One thing that happened, and the fields it moved. */
function HistoryEntry({ entry }: { entry: TaskHistoryEntry }) {
  const when = new Date(entry.occurred_at).toLocaleString('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })

  return (
    <div className={styles.historyEntry}>
      <div className={styles.historyLine}>
        <span>{entry.summary}</span>
        <span className={styles.when}>{when}</span>
      </div>
      <div className={styles.historyWho}>
        {entry.actor_label}
        {/* Agents act through the same API as the browser, so the record is
            the only place that says a person did not do this. */}
        {entry.channel === 'api' ? <span className={styles.agent}>agent</span> : null}
      </div>
      {entry.changes.length ? (
        <dl className={styles.changes}>
          {entry.changes.map((change) => (
            <div key={change.field} className={styles.change}>
              <dt>{change.label}</dt>
              <dd>
                <span className={styles.was}>{shown(change.from)}</span>
                <span aria-hidden="true"> → </span>
                <span className="visually-hidden"> became </span>
                <span className={styles.now}>{shown(change.to)}</span>
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
    </div>
  )
}

/** How much of a changed value the record shows before it gets in the way. */
const LONGEST = 90

/**
 * A field's value as one short line.
 *
 * Truncated, because a description is a paragraph and a history that prints
 * two of them per edit is a history you have to scroll past rather than read.
 * The full text is on the card itself, which is the thing this is a record of.
 */
function shown(value: string | number | string[] | null): string {
  if (value === null || value === '') return '—'
  const text = Array.isArray(value) ? value.join(' · ') : String(value)
  return text.length > LONGEST ? `${text.slice(0, LONGEST - 1)}…` : text
}

/**
 * One column's date, said as what it still asks for.
 *
 * Three states and not four: reached, late, and simply ahead. A date the card
 * has passed is never late however long ago it was — the card got there, and
 * the schedule has moved on to the next column.
 */
function ColumnDateRead({ entry }: { entry: ColumnDueDate }) {
  const late = !entry.met && isOverdue(entry.due_date)
  return (
    <li className={entry.met ? styles.columnDateMet : undefined}>
      <span className={styles.columnDateName}>{entry.column_name}</span>
      <span className={late ? styles.late : ''}>
        {late ? '⚠ ' : ''}
        {formatDue(entry.due_date)}
      </span>
      {entry.met ? <span className={styles.columnDateNote}>reached</span> : null}
    </li>
  )
}

/** A labelled row of the detail view. The `Field` twin for text with no input. */
function ReadField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className={styles.readField}>
      <span className={styles.readLabel}>{label}</span>
      <div className={styles.readValue}>{children}</div>
    </div>
  )
}

/**
 * Parse a plain `YYYY-MM-DD` as a local date.
 *
 * `new Date(iso)` reads it as UTC midnight, which shows as the previous day
 * anywhere west of Greenwich — and a due date off by one is worse than none.
 */
function localDate(iso: string): Date {
  const [year = 1970, month = 1, day = 1] = iso.split('-').map(Number)
  return new Date(year, month - 1, day)
}

function formatDue(iso: string | null): string {
  if (!iso) return '—'
  return localDate(iso).toLocaleDateString('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

/** A card with no date is never late: there is no day it was wanted by. */
function isOverdue(iso: string | null): boolean {
  if (!iso) return false
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return localDate(iso) < today
}

function TaskForm({
  projectKey,
  parentRef,
  columns,
  firstColumn,
  templates,
  goals,
  defaultGoalId,
  task,
  members,
  announce,
  onOpenTask,
  onSplit,
  onDone,
  onClose,
  onCancel,
}: DialogProps & {
  task: TaskDetail | null
  members: Person[]
  /**
   * Leaving the form without saving. On an existing card that is a step back
   * to the detail view; on a new one there is nothing behind it, so it closes.
   */
  onCancel: () => void
}) {
  const [form, setForm] = useState<TaskInput>({
    title: task?.title ?? '',
    description: task?.description ?? '',
    type: task?.type ?? 'feature',
    priority: task?.priority ?? 'p3',
    sub_statuses: task?.sub_statuses ?? [],
    // The date input's empty value is '', not null; the mutation turns it back
    // into the null the API reads as "no date".
    due_date: task?.due_date ?? '',
    assignee_id: task?.assignee.id ?? members[0]?.id ?? '',
    template_id: task?.template_id ?? null,
    goal_id: task?.goal_id ?? defaultGoalId ?? null,
    jira_ref: task?.jira_ref ?? '',
    pr_ref: task?.pr_ref ?? '',
  })
  /**
   * Which stage the card is on. Held apart from `form` because creating a card
   * cannot say it — a new one starts on its first stage — while editing can:
   * reordering or removing a stage moves the marker, and this form is the only
   * thing that knows where it went.
   */
  const [subStatusIndex, setSubStatusIndex] = useState(task?.sub_status_index ?? 0)
  /**
   * A date per column, keyed by column — the shape the panel edits, not the
   * shape the API takes. A record keyed by column is what "the date in this
   * box" means; the list of pairs the API wants is built from it on save, with
   * the empty boxes dropped.
   */
  const [columnDates, setColumnDates] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      (task?.column_due_dates ?? []).map((entry) => [entry.column_id, entry.due_date]),
    ),
  )
  /**
   * Whether the column-dates tab is showing. A card that has none starts
   * without it: most cards are wanted done by a date and no more than that, and
   * a tab of six empty date boxes on every card is six questions nobody asked.
   */
  const [scheduling, setScheduling] = useState(Boolean(task?.column_due_dates.length))
  const [columnId, setColumnId] = useState(task?.column_id ?? firstColumn.id)
  const [status, setStatus] = useState<TaskStatus>(task?.status ?? 'active')
  const [reason, setReason] = useState('')
  const [tagged, setTagged] = useState<string[]>(task?.waiting_on.map((p) => p.id) ?? [])
  const [comment, setComment] = useState('')
  const [author, setAuthor] = useState('')

  const was = task?.status ?? 'active'
  const changing = status !== was
  const stalling = changing && status !== 'active'
  // Never for a sub-task: it has no column, so `columnId` fell back to the
  // board's first one and every save would look like a move to it.
  const moving = task !== null && task.column_id !== null && columnId !== task.column_id
  const destination = columns.find((column) => column.id === columnId)?.name ?? null
  /**
   * Whether this save changes what kind of card it is.
   *
   * Worth knowing on its own because it decides the order of the requests: a
   * card is never left sitting in a column its own new template forbids, so a
   * card being retyped *and* moved is moved first, while it is still the kind
   * of card that was allowed where it started.
   */
  const retyping = task !== null && form.template_id !== task.template_id
  /** Whether this card is one that can be on a goal at all. */
  const onGoal = task ? task.parent_id === null : !parentRef
  /** Whether this card passes through columns, and so can be dated in them. */
  const onBoard = task ? task.parent_id === null : !parentRef
  /** The columns a date can be set for: every one but the last, which is `due_date`. */
  const stages = columns.slice(0, -1)
  const lastColumn = columns[columns.length - 1]
  /** How far along the board the card has got, so a date behind it says so. */
  const here = columns.findIndex((column) => column.id === task?.column_id)

  const save = useMutation({
    mutationFn: async () => {
      const payload = {
        ...form,
        sub_statuses: form.sub_statuses.map((label) => label.trim()),
        due_date: form.due_date || null,
        // Sent whole on a card, and never on a sub-task. Empty boxes are
        // dropped rather than sent as nulls: a column with no date is a column
        // with no row, which is also how a date is taken off again.
        ...(onBoard
          ? {
              column_due_dates: stages
                .filter((column) => columnDates[column.id])
                .map((column) => ({
                  column_id: column.id,
                  due_date: columnDates[column.id] as string,
                })),
            }
          : {}),
        jira_ref: form.jira_ref?.trim() ? form.jira_ref.trim() : null,
        pr_ref: form.pr_ref?.trim() ? form.pr_ref.trim() : null,
      }
      // Top of the column rather than the bottom: a card moved from a dialog
      // has no place on the board the reader is already looking at. Before the
      // edit when the template is changing too — see `retyping`.
      if (task && moving && retyping) await api.moveTask(task.id, columnId, 0)

      const saved = task
        ? await api.updateTask(task.id, { ...payload, sub_status_index: subStatusIndex })
        : parentRef
          ? await api.createSubtask(parentRef, payload)
          : await api.createTask(projectKey, payload)

      if (task && moving && !retyping) await api.moveTask(saved.id, columnId, 0)

      if (changing) {
        await api.setTaskStatus(saved.id, {
          status,
          reason: reason.trim(),
          waiting_on: status === 'active' ? [] : tagged,
        })
      }

      if (comment.trim()) await api.addComment(saved.id, comment.trim(), author || null)
      return saved
    },
    onSuccess: async (saved) => {
      announce(
        summarise(
          saved.reference,
          task === null,
          moving ? destination : null,
          changing ? status : null,
        ),
      )
      await onDone()
      onClose()
    },
  })

  const remove = useMutation({
    mutationFn: () => api.deleteTask(task?.id ?? ''),
    onSuccess: async () => {
      announce(`${task?.reference ?? 'The task'} deleted.`)
      await onDone()
      onClose()
    },
  })

  const complete =
    form.title.trim() &&
    form.description.trim() &&
    form.assignee_id &&
    form.sub_statuses.every((label) => label.trim()) &&
    (!stalling || reason.trim())

  const error = save.error ?? remove.error

  function toggleTag(personId: string) {
    setTagged((current) =>
      current.includes(personId) ? current.filter((id) => id !== personId) : [...current, personId],
    )
  }

  /**
   * Arrow keys walk the status control, as they do any radio group.
   *
   * Focus moves with the choice rather than staying put: the roving tabindex
   * follows whichever option is checked, so leaving focus behind would make
   * the second arrow press do nothing.
   */
  function walkStatus(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const step =
      event.key === 'ArrowRight' || event.key === 'ArrowDown'
        ? 1
        : event.key === 'ArrowLeft' || event.key === 'ArrowUp'
          ? -1
          : 0
    if (step === 0) return

    event.preventDefault()
    const next = (index + step + STATUSES.length) % STATUSES.length
    const option = STATUSES[next]
    if (!option) return

    setStatus(option.value)
    const group = event.currentTarget.parentElement
    group?.querySelectorAll<HTMLButtonElement>('[role="radio"]')[next]?.focus()
  }

  /**
   * Which tab is showing.
   *
   * The form is long enough that the fields that decide what the card *is* —
   * its name, what done looks like, how urgent, what kind — were being scrolled
   * past on the way to the ones that decide how it is tracked. Splitting them
   * puts the first decision on the first screen.
   *
   * Every panel stays mounted and is hidden with `hidden`, so switching tabs
   * never throws away what has been typed on the other one. `hidden` also takes
   * the panel out of the modal's focus trap, which reads the laid-out controls
   * rather than a list it keeps in step by hand.
   */
  const [tab, setTab] = useState<TabId>('basics')

  const tabs: Tab[] = [
    {
      id: 'basics',
      label: 'Basics',
      // Marked on the tab, not only on the Save button: a disabled Save with
      // no reason showing is a dead end when the reason is on a tab you
      // cannot see.
      incomplete: !form.title.trim() || !form.description.trim(),
    },
    {
      id: 'details',
      label: 'Details',
      incomplete:
        !form.assignee_id ||
        !form.sub_statuses.every((label) => label.trim()) ||
        Boolean(stalling && !reason.trim()),
    },
    // Opened by the + beside the due date, and it stays open once a card has
    // dates on it: a schedule you had to go and ask for once should be in front
    // of you every time afterwards.
    ...(onBoard && scheduling
      ? [{ id: 'dates' as const, label: 'Column dates', incomplete: false }]
      : []),
    ...(task ? [{ id: 'work' as const, label: 'Sub-tasks & comments', incomplete: false }] : []),
  ]

  return (
    <Modal
      // Escape and the backdrop mean "stop editing", which on an existing card
      // is the detail view rather than the board.
      title={
        task
          ? `${task.reference} · editing`
          : parentRef
            ? `New sub-task of ${parentRef}`
            : 'New task'
      }
      onClose={onCancel}
      footer={
        <>
          {task ? (
            <Button
              variant="ghost"
              danger
              className={styles.spacer}
              disabled={remove.isPending}
              onClick={() => remove.mutate()}
            >
              Delete
            </Button>
          ) : null}
          <Button onClick={onCancel}>Cancel</Button>
          <Button variant="go" disabled={save.isPending || !complete} onClick={() => save.mutate()}>
            {save.isPending
              ? 'Saving…'
              : task
                ? 'Save changes'
                : parentRef
                  ? 'Create sub-task'
                  : 'Create task'}
          </Button>
        </>
      }
    >
      <ModalBody>
        {error ? <ErrorBanner>{error.message}</ErrorBanner> : null}

        <Tabs tabs={tabs} current={tab} onSelect={setTab} />

        <div
          className={styles.panel}
          role="tabpanel"
          id="task-panel-basics"
          aria-labelledby="task-tab-basics"
          hidden={tab !== 'basics'}
        >
          <Field label="Task name" required>
            <input
              value={form.title}
              maxLength={200}
              onChange={(event) => setForm({ ...form, title: event.target.value })}
              placeholder="Short, verb-first"
            />
          </Field>

          <Field label="Description" required hint="Type @ to tag someone on the project.">
            <MentionBox
              multiline
              value={form.description}
              onChange={(description) => setForm({ ...form, description })}
              members={members}
              placeholder="What done looks like"
            />
          </Field>

          <FieldPair>
            <Field label="Priority" required>
              <select
                value={form.priority}
                onChange={(event) =>
                  setForm({ ...form, priority: event.target.value as TaskPriority })
                }
              >
                {PRIORITIES.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label} — {option.means}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Type" required>
              <select
                value={form.type}
                onChange={(event) => setForm({ ...form, type: event.target.value as TaskType })}
              >
                {TYPES.map((type) => (
                  <option key={type} value={type}>
                    {type}
                  </option>
                ))}
              </select>
            </Field>
          </FieldPair>

          <Field label="Template" hint={templateHint(templates, form.template_id, columns)}>
            <select
              value={form.template_id ?? ''}
              onChange={(event) => setForm({ ...form, template_id: event.target.value || null })}
            >
              <option value="">No template — goes anywhere on the board</option>
              {templates.map((template) => (
                <option key={template.id} value={template.id}>
                  {template.name}
                </option>
              ))}
            </select>
          </Field>

          {/* Not offered on a sub-task: a sub-task belongs to its card, and its
              card is what belongs to a goal. The API refuses one either way —
              this is only the form agreeing with it. */}
          {onGoal ? (
            <Field
              label="Goal"
              hint="The epic this card is work towards. Its colour becomes the card's rail on the board."
            >
              <select
                value={form.goal_id ?? ''}
                onChange={(event) => setForm({ ...form, goal_id: event.target.value || null })}
              >
                <option value="">No goal — this card stands on its own</option>
                {goals.map((goal) => (
                  <option key={goal.id} value={goal.id}>
                    {goal.name}
                    {goal.status === 'open' ? '' : ` (${goal.status})`}
                  </option>
                ))}
              </select>
            </Field>
          ) : null}
        </div>

        <div
          className={styles.panel}
          role="tabpanel"
          id="task-panel-details"
          aria-labelledby="task-tab-details"
          hidden={tab !== 'details'}
        >
          <FieldPair>
            <Field
              label="Due date"
              {...(onBoard ? { hint: `When it is wanted done — in ${lastColumn?.name}.` } : {})}
            >
              <div className={styles.dueRow}>
                <input
                  type="date"
                  value={form.due_date ?? ''}
                  onChange={(event) => setForm({ ...form, due_date: event.target.value })}
                />
                {/* The way in to the dates before this one. A card is dated at
                    the end of the board by default and the columns on the way
                    are asked for, not offered: most cards want one date, and a
                    form that opens with six is a form that reads as six
                    questions. */}
                {onBoard ? (
                  <Button
                    variant="ghost"
                    small
                    title="Set a date per column"
                    aria-label="Set a date per column"
                    onClick={() => {
                      setScheduling(true)
                      setTab('dates')
                    }}
                  >
                    +
                  </Button>
                ) : null}
              </div>
            </Field>
            <Field label="Assignee" required>
              <select
                value={form.assignee_id}
                onChange={(event) => setForm({ ...form, assignee_id: event.target.value })}
              >
                {members.map((person) => (
                  <option key={person.id} value={person.id}>
                    {person.name} — {person.role}
                  </option>
                ))}
              </select>
            </Field>
          </FieldPair>

          <Field
            label="Sub-status"
            hint="Up to 4 stages, in order. Moving the card between them happens on the board. Type @ to tag someone."
          >
            <SubStatusListEditor
              value={form.sub_statuses}
              current={subStatusIndex}
              members={members}
              onChange={(sub_statuses, current) => {
                setForm({ ...form, sub_statuses })
                setSubStatusIndex(current)
              }}
            />
          </Field>

          <FieldPair>
            <Field label="Jira">
              <input
                value={form.jira_ref ?? ''}
                onChange={(event) => setForm({ ...form, jira_ref: event.target.value })}
                placeholder="ATL-00 or a URL"
              />
            </Field>
            <Field label="Pull request">
              <input
                value={form.pr_ref ?? ''}
                onChange={(event) => setForm({ ...form, pr_ref: event.target.value })}
                placeholder="#000 or a URL"
              />
            </Field>
          </FieldPair>

          {/* Neither column nor status is offered on a new card, because
              neither is a choice: work enters the board at the first column and
              starts out active. A control with one possible value is a question
              that reads as though it had an answer.

              Nor is a column offered on a sub-task, for the stronger reason
              that it has none: a sub-task is not on the board, and every one of
              these options would be a move the server refuses. It is finished
              from its parent's list instead. */}
          {task && task.parent_id === null ? (
            <Field label="Column" required>
              <select value={columnId} onChange={(event) => setColumnId(event.target.value)}>
                {columns.map((column) => (
                  <option key={column.id} value={column.id}>
                    {column.name}
                  </option>
                ))}
              </select>
            </Field>
          ) : null}

          {task ? (
            <Field label="Status">
              <div className={styles.segmented} role="radiogroup" aria-label="Status">
                {STATUSES.map((option, index) => (
                  <button
                    key={option.value}
                    type="button"
                    role="radio"
                    aria-checked={status === option.value}
                    // One stop for the whole group, on whichever option is chosen:
                    // Tab should pass a three-way choice, not visit it three times.
                    tabIndex={status === option.value ? 0 : -1}
                    className={
                      status === option.value ? `${styles.on} ${styles[option.value]}` : ''
                    }
                    onClick={() => setStatus(option.value)}
                    onKeyDown={(event) => walkStatus(event, index)}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </Field>
          ) : null}

          {stalling ? (
            <div className={`${styles.why} ${styles[status]}`}>
              <Field label="Why is the status changing?" required>
                <textarea
                  className={styles.reason}
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  placeholder="Required when moving to On hold or Blocked"
                />
              </Field>
              <Field label="Who is this waiting on?">
                <div className={styles.tagPicker} role="group" aria-label="Who is this waiting on?">
                  {members.map((person) => (
                    <button
                      key={person.id}
                      type="button"
                      className={tagged.includes(person.id) ? styles.tagged : ''}
                      aria-pressed={tagged.includes(person.id)}
                      onClick={() => toggleTag(person.id)}
                    >
                      <Avatar name={person.name} colour={person.colour} />
                      {person.name.split(' ')[0]}
                      <span className={styles.role}>· {person.role.split(',')[0]}</span>
                    </button>
                  ))}
                </div>
              </Field>
              <small>
                Added to the card&rsquo;s comments and shown on the board as &ldquo;Waiting on
                @name&rdquo;.
              </small>
            </div>
          ) : null}

          {/* A card being written has no timeline yet, but it may well be
              worth a first word — so the composer follows it onto Details
              rather than disappearing with the tab it lives on for a card
              that exists. */}
          {task ? null : (
            <Comments
              task={null}
              members={members}
              author={author}
              onAuthor={setAuthor}
              comment={comment}
              onComment={setComment}
            />
          )}
        </div>

        {onBoard && scheduling ? (
          <div
            className={styles.panel}
            role="tabpanel"
            id="task-panel-dates"
            aria-labelledby="task-tab-dates"
            hidden={tab !== 'dates'}
          >
            <p className={styles.note}>
              The day this card is wanted in each column. Any of them, none of them — a column left
              empty simply has no date. The last column is the card&rsquo;s due date, on Details.
            </p>
            {stages.map((column, index) => (
              <Field
                key={column.id}
                label={column.name}
                {...(index <= here ? { hint: 'The card has already been here.' } : {})}
              >
                <input
                  type="date"
                  value={columnDates[column.id] ?? ''}
                  onChange={(event) =>
                    setColumnDates({ ...columnDates, [column.id]: event.target.value })
                  }
                />
              </Field>
            ))}
          </div>
        ) : null}

        {task ? (
          <div
            className={styles.panel}
            role="tabpanel"
            id="task-panel-work"
            aria-labelledby="task-tab-work"
            hidden={tab !== 'work'}
          >
            <Subtasks
              task={task}
              members={members}
              onOpenTask={onOpenTask}
              onSplit={onSplit}
              announce={announce}
              onDone={onDone}
            />

            <Comments
              task={task}
              members={members}
              author={author}
              onAuthor={setAuthor}
              comment={comment}
              onComment={setComment}
            />
          </div>
        ) : null}
      </ModalBody>
    </Modal>
  )
}

/**
 * What has been said about the card, and a box to say something.
 *
 * Shared by both halves of the form because a new card gets the composer
 * without the timeline: there is nothing to read yet, and a first comment
 * typed while writing the card is posted with it.
 */
function Comments({
  task,
  members,
  author,
  onAuthor,
  comment,
  onComment,
}: {
  /** Null while the card is being written, which is the empty timeline. */
  task: TaskDetail | null
  members: Person[]
  author: string
  onAuthor: (id: string) => void
  comment: string
  onComment: (text: string) => void
}) {
  return (
    <Field label="Comments">
      <div className={styles.timeline}>
        {task?.comments.length ? (
          task.comments.map((entry) => <Entry key={entry.id} entry={entry} members={members} />)
        ) : (
          <span className={styles.empty}>No comments yet.</span>
        )}
        <div className={styles.composer}>
          <select
            value={author}
            onChange={(event) => onAuthor(event.target.value)}
            aria-label="Comment as"
          >
            <option value="">Unattributed</option>
            {members.map((person) => (
              <option key={person.id} value={person.id}>
                {person.name}
              </option>
            ))}
          </select>
          <MentionBox
            value={comment}
            onChange={onComment}
            members={members}
            className={styles.grow}
            aria-label="Add a comment"
            placeholder="Add a comment, @ to tag someone…"
          />
        </div>
      </div>
    </Field>
  )
}

type TabId = 'basics' | 'details' | 'dates' | 'work'

interface Tab {
  id: TabId
  label: string
  /** Whether a required field on this tab is still empty. */
  incomplete: boolean
}

/**
 * The form's tab strip.
 *
 * A tab carries a dot when something required on it is still blank, so a
 * disabled Save button always has somewhere visible to point at — otherwise
 * the one unfilled field is behind a tab nobody has a reason to open.
 *
 * Arrow keys walk the strip and focus follows the selection, as they do in any
 * tab list: the roving tabindex sits on the selected tab, so leaving focus
 * behind would make the second arrow press do nothing.
 */
function Tabs({
  tabs,
  current,
  onSelect,
}: {
  tabs: Tab[]
  current: TabId
  onSelect: (id: TabId) => void
}) {
  function walk(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0
    if (step === 0) return

    event.preventDefault()
    const next = tabs[(index + step + tabs.length) % tabs.length]
    if (!next) return

    onSelect(next.id)
    const strip = event.currentTarget.parentElement
    strip?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[tabs.indexOf(next)]?.focus()
  }

  return (
    <div className={styles.tabs} role="tablist" aria-label="Task fields">
      {tabs.map((tab, index) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          id={`task-tab-${tab.id}`}
          aria-selected={tab.id === current}
          aria-controls={`task-panel-${tab.id}`}
          tabIndex={tab.id === current ? 0 : -1}
          className={tab.id === current ? styles.tabOn : ''}
          onClick={() => onSelect(tab.id)}
          onKeyDown={(event) => walk(event, index)}
        >
          {tab.label}
          {tab.incomplete ? (
            <span className={styles.needed} aria-label="something required is still empty">
              •
            </span>
          ) : null}
        </button>
      ))}
    </div>
  )
}

/**
 * What choosing a template commits the card to, in words.
 *
 * A template's stages are invisible until the card is dragged somewhere they
 * refuse, or stuck short of a sub-stage they demand. Saying it here is the
 * difference between a dropdown and a decision.
 */
function templateHint(
  templates: Template[],
  templateId: string | null,
  columns: BoardColumn[],
): string {
  if (!templateId) return 'Optional. A card with no template may sit in any column.'

  const template = templates.find((candidate) => candidate.id === templateId)
  if (!template) return ''
  if (!template.stages.length) {
    return `${template.name} has no columns set up yet, so its cards may sit anywhere.`
  }

  const named = template.allowed_column_ids
    .map((id) => columns.find((column) => column.id === id)?.name)
    .filter(Boolean)
  const staged = template.stages.some((stage) => stage.sub_stage_labels.length)
  return (
    `${template.name} cards go to ${named.join(' → ')}, and nowhere else.` +
    (staged ? ' Each column may set sub-stages a card must reach before leaving it.' : '')
  )
}

/**
 * The stage list, edited by hand: renamed in place, reordered, removed, added.
 *
 * Order is the axis the board's slider slides along, so moving a stage is a
 * real edit rather than delete-and-retype. The current stage travels with its
 * label — move "Review" up and the card is still on "Review" — which is why
 * this owns the marker as well as the words.
 */
function SubStatusListEditor({
  value,
  current,
  members,
  onChange,
}: {
  value: string[]
  current: number
  members: Person[]
  onChange: (value: string[], current: number) => void
}) {
  /** Swap two neighbours, taking the marker along if it is on one of them. */
  const swap = (a: number, b: number) => {
    const next = [...value]
    next[a] = value[b] as string
    next[b] = value[a] as string
    onChange(next, current === a ? b : current === b ? a : current)
  }

  const remove = (index: number) => {
    const next = value.filter((_, position) => position !== index)
    // The marker follows what is left: a stage taken from behind it pulls it
    // back one, and taking the current stage leaves the marker on whatever
    // has moved up into its place — or on the new last stage if nothing has.
    onChange(next, index < current ? current - 1 : Math.min(current, Math.max(next.length - 1, 0)))
  }

  return (
    <div className={styles.subStatusEditor}>
      {value.map((label, index) => (
        <div key={index} className={styles.subStatusEditorRow}>
          <span
            className={`${styles.subStatusEditorNumber} ${
              index === current ? styles.subStatusEditorNow : ''
            }`}
            title={index === current ? 'The stage this card is on' : `Stage ${index + 1}`}
          >
            {index + 1}
          </span>
          <MentionBox
            value={label}
            members={members}
            className={styles.grow}
            maxLength={60}
            placeholder={`Stage ${index + 1}`}
            aria-label={`Sub-status stage ${index + 1}`}
            onChange={(text) => {
              const next = [...value]
              next[index] = text
              onChange(next, current)
            }}
          />
          <Button
            variant="ghost"
            small
            disabled={index === 0}
            aria-label={`Move stage ${index + 1} up`}
            title="Move up"
            onClick={() => swap(index, index - 1)}
          >
            ↑
          </Button>
          <Button
            variant="ghost"
            small
            disabled={index === value.length - 1}
            aria-label={`Move stage ${index + 1} down`}
            title="Move down"
            onClick={() => swap(index, index + 1)}
          >
            ↓
          </Button>
          <Button
            variant="ghost"
            small
            aria-label={`Remove stage ${index + 1}`}
            title="Remove"
            onClick={() => remove(index)}
          >
            ×
          </Button>
        </div>
      ))}
      {value.length < 4 ? (
        <Button variant="ghost" small onClick={() => onChange([...value, ''], current)}>
          + Add stage
        </Button>
      ) : null}
    </div>
  )
}

/**
 * One sentence covering everything a save did.
 *
 * A save is up to four requests, and hearing four separate confirmations of
 * one button press is worse than hearing none.
 */
function summarise(
  reference: string,
  created: boolean,
  movedTo: string | null,
  newStatus: TaskStatus | null,
): string {
  const parts = [created ? `Created ${reference}.` : `Saved ${reference}.`]
  if (movedTo) parts.push(`Moved to ${movedTo}.`)

  // Only when it actually changed: "status active" after every save is noise
  // that trains people to stop listening to the region.
  const label = STATUSES.find((option) => option.value === newStatus)?.label
  if (label) parts.push(`Status ${label.toLowerCase()}.`)
  return parts.join(' ')
}

/** One timeline entry. System entries are drawn apart from what people wrote. */
function Entry({ entry, members }: { entry: TaskComment; members: Person[] }) {
  const when = new Date(entry.created_at).toLocaleString('en-GB', {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })

  if (entry.kind === 'status_change') {
    const names = (entry.meta.tagged ?? [])
      .map((id) => members.find((person) => person.id === id)?.name)
      .filter((name): name is string => Boolean(name))

    return (
      <div className={`${styles.entry} ${styles.system} ${styles[entry.meta.to ?? 'active']}`}>
        <div className={styles.bubble}>
          {entry.body}
          {names.length ? <> · waiting on {names.map((name) => `@${name}`).join(', ')}</> : null}
          <span className={styles.when}> · {when}</span>
        </div>
      </div>
    )
  }

  return (
    <div className={styles.entry}>
      {entry.author ? (
        <Avatar name={entry.author.name} colour={entry.author.colour} />
      ) : (
        <Avatar name="?" colour="var(--ink-3)" />
      )}
      <div className={styles.bubble}>
        <div className={styles.who}>
          {entry.author?.name ?? 'Unattributed'}
          <span>{when}</span>
        </div>
        <Tagged text={entry.body} members={members} />
      </div>
    </div>
  )
}

const CHECKLIST_LABELS: Record<ChecklistState, string> = {
  open: 'Open',
  done: 'Done',
  cancelled: 'Cancelled',
}

/**
 * Ticking a sub-task off, shared by the detail view and the form.
 *
 * Two kinds of sub-task, one gesture. Neither is on the board, so neither is
 * finished by going anywhere: a tick box carries a state, a sub-task carries a
 * finishing time, and ticking either one settles it and stops it holding the
 * parent back.
 *
 * One hook rather than a copy in each list, so the two cannot drift: whichever
 * half of the dialog you are looking at, ticking means the same thing.
 */
function useSubtaskTicking({
  task,
  announce,
  onDone,
}: {
  task: TaskDetail
  announce: (message: string) => void
  onDone: () => Promise<void>
}) {
  const queryClient = useQueryClient()

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: ['task', task.id] })
    // Ticking a box is itself a change to the card, so the history under it
    // is now one entry out of date.
    await queryClient.invalidateQueries({ queryKey: ['task-history', task.id] })
    await onDone()
  }

  const setState = useMutation({
    mutationFn: ({ id, state }: { id: string; state: ChecklistState }) =>
      api.updateChecklistItem(id, { state }),
    onSuccess: async (item) => {
      announce(`${item.title} is ${CHECKLIST_LABELS[item.state].toLowerCase()}.`)
      await refresh()
    },
  })

  const setFinished = useMutation({
    mutationFn: ({ child, finished }: { child: Task; finished: boolean }) =>
      api.finishTask(child.id, finished),
    onSuccess: async (child, { finished }) => {
      announce(`${child.reference} is ${finished ? 'finished' : 'open again'}.`)
      await refresh()
    },
  })

  return { refresh, setState, setFinished }
}

/**
 * One sub-task with a reference of its own, in either list.
 *
 * The tick box is the point of it: the reason to be looking at this list is to
 * see what is left, and a list you have to leave in order to finish anything is
 * a list that gets left. It ticks the sub-task itself — nothing moves on the
 * board behind the dialog, because a sub-task is not on it.
 *
 * The owner is the one thing here a tick box has no equivalent of, and the
 * reason the two kinds of sub-task both exist: the board no longer shows who is
 * on a piece of split-out work, so this row does.
 */
function SubtaskCardRow({
  child,
  busy,
  onOpen,
  onFinish,
}: {
  child: Task
  busy: boolean
  onOpen: () => void
  onFinish: (finished: boolean) => void
}) {
  const finished = child.finished_at !== null
  const cancelled = child.status === 'cancelled'
  const status = STATUSES.find((option) => option.value === child.status)

  return (
    <div className={`${styles.subtaskRow} ${finished || cancelled ? styles.settled : ''}`}>
      <input
        type="checkbox"
        checked={finished}
        // A cancelled sub-task is settled already and ticking it would say the
        // work happened. Reopen it with the status control on its own card.
        disabled={busy || cancelled}
        aria-label={
          cancelled
            ? `${child.reference} is cancelled`
            : finished
              ? `${child.reference} is finished. Reopen it.`
              : `Finish ${child.reference}.`
        }
        onChange={() => onFinish(!finished)}
      />
      {/* The button is the whole of the rest of the row rather than the
          reference alone: the title is what the eye lands on, and a link you
          have to aim at is a link you misclick. It cannot wrap the tick box —
          one control does not go inside another. */}
      <button type="button" className={styles.subOpen} onClick={onOpen}>
        <span className={styles.subRef}>{child.reference}</span>
        <span className={styles.subTitle}>{child.title}</span>
      </button>
      <span className={styles.subState}>
        {/* Whichever of the two is the more urgent thing to know. A stalled
            sub-task says so; anything else is answered by the tick box, so the
            owner is what the space is better spent on. */}
        {child.status === 'active' ? (
          <Avatar name={child.assignee.name} colour={child.assignee.colour} />
        ) : (
          status?.label
        )}
      </span>
    </div>
  )
}

/**
 * Both kinds of sub-task, on the card they belong to.
 *
 * The tick boxes save on the spot rather than on the dialog's Save button: a
 * checkbox that quietly waits for a second, unrelated button is a checkbox
 * that has already lied to whoever ticked it. The cards do not save here at
 * all — splitting hands the job back to the board, which opens a fresh dialog
 * for the new card, because a sub-task needs every field a card needs.
 */
function Subtasks({
  task,
  members,
  onOpenTask,
  onSplit,
  announce,
  onDone,
}: {
  task: TaskDetail
  /** The people a checklist item may tag, and whose tags it draws. */
  members: Person[]
  onOpenTask?: ((taskRef: string) => void) | undefined
  onSplit?: ((parentRef: string) => void) | undefined
  announce: (message: string) => void
  onDone: () => Promise<void>
}) {
  const [title, setTitle] = useState('')
  const { refresh, setState, setFinished } = useSubtaskTicking({ task, announce, onDone })

  const add = useMutation({
    mutationFn: (text: string) => api.addChecklistItem(task.id, text),
    onSuccess: async (item) => {
      announce(`Added ${item.title} to the checklist.`)
      setTitle('')
      await refresh()
    },
  })

  const remove = useMutation({
    mutationFn: (id: string) => api.deleteChecklistItem(id),
    onSuccess: refresh,
  })

  const error = add.error ?? setState.error ?? remove.error ?? setFinished.error
  const outstanding = task.open_subtask_count
  const busy = setState.isPending || setFinished.isPending || remove.isPending

  return (
    <Field label="Sub-tasks">
      {error ? <ErrorBanner>{error.message}</ErrorBanner> : null}

      <div className={styles.subtasks}>
        {task.subtasks.map((child) => (
          <SubtaskCardRow
            key={child.id}
            child={child}
            busy={busy}
            onOpen={() => onOpenTask?.(child.reference)}
            onFinish={(finished) => setFinished.mutate({ child, finished })}
          />
        ))}

        {task.checklist.map((item) => (
          <ChecklistRow
            key={item.id}
            item={item}
            members={members}
            busy={busy}
            onSetState={(state) => setState.mutate({ id: item.id, state })}
            onRemove={() => remove.mutate(item.id)}
          />
        ))}

        {task.subtasks.length === 0 && task.checklist.length === 0 ? (
          <span className={styles.empty}>No sub-tasks.</span>
        ) : null}

        <div className={styles.composer}>
          {/* Enter is the list's own gesture — type a line, press enter, type
              the next — and it is also how the suggestion list accepts a name.
              The box stops the key while the list is open, so the tag is taken
              first and the item is added by the enter after it. */}
          <MentionBox
            value={title}
            onChange={setTitle}
            members={members}
            className={styles.grow}
            aria-label="Add a checklist item"
            maxLength={200}
            onEnter={() => {
              if (title.trim()) add.mutate(title.trim())
            }}
            placeholder="Add a checklist item, @ to tag someone…"
          />
          <Button
            small
            disabled={!title.trim() || add.isPending}
            onClick={() => add.mutate(title.trim())}
          >
            Add
          </Button>
        </div>

        {/* A sub-task cannot be split again: the board would stop being one. */}
        {task.parent_id === null ? (
          <Button small variant="ghost" onClick={() => onSplit?.(task.reference)}>
            + Split into a sub-task with an owner of its own
          </Button>
        ) : (
          <small>
            A sub-task of{' '}
            <button
              type="button"
              className={styles.parentLink}
              onClick={() => onOpenTask?.(task.parent_reference ?? '')}
            >
              {task.parent_reference}
            </button>
            . Sub-tasks go one level deep.
          </small>
        )}

        {outstanding ? (
          <small className={styles.gate}>
            {outstanding} still open. Every sub-task has to be finished or cancelled before this
            card can move to {'the board’s last column'}.
          </small>
        ) : null}
      </div>
    </Field>
  )
}

/**
 * Both kinds of sub-task, on the detail view.
 *
 * The twin of `Subtasks`, and it ticks. Everything else here is text, because
 * a card should be readable without being editable — but finishing a sub-task
 * is not editing this card. It is settling a different item, which is the
 * whole reason to be looking at the list, and requiring the pencil for it
 * would mean opening a form over every field of the parent in order to tick
 * one box that belongs to something else.
 *
 * So the line is drawn at what a control changes rather than at whether there
 * is one: nothing on this view can alter the card's own fields, and the two
 * things it can do — tick a sub-task off, open one — are both about the
 * sub-tasks. Adding, retitling, cancelling and deleting them stay behind the
 * pencil with everything else.
 */
function SubtasksRead({
  task,
  members,
  onOpenTask,
  announce,
  onDone,
}: {
  task: TaskDetail
  /** Only to draw the `@` tags in a checklist item as tags. */
  members: Person[]
  onOpenTask?: ((taskRef: string) => void) | undefined
  announce: (message: string) => void
  onDone: () => Promise<void>
}) {
  const { setState, setFinished } = useSubtaskTicking({ task, announce, onDone })

  const outstanding = task.open_subtask_count
  const error = setState.error ?? setFinished.error
  const busy = setState.isPending || setFinished.isPending

  return (
    <div className={styles.subtasks}>
      {error ? <ErrorBanner>{error.message}</ErrorBanner> : null}

      {task.subtasks.map((child) => (
        <SubtaskCardRow
          key={child.id}
          child={child}
          busy={busy}
          onOpen={() => onOpenTask?.(child.reference)}
          onFinish={(finished) => setFinished.mutate({ child, finished })}
        />
      ))}

      {task.checklist.map((item) => (
        <ChecklistRow
          key={item.id}
          item={item}
          members={members}
          busy={busy}
          onSetState={(state) => setState.mutate({ id: item.id, state })}
        />
      ))}

      {task.subtasks.length === 0 && task.checklist.length === 0 ? (
        <span className={styles.empty}>No sub-tasks.</span>
      ) : null}

      {task.parent_id === null ? null : (
        <small>
          A sub-task of{' '}
          <button
            type="button"
            className={styles.parentLink}
            onClick={() => onOpenTask?.(task.parent_reference ?? '')}
          >
            {task.parent_reference}
          </button>
          . Sub-tasks go one level deep.
        </small>
      )}

      {outstanding ? (
        <small className={styles.gate}>
          {outstanding} still open. Every sub-task has to be finished or cancelled before this card
          can move to {'the board\u2019s last column'}.
        </small>
      ) : null}
    </div>
  )
}

/**
 * One tick box: ticked, cancelled, put back, or removed outright.
 *
 * Without `onRemove` it is only the tick box — which is the detail view, where
 * settling an item is fair game and rewriting the list is not.
 */
function ChecklistRow({
  item,
  members,
  busy,
  onSetState,
  onRemove,
}: {
  item: ChecklistItem
  /** Only to draw the `@` tags in the title as tags. */
  members: Person[]
  busy: boolean
  onSetState: (state: ChecklistState) => void
  onRemove?: (() => void) | undefined
}) {
  const settled = item.state !== 'open'

  return (
    <div className={`${styles.checkRow} ${settled ? styles.settled : ''}`}>
      <label className={styles.checkLabel}>
        <input
          type="checkbox"
          checked={item.state === 'done'}
          disabled={busy}
          onChange={(event) => onSetState(event.target.checked ? 'done' : 'open')}
        />
        <span className={item.state === 'cancelled' ? styles.struck : ''}>
          <Tagged text={item.title} members={members} />
        </span>
      </label>
      {onRemove ? (
        <span className={styles.checkActions}>
          <Button
            small
            variant="ghost"
            disabled={busy}
            onClick={() => onSetState(item.state === 'cancelled' ? 'open' : 'cancelled')}
          >
            {item.state === 'cancelled' ? 'Reopen' : 'Cancel'}
          </Button>
          <Button small variant="ghost" danger disabled={busy} onClick={onRemove}>
            Delete
          </Button>
        </span>
      ) : (
        <span className={styles.subState}>{CHECKLIST_LABELS[item.state]}</span>
      )}
    </div>
  )
}
