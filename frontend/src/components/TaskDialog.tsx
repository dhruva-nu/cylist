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
 */

import { useMutation, useQuery } from '@tanstack/react-query'
import { useState, type KeyboardEvent } from 'react'
import {
  api,
  type BoardColumn,
  type Person,
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
]

const TYPES: TaskType[] = ['feature', 'bug', 'chore']

interface DialogProps {
  projectKey: string
  /** Null opens the dialog for a new task, which is locked to the first column. */
  taskId: string | null
  columns: BoardColumn[]
  firstColumn: BoardColumn
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
  columns,
  firstColumn,
  task,
  members,
  announce,
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
      title={task ? task.reference : 'New task'}
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
            {save.isPending ? 'Saving…' : task ? 'Save changes' : 'Create task'}
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
