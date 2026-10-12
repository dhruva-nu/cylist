/**
 * A card's sub-tasks, of both kinds: the ones with a reference and an owner of
 * their own, and the tick boxes that live on the card alone.
 *
 * Both are ticked off in place — from either half of the dialog — rather than
 * on the Save button: a checkbox that only takes effect when you remember to
 * press Save is a checkbox that lies. What a sub-task's *fields* say is never
 * edited here: "Split into a sub-task" hands the job back to the board, which
 * opens a second dialog for it, because a sub-task needs every field a card
 * needs.
 */

import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import {
  api,
  type ChecklistItem,
  type ChecklistState,
  type FiledItem,
  type Person,
  type Task,
  type TaskDetail,
} from '../../api/client'
import { Field } from '../Modal'
import { MentionBox } from '../Mentions'
import { STATUS_LABELS } from '../taskLabels'
import { Avatar, Button, ErrorNotice, Tagged } from '../ui'
import { CHECKLIST_LABELS, useSubtaskTicking } from './useSubtaskTicking'
import shared from './shared.module.css'
import styles from './Subtasks.module.css'

interface SubtaskListProps {
  task: TaskDetail
  /** The people a checklist item may tag, and whose tags it draws. */
  members: Person[]
  /** The files it may tag, and whose tags it draws as links. */
  files: readonly FiledItem[]
  onOpenTask?: ((taskRef: string) => void) | undefined
  announce: (message: string) => void
  onDone: () => Promise<void>
}

/**
 * Both kinds of sub-task, on the form: ticked, added, cancelled and deleted.
 *
 * The tick boxes save on the spot rather than on the dialog's Save button: a
 * checkbox that quietly waits for a second, unrelated button is a checkbox
 * that has already lied to whoever ticked it. The cards do not save here at
 * all — splitting hands the job back to the board, which opens a fresh dialog
 * for the new card, because a sub-task needs every field a card needs.
 */
export function Subtasks({
  task,
  members,
  files,
  onOpenTask,
  onSplit,
  announce,
  onDone,
}: SubtaskListProps & {
  onSplit?: ((parentRef: string) => void) | undefined
}) {
  const [title, setTitle] = useState('')
  const ticking = useSubtaskTicking({ task, announce, onDone })

  const add = useMutation({
    mutationFn: (text: string) => api.addChecklistItem(task.id, text),
    onSuccess: async (item) => {
      announce(`Added ${item.title} to the checklist.`)
      setTitle('')
      await ticking.refresh()
    },
  })

  const remove = useMutation({
    mutationFn: (id: string) => api.deleteChecklistItem(id),
    onSuccess: ticking.refresh,
  })

  const busy = ticking.busy || remove.isPending

  function addItem() {
    if (title.trim()) add.mutate(title.trim())
  }

  return (
    <Field label="Sub-tasks">
      <ErrorNotice error={add.error ?? ticking.error ?? remove.error} />

      <div className={styles.subtasks}>
        <SubtaskRows
          task={task}
          members={members}
          files={files}
          busy={busy}
          onOpenTask={onOpenTask}
          onFinish={ticking.setFinished}
          onSetState={ticking.setItemState}
          onRemove={(id) => remove.mutate(id)}
        />

        <div className={shared.composer}>
          {/* Enter is the list's own gesture — type a line, press enter, type
              the next — and it is also how the suggestion list accepts a name.
              The box stops the key while the list is open, so the tag is taken
              first and the item is added by the enter after it. */}
          <MentionBox
            value={title}
            onChange={setTitle}
            members={members}
            files={files}
            className={shared.grow}
            aria-label="Add a checklist item"
            maxLength={200}
            onEnter={addItem}
            placeholder="Add a checklist item, @ to tag someone, > a file…"
          />
          <Button small disabled={!title.trim() || add.isPending} onClick={addItem}>
            Add
          </Button>
        </div>

        {/* A sub-task cannot be split again: the board would stop being one. */}
        {task.parent_id === null ? (
          <Button small variant="ghost" onClick={() => onSplit?.(task.reference)}>
            + Split into a sub-task with an owner of its own
          </Button>
        ) : (
          <SubtaskOfNote task={task} onOpenTask={onOpenTask} />
        )}

        <OpenSubtasksGate outstanding={task.open_subtask_count} />
      </div>
    </Field>
  )
}

/**
 * Both kinds of sub-task, on the detail view.
 *
 * The twin of `Subtasks`, and it ticks. Everything else on the detail view is
 * text, because a card should be readable without being editable — but
 * finishing a sub-task is not editing this card. It is settling a different
 * item, which is the whole reason to be looking at the list, and requiring the
 * pencil for it would mean opening a form over every field of the parent in
 * order to tick one box that belongs to something else.
 *
 * So the line is drawn at what a control changes rather than at whether there
 * is one: nothing on this view can alter the card's own fields, and the two
 * things it can do — tick a sub-task off, open one — are both about the
 * sub-tasks. Adding, retitling, cancelling and deleting them stay behind the
 * pencil with everything else.
 */
export function SubtasksRead({
  task,
  members,
  files,
  onOpenTask,
  announce,
  onDone,
}: SubtaskListProps) {
  const ticking = useSubtaskTicking({ task, announce, onDone })

  return (
    <div className={styles.subtasks}>
      <ErrorNotice error={ticking.error} />

      <SubtaskRows
        task={task}
        members={members}
        files={files}
        busy={ticking.busy}
        onOpenTask={onOpenTask}
        onFinish={ticking.setFinished}
        onSetState={ticking.setItemState}
      />

      {task.parent_id === null ? null : <SubtaskOfNote task={task} onOpenTask={onOpenTask} />}

      <OpenSubtasksGate outstanding={task.open_subtask_count} />
    </div>
  )
}

/**
 * The rows of both kinds of sub-task — the cards, then the tick boxes — or a
 * line saying there are none.
 *
 * Without `onRemove` a tick box is only a tick box, which is the detail view's
 * form of the list. See `ChecklistRow`.
 */
function SubtaskRows({
  task,
  members,
  files,
  busy,
  onOpenTask,
  onFinish,
  onSetState,
  onRemove,
}: {
  task: TaskDetail
  members: Person[]
  files: readonly FiledItem[]
  busy: boolean
  onOpenTask?: ((taskRef: string) => void) | undefined
  onFinish: (child: Task, finished: boolean) => void
  onSetState: (itemId: string, state: ChecklistState) => void
  onRemove?: ((itemId: string) => void) | undefined
}) {
  return (
    <>
      {task.subtasks.map((child) => (
        <SubtaskCardRow
          key={child.id}
          child={child}
          busy={busy}
          onOpen={() => onOpenTask?.(child.reference)}
          onFinish={(finished) => onFinish(child, finished)}
        />
      ))}

      {task.checklist.map((item) => (
        <ChecklistRow
          key={item.id}
          item={item}
          members={members}
          files={files}
          busy={busy}
          onSetState={(state) => onSetState(item.id, state)}
          onRemove={onRemove ? () => onRemove(item.id) : undefined}
        />
      ))}

      {task.subtasks.length === 0 && task.checklist.length === 0 ? (
        <span className={shared.empty}>No sub-tasks.</span>
      ) : null}
    </>
  )
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

  return (
    <div className={`${styles.subtaskRow} ${finished || cancelled ? styles.settled : ''}`}>
      <input
        type="checkbox"
        checked={finished}
        // A cancelled sub-task is settled already and ticking it would say the
        // work happened. Reopen it with the status control on its own card.
        disabled={busy || cancelled}
        aria-label={finishBoxLabel(child.reference, { finished, cancelled })}
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
          STATUS_LABELS[child.status]
        )}
      </span>
    </div>
  )
}

/** What the tick box beside a sub-task says it will do. */
function finishBoxLabel(
  reference: string,
  { finished, cancelled }: { finished: boolean; cancelled: boolean },
): string {
  if (cancelled) return `${reference} is cancelled`
  if (finished) return `${reference} is finished. Reopen it.`
  return `Finish ${reference}.`
}

/** The card a sub-task belongs to, as the way back to it. */
function SubtaskOfNote({
  task,
  onOpenTask,
}: {
  task: TaskDetail
  onOpenTask?: ((taskRef: string) => void) | undefined
}) {
  return (
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
  )
}

/** How many sub-tasks are still keeping the card out of the board's last column. */
function OpenSubtasksGate({ outstanding }: { outstanding: number }) {
  if (!outstanding) return null

  return (
    <small className={styles.gate}>
      {outstanding} still open. Every sub-task has to be finished or cancelled before this card can
      move to {'the board’s last column'}.
    </small>
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
  files,
  busy,
  onSetState,
  onRemove,
}: {
  item: ChecklistItem
  /** Only to draw the title's `@` and `>` tags as tags. */
  members: Person[]
  files: readonly FiledItem[]
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
          <Tagged text={item.title} members={members} files={files} />
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
