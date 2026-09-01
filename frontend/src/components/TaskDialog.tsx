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
 * The status control is the interesting part. Choosing On hold or Blocked
 * reveals a reason box and a picker of people to tag, and neither the button
 * nor the server will let the change through without a reason — a red card
 * that does not say why is a question rather than information.
 *
 * Saving may be several requests: the fields, then a move, then the status
 * change, then a comment. They go in that order so the status entry lands on a
 * card that already reads the way it will after the save.
 *
 * Sub-tasks come in two kinds, and both are ticked off in place — from either
 * half of the dialog — rather than on the Save button: a checkbox that only
 * takes effect when you remember to press Save is a checkbox that lies. A tick
 * box settles itself; a sub-task with a card of its own is settled by being
 * moved to the board's last column, since "done" is a place on the board. What
 * a sub-task card's *fields* say is never edited here: "Split into a sub-task"
 * hands the job back to the board, which opens a second dialog for the new
 * card.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type KeyboardEvent, type ReactNode } from 'react'
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
  type TaskPriority,
  type TaskStatus,
  type TaskType,
} from '../api/client'
import { Field, FieldPair, Modal, ModalBody } from './Modal'
import { Avatar, Button, ErrorBanner, PriorityIcon, SubStatusBar, TaskRef, TypeIcon } from './ui'
import styles from './TaskDialog.module.css'

const STATUSES: { value: TaskStatus; label: string }[] = [
  { value: 'active', label: 'Active' },
  { value: 'hold', label: 'On hold' },
  { value: 'blocked', label: 'Blocked' },
  { value: 'cancelled', label: 'Cancelled' },
]

const TYPES: TaskType[] = ['feature', 'bug', 'chore']

const PRIORITIES: { value: TaskPriority; label: string }[] = [
  { value: 'urgent', label: 'Urgent' },
  { value: 'asap', label: 'ASAP' },
  { value: 'week', label: 'This week' },
  { value: 'someday', label: 'Someday' },
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
  const late = isOverdue(task.due_date)
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
        </div>

        {task.sub_statuses.length ? (
          <ReadField label="Sub-status">
            <SubStatusBar labels={task.sub_statuses} index={task.sub_status_index ?? 0} wrap />
          </ReadField>
        ) : null}

        <ReadField label="Description">
          <p className={styles.prose}>{task.description}</p>
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

        <div className={styles.readPair}>
          <ReadField label="Column">{column?.name ?? '—'}</ReadField>
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
            columns={columns}
            onOpenTask={onOpenTask}
            announce={announce}
            onDone={onDone}
          />
        </ReadField>

        <ReadField label="Timeline">
          <div className={styles.timeline}>
            {task.comments.length ? (
              task.comments.map((entry) => <Entry key={entry.id} entry={entry} members={members} />)
            ) : (
              <span className={styles.empty}>Nothing has happened to this card yet.</span>
            )}
          </div>
        </ReadField>
      </ModalBody>
    </Modal>
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

function formatDue(iso: string): string {
  if (!iso) return '—'
  return localDate(iso).toLocaleDateString('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

function isOverdue(iso: string): boolean {
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
    priority: task?.priority ?? 'someday',
    sub_statuses: task?.sub_statuses ?? [],
    due_date: task?.due_date ?? '',
    assignee_id: task?.assignee.id ?? members[0]?.id ?? '',
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
        sub_statuses: form.sub_statuses.map((label) => label.trim()),
        jira_ref: form.jira_ref?.trim() ? form.jira_ref.trim() : null,
        pr_ref: form.pr_ref?.trim() ? form.pr_ref.trim() : null,
      }
      const saved = task
        ? await api.updateTask(task.id, { ...payload, sub_status_index: subStatusIndex })
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

        <Field label="Priority" required>
          <select
            value={form.priority}
            onChange={(event) => setForm({ ...form, priority: event.target.value as TaskPriority })}
          >
            {PRIORITIES.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </Field>

        <Field
          label="Sub-status"
          hint="Up to 4 stages, in order. Moving the card between them happens on the board."
        >
          <SubStatusListEditor
            value={form.sub_statuses}
            current={subStatusIndex}
            onChange={(sub_statuses, current) => {
              setForm({ ...form, sub_statuses })
              setSubStatusIndex(current)
            }}
          />
        </Field>

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
  onChange,
}: {
  value: string[]
  current: number
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
          <input
            value={label}
            maxLength={60}
            placeholder={`Stage ${index + 1}`}
            aria-label={`Sub-status stage ${index + 1}`}
            onChange={(event) => {
              const next = [...value]
              next[index] = event.target.value
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
 * Ticking a sub-task off, shared by the detail view and the form.
 *
 * Two kinds of sub-task settle two different ways. A tick box has a state of
 * its own. A sub-task with a card is finished by being moved to the board's
 * last column, because "done" is a place on this board and not a field — the
 * same rule the server enforces when it refuses a parent whose sub-tasks are
 * still open.
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

  // Position 0, as every move made from a dialog is: a card dropped at the
  // bottom of a column lands somewhere the reader cannot see.
  const setColumn = useMutation({
    mutationFn: ({ child, column }: { child: Task; column: BoardColumn }) =>
      api.moveTask(child.id, column.id, 0),
    onSuccess: async (moved, { column }) => {
      announce(`${moved.reference} moved to ${column.name}.`)
      await refresh()
    },
  })

  return { refresh, setState, setColumn }
}

/**
 * One sub-task that has a card of its own, in either list.
 *
 * The tick box is the point of it: the reason to be looking at this list is to
 * see what is left, and a list you have to leave in order to finish anything
 * is a list that gets left. Ticking is a move, so the label names the column —
 * a checkbox that silently relocates a card on the board behind the dialog
 * would be worse than no checkbox at all. Unticking returns the card to the
 * first column, which is the only honest destination: where it sat before it
 * was ticked is recorded nowhere.
 */
function SubtaskCardRow({
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
  const { refresh, setState, setColumn } = useSubtaskTicking({ task, announce, onDone })

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

  const error = add.error ?? setState.error ?? remove.error ?? setColumn.error
  const outstanding = task.open_subtask_count
  const busy = setState.isPending || setColumn.isPending || remove.isPending
  const finishedColumn = columns[columns.length - 1]
  const firstColumn = columns[0]

  return (
    <Field label="Sub-tasks">
      {error ? <ErrorBanner>{error.message}</ErrorBanner> : null}

      <div className={styles.subtasks}>
        {task.subtasks.map((child) => (
          <SubtaskCardRow
            key={child.id}
            child={child}
            column={columns.find((option) => option.id === child.column_id)}
            finishedColumn={finishedColumn}
            firstColumn={firstColumn}
            busy={busy}
            onOpen={() => onOpenTask?.(child.reference)}
            onMove={(column) => setColumn.mutate({ child, column })}
          />
        ))}

        {task.checklist.map((item) => (
          <ChecklistRow
            key={item.id}
            item={item}
            busy={busy}
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
  columns,
  onOpenTask,
  announce,
  onDone,
}: {
  task: TaskDetail
  /** The board's columns, in order — the last of them is what "done" means. */
  columns: BoardColumn[]
  onOpenTask?: ((taskRef: string) => void) | undefined
  announce: (message: string) => void
  onDone: () => Promise<void>
}) {
  const { setState, setColumn } = useSubtaskTicking({ task, announce, onDone })

  const outstanding = task.open_subtask_count
  const error = setState.error ?? setColumn.error
  const busy = setState.isPending || setColumn.isPending
  const finishedColumn = columns[columns.length - 1]
  const firstColumn = columns[0]

  return (
    <div className={styles.subtasks}>
      {error ? <ErrorBanner>{error.message}</ErrorBanner> : null}

      {task.subtasks.map((child) => (
        <SubtaskCardRow
          key={child.id}
          child={child}
          column={columns.find((option) => option.id === child.column_id)}
          finishedColumn={finishedColumn}
          firstColumn={firstColumn}
          busy={busy}
          onOpen={() => onOpenTask?.(child.reference)}
          onMove={(column) => setColumn.mutate({ child, column })}
        />
      ))}

      {task.checklist.map((item) => (
        <ChecklistRow
          key={item.id}
          item={item}
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
  busy,
  onSetState,
  onRemove,
}: {
  item: ChecklistItem
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
        <span className={item.state === 'cancelled' ? styles.struck : ''}>{item.title}</span>
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
