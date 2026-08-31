/**
 * The task dialog: every field of a card, plus its timeline.
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
 * Sub-tasks come in two kinds and are edited two ways. Tick boxes are saved
 * the moment they are ticked, because a checkbox that only takes effect when
 * you remember to press Save is a checkbox that lies. Sub-tasks with a card of
 * their own are not edited here at all: "Split into a sub-task" hands the job
 * back to the board, which opens a second dialog for the new card.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type KeyboardEvent } from 'react'
import {
  api,
  type BoardColumn,
  type ChecklistItem,
  type ChecklistState,
  type Person,
  type Task,
  type TaskComment,
  type TaskDetail,
  type TaskInput,
  type TaskStatus,
  type TaskType,
} from '../api/client'
import { Field, FieldPair, Modal, ModalBody } from './Modal'
import { Avatar, Button, ErrorBanner } from './ui'
import styles from './TaskDialog.module.css'

const STATUSES: { value: TaskStatus; label: string }[] = [
  { value: 'active', label: 'Active' },
  { value: 'hold', label: 'On hold' },
  { value: 'blocked', label: 'Blocked' },
  { value: 'cancelled', label: 'Cancelled' },
]

const TYPES: TaskType[] = ['feature', 'bug', 'chore']

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

  // Remounting per task keeps the form's initial state honest: a dialog opened
  // on a different card must not inherit the last one's half-typed edits.
  return (
    <TaskForm
      key={taskId ?? 'new'}
      {...props}
      task={task.data ?? null}
      members={members.data.members}
    />
  )
}

function TaskForm({
  projectKey,
  parentRef,
  columns,
  firstColumn,
  task,
  members,
  announce,
  onOpenTask,
  onSplit,
  onDone,
  onClose,
}: DialogProps & { task: TaskDetail | null; members: Person[] }) {
  const [form, setForm] = useState<TaskInput>({
    title: task?.title ?? '',
    description: task?.description ?? '',
    type: task?.type ?? 'feature',
    due_date: task?.due_date ?? '',
    assignee_id: task?.assignee.id ?? members[0]?.id ?? '',
    jira_ref: task?.jira_ref ?? '',
    pr_ref: task?.pr_ref ?? '',
  })
  const [columnId, setColumnId] = useState(task?.column_id ?? firstColumn.id)
  const [status, setStatus] = useState<TaskStatus>(task?.status ?? 'active')
  const [reason, setReason] = useState('')
  const [tagged, setTagged] = useState<string[]>(task?.waiting_on.map((p) => p.id) ?? [])
  const [comment, setComment] = useState('')
  const [author, setAuthor] = useState('')

  const was = task?.status ?? 'active'
  const changing = status !== was
  const stalling = changing && status !== 'active'
  const moving = task !== null && columnId !== task.column_id
  const destination = columns.find((column) => column.id === columnId)?.name ?? null

  const save = useMutation({
    mutationFn: async () => {
      const payload = {
        ...form,
        jira_ref: form.jira_ref?.trim() ? form.jira_ref.trim() : null,
        pr_ref: form.pr_ref?.trim() ? form.pr_ref.trim() : null,
      }
      const saved = task
        ? await api.updateTask(task.id, payload)
        : parentRef
          ? await api.createSubtask(parentRef, payload)
          : await api.createTask(projectKey, payload)

      // Top of the column rather than the bottom: a card moved from a dialog
      // has no place on the board the reader is already looking at.
      if (task && columnId !== task.column_id) await api.moveTask(saved.id, columnId, 0)

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
    form.due_date &&
    form.assignee_id &&
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

  return (
    <Modal
      title={task ? task.reference : parentRef ? `New sub-task of ${parentRef}` : 'New task'}
      onClose={onClose}
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
          <Button onClick={onClose}>Cancel</Button>
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

        <Field label="Task name" required>
          <input
            value={form.title}
            maxLength={200}
            onChange={(event) => setForm({ ...form, title: event.target.value })}
            placeholder="Short, verb-first"
          />
        </Field>

        <Field label="Description" required>
          <textarea
            value={form.description}
            onChange={(event) => setForm({ ...form, description: event.target.value })}
            placeholder="What done looks like"
          />
        </Field>

        <FieldPair>
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
          {task ? (
            <Field label="Column" required>
              <select value={columnId} onChange={(event) => setColumnId(event.target.value)}>
                {columns.map((column) => (
                  <option key={column.id} value={column.id}>
                    {column.name}
                  </option>
                ))}
              </select>
            </Field>
          ) : (
            <Field label="Column" hint="New tasks land in the first column. Move it afterwards.">
              <input value={firstColumn.name} disabled />
            </Field>
          )}
        </FieldPair>

        <FieldPair>
          <Field label="Due date" required>
            <input
              type="date"
              value={form.due_date}
              onChange={(event) => setForm({ ...form, due_date: event.target.value })}
            />
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

        <FieldPair>
          <Field label="Jira">
            <input
              value={form.jira_ref ?? ''}
              onChange={(event) => setForm({ ...form, jira_ref: event.target.value })}
              placeholder="ATL-00"
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
                className={status === option.value ? `${styles.on} ${styles[option.value]}` : ''}
                onClick={() => setStatus(option.value)}
                onKeyDown={(event) => walkStatus(event, index)}
              >
                {option.label}
              </button>
            ))}
          </div>
        </Field>

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

        {task ? (
          <Subtasks
            task={task}
            columns={columns}
            onOpenTask={onOpenTask}
            onSplit={onSplit}
            announce={announce}
            onDone={onDone}
          />
        ) : null}

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
                onChange={(event) => setAuthor(event.target.value)}
                aria-label="Comment as"
              >
                <option value="">Unattributed</option>
                {members.map((person) => (
                  <option key={person.id} value={person.id}>
                    {person.name}
                  </option>
                ))}
              </select>
              <input
                value={comment}
                aria-label="Add a comment"
                onChange={(event) => setComment(event.target.value)}
                placeholder="Add a comment…"
              />
            </div>
          </div>
        </Field>
      </ModalBody>
    </Modal>
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
        {entry.body}
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
  columns,
  onOpenTask,
  onSplit,
  announce,
  onDone,
}: {
  task: TaskDetail
  /** The board's columns, in order — the last of them is what "done" means. */
  columns: BoardColumn[]
  onOpenTask?: ((taskRef: string) => void) | undefined
  onSplit?: ((parentRef: string) => void) | undefined
  announce: (message: string) => void
  onDone: () => Promise<void>
}) {
  const [title, setTitle] = useState('')
  const queryClient = useQueryClient()

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: ['task', task.id] })
    await onDone()
  }

  const add = useMutation({
    mutationFn: (text: string) => api.addChecklistItem(task.id, text),
    onSuccess: async (item) => {
      announce(`Added ${item.title} to the checklist.`)
      setTitle('')
      await refresh()
    },
  })

  const setState = useMutation({
    mutationFn: ({ id, state }: { id: string; state: ChecklistState }) =>
      api.updateChecklistItem(id, { state }),
    onSuccess: async (item) => {
      announce(`${item.title} is ${CHECKLIST_LABELS[item.state].toLowerCase()}.`)
      await refresh()
    },
  })

  const remove = useMutation({
    mutationFn: (id: string) => api.deleteChecklistItem(id),
    onSuccess: refresh,
  })

  /**
   * Tick a sub-task's card off, or put it back.
   *
   * A move rather than a flag, because "done" is a place on this board and not
   * a field on the card — the same rule the server enforces when it refuses a
   * parent whose sub-tasks are still open. Position 0, as every move made from
   * a dialog is: a card dropped at the bottom of a column lands somewhere the
   * reader cannot see.
   */
  const setColumn = useMutation({
    mutationFn: ({ child, column }: { child: Task; column: BoardColumn }) =>
      api.moveTask(child.id, column.id, 0),
    onSuccess: async (moved, { column }) => {
      announce(`${moved.reference} moved to ${column.name}.`)
      await refresh()
    },
  })

  const error = add.error ?? setState.error ?? remove.error ?? setColumn.error
  const outstanding = task.open_subtask_count

  // The board's own order says which column means finished — the same answer
  // `columns.last` gives the server. Reopening sends the card back to the
  // first, which is the only column that means "not started" rather than a
  // guess at where the card was before it was ticked.
  const finishedColumn = columns[columns.length - 1]
  const firstColumn = columns[0]

  return (
    <Field label="Sub-tasks">
      {error ? <ErrorBanner>{error.message}</ErrorBanner> : null}

      <div className={styles.subtasks}>
        {task.subtasks.map((child) => (
          <SubtaskRow
            key={child.id}
            child={child}
            column={columns.find((option) => option.id === child.column_id)}
            finishedColumn={finishedColumn}
            firstColumn={firstColumn}
            busy={setColumn.isPending}
            onOpen={() => onOpenTask?.(child.reference)}
            onMove={(column) => setColumn.mutate({ child, column })}
          />
        ))}

        {task.checklist.map((item) => (
          <ChecklistRow
            key={item.id}
            item={item}
            busy={setState.isPending || remove.isPending}
            onSetState={(state) => setState.mutate({ id: item.id, state })}
            onRemove={() => remove.mutate(item.id)}
          />
        ))}

        {task.subtasks.length === 0 && task.checklist.length === 0 ? (
          <span className={styles.empty}>No sub-tasks.</span>
        ) : null}

        <div className={styles.composer}>
          <input
            value={title}
            aria-label="Add a checklist item"
            maxLength={200}
            onChange={(event) => setTitle(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && title.trim()) {
                event.preventDefault()
                add.mutate(title.trim())
              }
            }}
            placeholder="Add a checklist item…"
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
            + Split into a sub-task with its own card
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
 * One sub-task that has a card of its own.
 *
 * The tick box is here, and not only inside the card's own dialog, because the
 * reason to be looking at this list is to see what is left — and a list you
 * have to leave in order to tick something off is a list that gets left. It
 * ticks the same way the checklist above it does, so the two kinds of sub-task
 * are finished with the same gesture even though only one of them is a row in
 * a table.
 *
 * What it does is a move, so the label says which column: a checkbox that
 * silently relocates a card on the board behind the dialog is worse than no
 * checkbox at all. Ticking sends the card to the last column; unticking brings
 * it back to the first, which is the only honest destination — where the card
 * sat before it was ticked is not recorded anywhere.
 */
function SubtaskRow({
  child,
  column,
  finishedColumn,
  firstColumn,
  busy,
  onOpen,
  onMove,
}: {
  child: Task
  /** The column the card is in now, if the board still has it. */
  column: BoardColumn | undefined
  finishedColumn: BoardColumn | undefined
  firstColumn: BoardColumn | undefined
  busy: boolean
  onOpen: () => void
  onMove: (column: BoardColumn) => void
}) {
  const finished = finishedColumn !== undefined && child.column_id === finishedColumn.id
  const target = finished ? firstColumn : finishedColumn
  const status = STATUSES.find((option) => option.value === child.status)

  return (
    <div className={`${styles.subtaskRow} ${finished ? styles.settled : ''}`}>
      <input
        type="checkbox"
        checked={finished}
        disabled={busy || target === undefined}
        aria-label={
          target === undefined
            ? `${child.reference} cannot be moved`
            : finished
              ? `${child.reference} is done. Reopen it into ${target.name}.`
              : `Mark ${child.reference} done by moving it to ${target.name}.`
        }
        onChange={() => {
          if (target) onMove(target)
        }}
      />
      {/* The button is the whole of the rest of the row rather than the
          reference alone: the title is what the eye lands on, and a link you
          have to aim at is a link you misclick. It cannot wrap the tick box —
          one control does not go inside another. */}
      <button type="button" className={styles.subOpen} onClick={onOpen}>
        <span className={styles.subRef}>{child.reference}</span>
        <span className={styles.subTitle}>{child.title}</span>
      </button>
      {/* The column, because that is what the tick box just changed — the
          status only replaces it when it is the more urgent of the two. */}
      <span className={styles.subState}>
        {child.status === 'active' ? column?.name : status?.label}
      </span>
    </div>
  )
}

/** One tick box: ticked, cancelled, put back, or removed outright. */
function ChecklistRow({
  item,
  busy,
  onSetState,
  onRemove,
}: {
  item: ChecklistItem
  busy: boolean
  onSetState: (state: ChecklistState) => void
  onRemove: () => void
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
        <span className={item.state === 'cancelled' ? styles.struck : ''}>{item.title}</span>
      </label>
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
    </div>
  )
}
