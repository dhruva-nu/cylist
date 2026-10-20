/**
 * Writing a new card, or editing one: every field, across the form's tabs, and
 * the Save that sends them.
 *
 * The form is tabbed. Basics holds what the card *is* — its name, what done
 * looks like, how urgent, what kind, and which template it follows; Details
 * holds how it is tracked. On an existing card a further tab carries its
 * sub-tasks and its conversation, which are about other work and about people
 * rather than about this card's fields. Every panel stays mounted, so tabbing
 * away never discards what has been typed, and a tab carries a dot while
 * something required on it is still blank — a disabled Save button has to have
 * somewhere to point.
 *
 * Saving may be several requests: the fields, then a move, then the status
 * change, then a comment. They go in that order so the status entry lands on a
 * card that already reads the way it will after the save.
 */

import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type TaskDetail, type TaskInput } from '../../api/client'
import { useIdentity } from '../../api/queries'
import { DialogActions, Field, Modal, ModalBody } from '../Modal'
import { Button, ErrorNotice } from '../ui'
import { Comments } from './Comments'
import type { DialogData, TaskDialogProps } from './dialogProps'
import { BasicsFields, ColumnDatesFields, TrackingFields } from './FormFields'
import { TabPanel, Tabs, type TabId } from './FormTabs'
import { StallFields, StatusField, useStatusChange } from './StatusFields'
import { Subtasks } from './Subtasks'
import {
  blankRequiredFields,
  columnDueDatesInput,
  formTabs,
  formTitle,
  initialFormFields,
  saveButtonLabel,
  summariseSave,
  taskPayload,
} from './taskForm'
import { useCommentAuthor } from './useCommentAuthor'
import { useCommentDeleting } from './useCommentDeleting'
import styles from './TaskForm.module.css'

export function TaskForm({
  projectKey,
  parentRef,
  columns,
  firstColumn,
  templates,
  goals,
  defaultGoalId,
  task,
  members,
  files,
  announce,
  onOpenTask,
  onSplit,
  onDone,
  onClose,
  onCancel,
}: TaskDialogProps &
  DialogData & {
    /** Null while a new card is being written. */
    task: TaskDetail | null
    /**
     * Leaving the form without saving. On an existing card that is a step back
     * to the detail view; on a new one there is nothing behind it, so it closes.
     */
    onCancel: () => void
  }) {
  // Who is looking, so a new card defaults to being theirs — the same answer
  // the API gives a card created without an assignee. Read from the cache the
  // auth gate already filled; a card started before it lands falls through to
  // the first member, which is what it used to do for everybody.
  const identity = useIdentity()
  const deleting = useCommentDeleting({ projectKey, announce, onDone })
  const [form, setForm] = useState<TaskInput>(() =>
    initialFormFields(task, members, identity.data, defaultGoalId),
  )
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
  const statusChange = useStatusChange(task)
  const { status, reason, tagged, changing, stalling } = statusChange
  const [comment, setComment] = useState('')
  const [author, setAuthor] = useCommentAuthor(members)
  const [tab, setTab] = useState<TabId>('basics')

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
  /**
   * Whether this card is on the board: a top-level card, as against a
   * sub-task. Only one of those passes through columns and so can be dated in
   * them, and only one of those can be on a goal.
   */
  const onBoard = task ? task.parent_id === null : !parentRef
  /** The columns a date can be set for: every one but the last, which is `due_date`. */
  const stages = columns.slice(0, -1)
  const lastColumn = columns.at(-1)
  /** How far along the board the card has got, so a date behind it says so. */
  const reachedColumnIndex = columns.findIndex((column) => column.id === task?.column_id)

  /** Change some of the card's fields and leave the rest as they are. */
  function updateForm(changes: Partial<TaskInput>) {
    setForm((current) => ({ ...current, ...changes }))
  }

  /** The card's own fields: an edit, a new sub-task, or a new card. */
  function saveFields(payload: TaskInput) {
    if (task) return api.updateTask(task.id, { ...payload, sub_status_index: subStatusIndex })
    if (parentRef) return api.createSubtask(parentRef, payload)
    return api.createTask(projectKey, payload)
  }

  const save = useMutation({
    mutationFn: async () => {
      const payload = taskPayload(form, onBoard ? columnDueDatesInput(stages, columnDates) : null)
      // Top of the column rather than the bottom: a card moved from a dialog
      // has no place on the board the reader is already looking at. Before the
      // edit when the template is changing too — see `retyping`.
      if (task && moving && retyping) await api.moveTask(task.id, columnId, 0)

      const saved = await saveFields(payload)

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
        summariseSave(
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
    mutationFn: (existing: TaskDetail) => api.deleteTask(existing.id),
    onSuccess: async (_deleted, existing) => {
      announce(`${existing.reference} deleted.`)
      await onDone()
      onClose()
    },
  })

  const blank = blankRequiredFields(form, statusChange.reasonMissing)
  const readyToSave = !blank.basics && !blank.details

  const tabs = formTabs({
    blank,
    showColumnDates: onBoard && scheduling,
    existing: task !== null,
  })

  const comments = (
    <Comments
      task={task}
      members={members}
      files={files}
      author={author}
      onAuthor={setAuthor}
      comment={comment}
      onComment={setComment}
      // Nothing has been said on a card being written, so nothing to take back.
      deleting={task ? deleting : null}
    />
  )

  return (
    <Modal
      // Every state of this dialog takes the same width — loading, reading and
      // editing — so pressing the pencil swaps the contents without the panel
      // resizing under the pointer.
      wide
      title={formTitle(task, parentRef)}
      // Escape and the backdrop mean "stop editing", which on an existing card
      // is the detail view rather than the board.
      onClose={onCancel}
      footer={
        <DialogActions
          submitLabel={saveButtonLabel(task, parentRef)}
          pendingLabel="Saving…"
          pending={save.isPending}
          disabled={!readyToSave}
          onSubmit={() => save.mutate()}
          onCancel={onCancel}
          note={
            task ? (
              <Button
                variant="ghost"
                danger
                className={styles.spacer}
                disabled={remove.isPending}
                onClick={() => remove.mutate(task)}
              >
                Delete
              </Button>
            ) : null
          }
        />
      }
    >
      <ModalBody>
        <ErrorNotice error={save.error ?? remove.error} />

        <Tabs tabs={tabs} current={tab} onSelect={setTab} />

        <TabPanel id="basics" current={tab}>
          <BasicsFields
            form={form}
            onChange={updateForm}
            members={members}
            files={files}
            templates={templates}
            columns={columns}
            goals={goals}
            offerGoal={onBoard}
          />
        </TabPanel>

        <TabPanel id="details" current={tab}>
          <TrackingFields
            form={form}
            onChange={updateForm}
            members={members}
            subStatusIndex={subStatusIndex}
            onSubStatusIndex={setSubStatusIndex}
            onBoard={onBoard}
            lastColumnName={lastColumn?.name}
            onAskForColumnDates={() => {
              setScheduling(true)
              setTab('dates')
            }}
          />

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

          {task ? <StatusField status={status} onChange={statusChange.setStatus} /> : null}

          {stalling ? (
            <StallFields
              status={status}
              reason={reason}
              onReason={statusChange.setReason}
              tagged={tagged}
              onToggleTag={statusChange.toggleTag}
              members={members}
            />
          ) : null}

          {/* A card being written has no timeline yet, but it may well be
              worth a first word — so the composer follows it onto Details
              rather than disappearing with the tab it lives on for a card
              that exists. */}
          {task ? null : comments}
        </TabPanel>

        {onBoard && scheduling ? (
          <TabPanel id="dates" current={tab}>
            <ColumnDatesFields
              stages={stages}
              reachedColumnIndex={reachedColumnIndex}
              dates={columnDates}
              onChange={setColumnDates}
            />
          </TabPanel>
        ) : null}

        {task ? (
          <TabPanel id="work" current={tab}>
            <Subtasks
              task={task}
              members={members}
              files={files}
              onOpenTask={onOpenTask}
              onSplit={onSplit}
              announce={announce}
              onDone={onDone}
            />

            {comments}
          </TabPanel>
        ) : null}
      </ModalBody>
    </Modal>
  )
}
