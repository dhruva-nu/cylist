import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from '@tanstack/react-router'
import type { ReactNode } from 'react'
import { api, type BoardColumn, type ColumnDueDate, type TaskDetail } from '../../api/client'
import { usePermissions } from '../../routes/usePermissions'
import { AgentPanel } from '../AgentPanel'
import { localDate } from '../dates'
import { GoalChip } from '../GoalMarks'
import { Markdown } from '../Markdown'
import { Modal, ModalBody } from '../Modal'
import { PRIORITIES, STATUS_LABELS } from '../taskLabels'
import {
  Avatar,
  Button,
  ErrorNotice,
  PriorityIcon,
  SubStatusBar,
  TaskRef,
  TypeIcon,
} from '../ui'
import { CommentComposer, Timeline } from './Comments'
import type { DialogData, TaskDialogProps } from './dialogProps'
import { History } from './History'
import { PrRefsRead } from './PullRequests'
import { SubtasksRead } from './Subtasks'
import { useCommentAuthor } from './useCommentAuthor'
import { useCommentDeleting } from './useCommentDeleting'
import { useRefreshTask } from './useRefreshTask'
import shared from './shared.module.css'
import styles from './TaskDetailView.module.css'

/**
 * The detail view of a card: read it, tick its sub-tasks off, and say how far
 * its pull requests got.
 *
 * Deliberately plain markup: a card's own fields are text here, not disabled
 * inputs. A disabled input still looks like somewhere to type and still has to
 * be kept in sync with a form; text cannot be saved by mistake at all. Nothing
 * on this view can alter a field of this card — Edit, Close, the sub-task
 * list, which finishes work that belongs to other cards, and the pull-request
 * list, which records what a forge did. See `SubtasksRead` and `PrRefsRead`.
 */
export function TaskDetailView({
  projectKey,
  task,
  members,
  files,
  columns,
  onOpenTask,
  announce,
  onDone,
  onEdit,
  onClose,
}: Pick<
  TaskDialogProps,
  'projectKey' | 'columns' | 'onOpenTask' | 'announce' | 'onDone' | 'onClose'
> &
  DialogData & {
    task: TaskDetail
    onEdit: () => void
  }) {
  const column = columns.find((candidate) => candidate.id === task.column_id)
  // A card that owes nothing is never late: once it is done, the day it was
  // wanted done is a fact about the past rather than something outstanding.
  const late = task.next_due_date !== null && isOverdue(task.due_date)
  const deleting = useCommentDeleting({ projectKey, announce, onDone })
  const refreshTask = useRefreshTask(onDone)
  const [author, setAuthor] = useCommentAuthor(members)
  const [draft, setDraft] = useState('')
  const post = useMutation({
    mutationFn: () => api.addComment(task.id, draft.trim(), author || null),
    onSuccess: async () => {
      setDraft('')
      announce('Comment added.')
      await refreshTask(task.id)
    },
  })

  const editButton = (
    <Button variant="ghost" small onClick={onEdit} aria-label="Edit task" title="Edit task">
      ✎
    </Button>
  )

  return (
    <Modal
      wide
      title={task.reference}
      onClose={onClose}
      headerActions={editButton}
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

        <TaskChips task={task} />

        {task.archived_at ? (
          <StoredNotice
            projectKey={projectKey}
            task={task}
            lastColumn={columns.at(-1)}
            announce={announce}
            onDone={onDone}
          />
        ) : null}

        {/* Under the chips rather than among them: the goal is the one thing
            on this card that is somewhere else as well, so it is a link, and a
            link sitting in a row of chips reads as a chip that is broken. */}
        <GoalReadField projectKey={projectKey} task={task} onClose={onClose} />

        {/* High on the card, under what the card is and above what it says:
            an agent waiting on a question is the one thing here somebody may
            have opened the card to do — a tap on a waiting card on a phone
            opens this rather than the board's popover (CYLIST-86). */}
        {task.agent_sessions.length ? (
          <AgentPanel taskRef={task.reference} taskId={task.id} sessions={task.agent_sessions} />
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
          <Markdown source={task.description} tags={{ members, files }} />
        </ReadField>

        <div className={styles.readPair}>
          <ReadField label="Assignee">
            <span className={styles.person}>
              <Avatar name={task.assignee.name} colour={task.assignee.colour} />
              {task.assignee.name}
              <span className={shared.aside}>· {task.assignee.title.split(',')[0]}</span>
            </span>
          </ReadField>
          <ReadField label="Due date">
            <DateRead iso={task.due_date} late={late} />
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
          <ColumnOrFinishedReadField task={task} column={column} />
          <ReadField label="Waiting on">
            {task.waiting_on.length ? task.waiting_on.map((person) => person.name).join(', ') : '—'}
          </ReadField>
        </div>

        {task.jira_ref || task.pr_refs.length ? (
          <div className={styles.readPair}>
            <ReadField label="Jira">
              {task.jira_ref ? <TaskRef kind="jira" value={task.jira_ref} /> : '—'}
            </ReadField>
            {/* Singular or plural by what the card actually has, because a
                label reading "Pull requests" over one of them says the card is
                missing some. */}
            <ReadField label={task.pr_refs.length > 1 ? 'Pull requests' : 'Pull request'}>
              {task.pr_refs.length ? (
                <PrRefsRead task={task} announce={announce} onDone={onDone} />
              ) : (
                '—'
              )}
            </ReadField>
          </div>
        ) : null}

        <ReadField label="Sub-tasks">
          <SubtasksRead
            task={task}
            members={members}
            files={files}
            onOpenTask={onOpenTask}
            announce={announce}
            onDone={onDone}
          />
        </ReadField>

        <ReadField label="Timeline">
          <Timeline
            comments={task.comments}
            members={members}
            files={files}
            deleting={deleting}
            emptyText="Nothing has been said about this card yet."
          >
            <ErrorNotice error={post.error} />
            <CommentComposer
              members={members}
              files={files}
              author={author}
              onAuthor={setAuthor}
              comment={draft}
              onComment={setDraft}
              onSubmit={() => post.mutate()}
              submitLabel={post.isPending ? 'Posting…' : 'Add comment'}
              canSubmit={Boolean(draft.trim()) && !post.isPending}
            />
          </Timeline>
        </ReadField>

        <History taskId={task.id} />
      </ModalBody>
    </Modal>
  )
}

/**
 * The card's kind, urgency, status and template, as a row of chips.
 *
 * The same two icons the board card carries, with the words kept: a dialog has
 * the room the card does not, and this is where you come to read the card
 * rather than scan it.
 */
function TaskChips({ task }: { task: TaskDetail }) {
  const priorityLabel =
    PRIORITIES.find((option) => option.value === task.priority)?.label ?? task.priority

  return (
    <div className={styles.chips}>
      <span className={`${styles.chip} ${styles[`type_${task.type}`]}`}>
        <TypeIcon type={task.type} />
        {task.type}
      </span>
      <span className={`${styles.chip} ${styles[`priority_${task.priority}`]}`}>
        <PriorityIcon priority={task.priority} />
        {priorityLabel}
      </span>
      {/* Filtered, because a cancelled card's chip has no ink or dot of its
          own, and a template string would put the word "undefined" in its
          class list. */}
      <span className={[styles.chip, styles[`state_${task.status}`]].filter(Boolean).join(' ')}>
        {STATUS_LABELS[task.status]}
      </span>
      {/* Last, and worded rather than iconised: a template is the only
          chip here that names something the project invented. */}
      {task.template_name ? <span className={styles.chip}>◇ {task.template_name}</span> : null}
    </div>
  )
}

/** The goal the card is written under, as a link to the goal's own page. */
function GoalReadField({
  projectKey,
  task,
  onClose,
}: {
  projectKey: string
  task: TaskDetail
  onClose: () => void
}) {
  if (!task.goal_id || !task.goal_name || !task.goal_colour) return null

  return (
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
  )
}

/**
 * Where the card stands: its column, or for a sub-task whether it is finished.
 *
 * A sub-task is in no column, so the row that would name one says the thing
 * that answers the same question for it instead. An empty Column would read as
 * one that failed to load.
 */
function ColumnOrFinishedReadField({
  task,
  column,
}: {
  task: TaskDetail
  column: BoardColumn | undefined
}) {
  const finishedOn = task.finished_at ? formatDayWithYear(task.finished_at.slice(0, 10)) : null

  if (task.parent_id !== null) {
    return <ReadField label="Finished">{finishedOn ?? 'Not yet'}</ReadField>
  }

  // Off the board, in no column: where it is is cold storage.
  if (task.archived_at) {
    return (
      <ReadField label="Column">
        Cold storage
        {task.outcome ? <span className={shared.aside}>· {task.outcome}</span> : null}
        {finishedOn ? <span className={shared.aside}>· finished {finishedOn}</span> : null}
      </ReadField>
    )
  }

  // The column is where a card is; the date beside it is when being there
  // started meaning done. Shown only once there is one, so a card still on its
  // way says where it is and nothing more.
  return (
    <ReadField label="Column">
      {column?.name ?? '—'}
      {task.outcome ? <span className={shared.aside}>· {task.outcome}</span> : null}
      {finishedOn ? <span className={shared.aside}>· finished {finishedOn}</span> : null}
    </ReadField>
  )
}

/**
 * Says a card is in cold storage, and offers the way back.
 *
 * Near the top rather than among the fields, because it changes how the rest
 * of the card reads: everything below is true, and none of it is on the board.
 * The button is drawn only for a reader whose role allows cards — the server
 * refuses everyone else, and a button that can only fail is not an offer.
 */
function StoredNotice({
  projectKey,
  task,
  lastColumn,
  announce,
  onDone,
}: {
  projectKey: string
  task: TaskDetail
  lastColumn: BoardColumn | undefined
  announce: (message: string) => void
  onDone: () => Promise<void>
}) {
  const may = usePermissions(projectKey)
  const restore = useMutation({
    mutationFn: () => api.restoreTask(task.reference),
    onSuccess: async (restored) => {
      announce(`${restored.reference} restored to ${lastColumn?.name ?? 'the board'}.`)
      await onDone()
    },
  })
  const storedOn = task.archived_at ? formatDayWithYear(task.archived_at.slice(0, 10)) : null

  return (
    <div className={styles.stored} role="note">
      <ErrorNotice error={restore.error} />
      <p>
        In cold storage since {storedOn}. It is off the board, still finished, and in the CSV
        written that day.
      </p>
      {may('tasks') ? (
        <Button small disabled={restore.isPending} onClick={() => restore.mutate()}>
          {restore.isPending ? 'Restoring…' : `Restore to ${lastColumn?.name ?? 'the board'}`}
        </Button>
      ) : null}
    </div>
  )
}

/**
 * One column's date, said as what it still asks for.
 *
 * Three states and not four: reached, late, and simply ahead. A date the card
 * has passed is never late however long ago it was — the card got there, and
 * the schedule has moved on to the next column.
 */
function ColumnDateRead({ entry }: { entry: ColumnDueDate }) {
  return (
    <li className={entry.met ? styles.columnDateMet : undefined}>
      <span className={styles.columnDateName}>{entry.column_name}</span>
      <DateRead iso={entry.due_date} late={!entry.met && isOverdue(entry.due_date)} />
      {entry.met ? <span className={styles.columnDateNote}>reached</span> : null}
    </li>
  )
}

/** A date in full, flagged when it has passed with the work still owed. */
function DateRead({ iso, late }: { iso: string | null; late: boolean }) {
  return (
    <span className={late ? styles.late : ''}>
      {late ? '⚠ ' : ''}
      {formatDayWithYear(iso)}
    </span>
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

/** A `YYYY-MM-DD` day in full, year and all — or a dash where there is none. */
function formatDayWithYear(iso: string | null): string {
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
