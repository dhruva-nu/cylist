/**
 * The task dialog: every field of a card, plus its timeline.
 *
 * Opening a card lands on a detail view. Every field of the card is text
 * there — no input, select or textarea is rendered for any of them — so a card
 * can be read, and read out, with no way to change it by accident. Editing is
 * behind the pencil in the header (and the Edit button beside it in the
 * footer); pressing either swaps the same dialog over to the form. Cancel,
 * Escape and the backdrop go back to the detail view rather than throwing the
 * whole dialog away, so a mis-click costs one keystroke instead of your place
 * on the board. A new task has nothing to read yet, so it opens straight on
 * the form.
 *
 * Two controls on the detail view are not Edit or Close, and the line they sit
 * on the right side of is *what a control changes*, not whether one exists.
 * Ticking a sub-task off settles a different item, and making that go through
 * the pencil would mean opening a form over every field of the parent to
 * finish something that is not the parent. The select beside a pull request
 * (CYLIST-76) is there on the same ground: a pull request is raised, reviewed
 * and merged somewhere else, and what the select records is what happened over
 * there. Everything that is a field *of this card* is still text here.
 *
 * Neither column nor status appears on a *new* card: work enters the board at
 * the first column and starts out active, and a control with one possible
 * value is a question that reads as though it had an answer.
 *
 * Under the timeline sits the card's history: what was changed, when, and who
 * changed it. Closed until asked for, and read from the server's own wording
 * rather than reconstructed here — see `taskDialog/History.tsx`.
 *
 * A sub-task opened here is the same dialog with one thing missing — the column
 * — and one thing in its place: whether it is finished. It is the only field a
 * card has that a sub-task does not, and the only field a sub-task has that a
 * card does not.
 *
 * The halves of the dialog live in `./taskDialog/`: the detail view, the form,
 * and the lists both of them draw.
 */

import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type TaskDetail } from '../api/client'
import { queryKeys, useMembers } from '../api/queries'
import { Modal, ModalBody } from './Modal'
import { useProjectFiles } from './projectFiles'
import type { DialogData, TaskDialogProps } from './taskDialog/dialogProps'
import { TaskDetailView } from './taskDialog/TaskDetailView'
import { TaskForm } from './taskDialog/TaskForm'
import { Button, ErrorNotice } from './ui'

export function TaskDialog(props: TaskDialogProps) {
  const { projectKey, taskId, onClose } = props

  const task = useQuery({
    queryKey: queryKeys.task(taskId),
    queryFn: ({ signal }) => api.getTask(taskId ?? '', signal),
    enabled: taskId !== null,
  })
  const members = useMembers(projectKey)
  /** The project's files, for the `>` tags. Not waited for — see the hook. */
  const files = useProjectFiles(projectKey)

  const loading = (taskId !== null && task.isPending) || members.isPending
  const error = task.error ?? members.error

  if (loading || error || !members.data) {
    return (
      <Modal
        wide
        title={taskId ? 'Task' : 'New task'}
        onClose={onClose}
        footer={<Button onClick={onClose}>Close</Button>}
      >
        <ModalBody>{error ? <ErrorNotice error={error} /> : <p>Loading…</p>}</ModalBody>
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
        files={files}
        onCancel={onClose}
      />
    )
  }

  return (
    <TaskPanel
      key={detail.id}
      {...props}
      task={detail}
      members={members.data.members}
      files={files}
    />
  )
}

/**
 * One card: read it, then choose to edit it.
 *
 * The mode lives here rather than in either half, so that leaving the form
 * unmounts it — cancelling really does discard the edits rather than leaving
 * them parked behind a flag.
 */
function TaskPanel(props: TaskDialogProps & DialogData & { task: TaskDetail }) {
  const [editing, setEditing] = useState(false)

  if (editing) return <TaskForm {...props} onCancel={() => setEditing(false)} />
  return <TaskDetailView {...props} onEdit={() => setEditing(true)} />
}
