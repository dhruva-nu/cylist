/**
 * The Kanban board.
 *
 * Two rules from the API are visible in the UI rather than only enforced by
 * it: "+ Add a task" appears under the first column alone, because that is
 * where new work lands; and the "+ Add a column" tile counts down to eight and
 * then goes flat, because that is where a board stops being readable.
 *
 * Dragging updates the cache before the request goes out. A card that snaps
 * back is how you find out the move failed — waiting for a round trip to see a
 * card move makes the board feel broken even when it is working.
 */

import {
  DndContext,
  PointerSensor,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams } from '@tanstack/react-router'
import { useState } from 'react'
import { api, type Board, type BoardColumn, type ColumnInput, type Task } from '../api/client'
import { Field, Modal, ModalBody } from '../components/Modal'
import { PageHead } from '../components/Shell'
import { TaskDialog } from '../components/TaskDialog'
import { Avatar, Button, EmptyState, ErrorBanner } from '../components/ui'
import styles from './ProjectBoard.module.css'

export function ProjectBoard() {
  const { projectKey } = useParams({ from: '/p/$projectKey/board' })
  const queryClient = useQueryClient()

  const [openTaskId, setOpenTaskId] = useState<string | null>(null)
  const [creatingTask, setCreatingTask] = useState(false)
  const [columnDialog, setColumnDialog] = useState<BoardColumn | 'new' | null>(null)

  const board = useQuery({
    queryKey: ['board', projectKey],
    queryFn: () => api.listColumns(projectKey),
  })
  const tasks = useQuery({
    queryKey: ['tasks', projectKey],
    queryFn: () => api.listTasks(projectKey),
  })

  const tasksKey = ['tasks', projectKey]

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['board', projectKey] }),
      queryClient.invalidateQueries({ queryKey: tasksKey }),
      queryClient.invalidateQueries({ queryKey: ['task'] }), // every open card's timeline
      queryClient.invalidateQueries({ queryKey: ['project-summary', projectKey] }),
    ])
  }

  const move = useMutation<Task, Error, Move, { previous: Task[] | undefined }>({
    mutationFn: ({ taskId, columnId, position }) => api.moveTask(taskId, columnId, position),
    onMutate: async ({ taskId, columnId, position }) => {
      await queryClient.cancelQueries({ queryKey: tasksKey })
      const previous = queryClient.getQueryData<Task[]>(tasksKey)
      queryClient.setQueryData<Task[]>(tasksKey, (current) =>
        current?.map((task) =>
          task.id === taskId ? { ...task, column_id: columnId, position } : task,
        ),
      )
      return { previous }
    },
    onError: (_error, _move, context) => {
      queryClient.setQueryData(tasksKey, context?.previous)
    },
    onSettled: refresh,
  })

  // A short drag threshold so a card can still be clicked open: without it
  // every press would start a drag and no click would ever land.
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }))

  if (board.isPending || tasks.isPending) return <EmptyState>Loading the board…</EmptyState>
  if (board.error) return <ErrorBanner>{board.error.message}</ErrorBanner>
  if (tasks.error) return <ErrorBanner>{tasks.error.message}</ErrorBanner>

  const columns = board.data.columns
  const firstColumn = columns[0]
  const byColumn = new Map(columns.map((column) => [column.id, [] as Task[]]))
  for (const task of tasks.data) byColumn.get(task.column_id)?.push(task)

  function onDragEnd(event: DragEndEvent) {
    const columnId = event.over?.id
    if (typeof columnId !== 'string') return

    const task = tasks.data?.find((candidate) => candidate.id === event.active.id)
    if (!task || task.column_id === columnId) return

    move.mutate({ taskId: task.id, columnId, position: byColumn.get(columnId)?.length ?? 0 })
  }

  return (
    <>
      <PageHead title="Kanban board">
        Cards enter at the first column and move wherever the work does. Colour flags anything on
        hold or blocked.
      </PageHead>

      {move.error ? <ErrorBanner>{move.error.message}</ErrorBanner> : null}

      <DndContext sensors={sensors} onDragEnd={onDragEnd}>
        <div className={styles.board}>
          {columns.map((column) => (
            <Column
              key={column.id}
              column={column}
              tasks={byColumn.get(column.id) ?? []}
              isFirst={column.id === firstColumn?.id}
              onOpenTask={setOpenTaskId}
              onAddTask={() => setCreatingTask(true)}
              onEdit={() => setColumnDialog(column)}
            />
          ))}
          <AddColumnTile board={board.data} onClick={() => setColumnDialog('new')} />
        </div>
      </DndContext>

      {columnDialog ? (
        <ColumnDialog
          projectKey={projectKey}
          column={columnDialog === 'new' ? null : columnDialog}
          canDelete={columns.length > board.data.min_columns}
          onDone={refresh}
          onClose={() => setColumnDialog(null)}
        />
      ) : null}

      {creatingTask && firstColumn ? (
        <TaskDialog
          projectKey={projectKey}
          taskId={null}
          columns={columns}
          firstColumn={firstColumn}
          onDone={refresh}
          onClose={() => setCreatingTask(false)}
        />
      ) : null}

      {openTaskId && firstColumn ? (
        <TaskDialog
          projectKey={projectKey}
          taskId={openTaskId}
          columns={columns}
          firstColumn={firstColumn}
          onDone={refresh}
          onClose={() => setOpenTaskId(null)}
        />
      ) : null}
    </>
  )
}

interface Move {
  taskId: string
  columnId: string
  position: number
}

function Column({
  column,
  tasks,
  isFirst,
  onOpenTask,
  onAddTask,
  onEdit,
}: {
  column: BoardColumn
  tasks: Task[]
  isFirst: boolean
  onOpenTask: (taskId: string) => void
  onAddTask: () => void
  onEdit: () => void
}) {
  const { setNodeRef, isOver } = useDroppable({ id: column.id })

  return (
    <section ref={setNodeRef} className={`${styles.column} ${isOver ? styles.over : ''}`}>
      <div className={styles.head}>
        <div className={styles.headRow}>
          <h3>{column.name}</h3>
          <span className={styles.headActions}>
            <span className={styles.count}>{tasks.length}</span>
            <Button variant="ghost" small onClick={onEdit} aria-label={`Edit ${column.name}`}>
              ···
            </Button>
          </span>
        </div>
        <p>{column.description}</p>
      </div>

      <div className={styles.cards}>
        {tasks.map((task) => (
          <TaskCard key={task.id} task={task} onOpen={() => onOpenTask(task.id)} />
        ))}
      </div>

      {isFirst ? (
        <button className={styles.addTask} onClick={onAddTask}>
          + Add a task
        </button>
      ) : (
        <div className={styles.foot} />
      )}
    </section>
  )
}

const STATUS_LABELS = { active: 'Active', hold: 'On hold', blocked: 'Blocked' } as const

function TaskCard({ task, onOpen }: { task: Task; onOpen: () => void }) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({ id: task.id })
  const late = isOverdue(task.due_date)

  return (
    <article
      ref={setNodeRef}
      {...attributes}
      {...listeners}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === 'Enter') onOpen()
      }}
      style={transform ? { transform: `translate3d(${transform.x}px, ${transform.y}px, 0)` } : {}}
      className={[styles.task, styles[task.status], isDragging && styles.dragging]
        .filter(Boolean)
        .join(' ')}
    >
      <div className={styles.taskRow}>
        <span className={styles.reference}>{task.reference}</span>
        {task.status === 'active' ? (
          <span className={`${styles.chip} ${styles[`type_${task.type}`]}`}>{task.type}</span>
        ) : (
          <span className={`${styles.pill} ${styles[`pill_${task.status}`]}`}>
            {STATUS_LABELS[task.status]}
          </span>
        )}
      </div>

      <div className={styles.title}>{task.title}</div>

      {task.waiting_on.length ? (
        <div className={styles.waiting}>
          Waiting on{' '}
          {task.waiting_on.map((person) => (
            <span key={person.id} className={styles.at}>
              @{person.name.split(' ')[0]}
            </span>
          ))}
        </div>
      ) : null}

      <div className={styles.taskRow}>
        <div className={styles.links}>
          {task.jira_ref ? <span>⌗ {task.jira_ref}</span> : null}
          {task.pr_ref ? <span>⎇ {task.pr_ref}</span> : null}
          {task.comment_count ? <span>✎ {task.comment_count}</span> : null}
        </div>
        <span className={styles.trailing}>
          <span className={`${styles.due} ${late ? styles.late : ''}`}>
            {late ? '⚠ ' : ''}
            {formatDue(task.due_date)}
          </span>
          <Avatar name={task.assignee.name} colour={task.assignee.colour} />
        </span>
      </div>
    </article>
  )
}

function AddColumnTile({ board, onClick }: { board: Board; onClick: () => void }) {
  const full = board.columns.length >= board.max_columns

  return (
    <button className={styles.addColumn} disabled={full} onClick={onClick}>
      {full ? (
        `Column limit reached (${board.max_columns} of ${board.max_columns})`
      ) : (
        <>
          + Add a column
          <span className={styles.hint}>
            {board.columns.length} of {board.max_columns} used
          </span>
        </>
      )}
    </button>
  )
}

/** Creates a column, or renames and deletes one. */
function ColumnDialog({
  projectKey,
  column,
  canDelete,
  onDone,
  onClose,
}: {
  projectKey: string
  column: BoardColumn | null
  canDelete: boolean
  onDone: () => Promise<void>
  onClose: () => void
}) {
  const [form, setForm] = useState<ColumnInput>({
    name: column?.name ?? '',
    description: column?.description ?? '',
  })

  const save = useMutation({
    mutationFn: (input: ColumnInput) =>
      column ? api.updateColumn(column.id, input) : api.createColumn(projectKey, input),
    onSuccess: async () => {
      await onDone()
      onClose()
    },
  })

  const remove = useMutation({
    mutationFn: () => api.deleteColumn(column?.id ?? ''),
    onSuccess: async () => {
      await onDone()
      onClose()
    },
  })

  const complete = form.name.trim() && form.description.trim()
  const error = save.error ?? remove.error

  return (
    <Modal
      title={column ? 'Edit column' : 'New column'}
      onClose={onClose}
      footer={
        <>
          {column ? (
            <Button
              variant="ghost"
              danger
              className={styles.spacer}
              disabled={remove.isPending}
              onClick={() => remove.mutate()}
            >
              Delete column
            </Button>
          ) : null}
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="go"
            disabled={save.isPending || !complete}
            onClick={() => save.mutate(form)}
          >
            {column ? 'Save' : 'Add column'}
          </Button>
        </>
      }
    >
      <ModalBody>
        {error ? <ErrorBanner>{error.message}</ErrorBanner> : null}
        {column && !canDelete ? (
          <p className={styles.note}>A board keeps at least two columns, so this one has to stay.</p>
        ) : null}
        <Field label="Name" required>
          <input
            value={form.name}
            maxLength={80}
            onChange={(event) => setForm({ ...form, name: event.target.value })}
            placeholder="Review"
          />
        </Field>
        <Field label="Description" required hint="What belongs here, so it does not drift.">
          <textarea
            value={form.description}
            onChange={(event) => setForm({ ...form, description: event.target.value })}
            placeholder="Waiting on PR review or QA"
          />
        </Field>
      </ModalBody>
    </Modal>
  )
}

/**
 * Parse a plain `YYYY-MM-DD`.
 *
 * `new Date(iso)` would read it as UTC midnight, which shows as the previous
 * day anywhere west of Greenwich — and a due date that is off by one is worse
 * than no due date at all.
 */
function localDate(iso: string): Date {
  const [year = 1970, month = 1, day = 1] = iso.split('-').map(Number)
  return new Date(year, month - 1, day)
}

function formatDue(iso: string): string {
  return localDate(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}

function isOverdue(iso: string): boolean {
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return localDate(iso) < today
}
