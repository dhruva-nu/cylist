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
 *
 * Dragging is not only a pointer gesture. A focused card is picked up with
 * space and walked between columns with the arrow keys, which is the same
 * operation through the same code path — see `DRAG_KEYS` and `moveByColumn`.
 *
 * Any column folds down to a rail, and which ones are folded is remembered per
 * project. A folded column is still a drop target: "Done" is exactly the
 * column you stop looking at and keep moving cards into, and a board that made
 * you unfold it first would be asking you to undo the tidying to use it.
 */

import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type Announcements,
  type ClientRect,
  type DragEndEvent,
  type KeyboardCodes,
  type KeyboardCoordinateGetter,
  type ScreenReaderInstructions,
  type UniqueIdentifier,
} from '@dnd-kit/core'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams } from '@tanstack/react-router'
import { useCallback, useEffect, useState } from 'react'
import { api, type Board, type BoardColumn, type ColumnInput, type Task } from '../api/client'
import { Field, Modal, ModalBody } from '../components/Modal'
import { PageHead } from '../components/Shell'
import { TaskDialog } from '../components/TaskDialog'
import { Avatar, Button, EmptyState, ErrorBanner, LiveRegion, useAnnouncer } from '../components/ui'
import styles from './ProjectBoard.module.css'

/**
 * Which keys drive a keyboard drag.
 *
 * Enter is deliberately not among them, though dnd-kit offers it by default:
 * Enter is what opens a card, and a card that cannot be opened from the
 * keyboard is a worse trade than one that cannot be dragged. Space does both
 * ends of the drag instead.
 *
 * Tab cancels rather than finishes. dnd-kit's default is to drop on Tab, which
 * means leaving the board mid-drag quietly commits a move nobody asked for;
 * cancelling still rules out the other failure, a drag left running after
 * focus has gone somewhere else.
 */
const DRAG_KEYS: KeyboardCodes = {
  start: ['Space'],
  cancel: ['Escape', 'Tab'],
  end: ['Space'],
}

const SCREEN_READER_INSTRUCTIONS: ScreenReaderInstructions = {
  draggable:
    'Press enter to open this card. Press space to pick it up, then use the left and right ' +
    'arrow keys to move it between columns. Press space again to drop it, or escape to leave ' +
    'it where it was.',
}

/** The column nearest a point, for when nothing is being hovered yet. */
function nearestColumn(columns: [UniqueIdentifier, ClientRect][], centre: number): number {
  let best = 0
  let bestDistance = Number.POSITIVE_INFINITY

  columns.forEach(([, rect], index) => {
    const distance = Math.abs(rect.left + rect.width / 2 - centre)
    if (distance < bestDistance) {
      bestDistance = distance
      best = index
    }
  })

  return best
}

/**
 * Move a picked-up card one whole column per key press.
 *
 * dnd-kit's stock getter nudges 25 pixels an arrow, which is a dozen presses
 * to cross a 300-pixel column and leaves the card wherever the twelfth one
 * happened to land. A board has one axis worth travelling and a small number
 * of places to stop on it, so left and right jump to the next column's centre
 * and up and down do nothing at all.
 */
const moveByColumn: KeyboardCoordinateGetter = (event, { context, currentCoordinates }) => {
  const step = event.code === 'ArrowRight' ? 1 : event.code === 'ArrowLeft' ? -1 : 0
  if (step === 0) return

  const { collisionRect, droppableContainers, droppableRects, over } = context
  if (!collisionRect) return

  const columns = [...droppableRects]
    .filter(([id]) => droppableContainers.get(id)?.disabled !== true)
    .sort(([, left], [, right]) => left.left - right.left)
  if (columns.length === 0) return

  const hovered = columns.findIndex(([id]) => id === over?.id)
  const from =
    hovered >= 0 ? hovered : nearestColumn(columns, collisionRect.left + collisionRect.width / 2)
  const to = Math.min(columns.length - 1, Math.max(0, from + step))
  if (to === from) return

  const target = columns[to]
  if (!target) return

  const [, rect] = target
  // Centred on the target, so the collision detection below has an unambiguous
  // winner however wide the card happens to be.
  return { x: rect.left + (rect.width - collisionRect.width) / 2, y: currentCoordinates.y }
}

/**
 * Which columns are folded, remembered per project.
 *
 * Per project rather than per board-wide: folding "Done" away on one project
 * says nothing about what you want to see on another. localStorage is wrapped
 * because reading it throws outright in a private window, and a board that
 * will not render is a worse outcome than one that forgets a preference.
 */
function collapsedKey(projectKey: string): string {
  return `cylist.board.collapsed.${projectKey}`
}

function readCollapsed(projectKey: string): string[] {
  try {
    const stored: unknown = JSON.parse(window.localStorage.getItem(collapsedKey(projectKey)) ?? '')
    return Array.isArray(stored) ? stored.filter((id): id is string => typeof id === 'string') : []
  } catch {
    return []
  }
}

function useCollapsedColumns(projectKey: string) {
  const [collapsed, setCollapsed] = useState<string[]>(() => readCollapsed(projectKey))

  // Keyed on the project, so walking from one board to another picks up that
  // board's folds instead of carrying the last one's across.
  useEffect(() => setCollapsed(readCollapsed(projectKey)), [projectKey])

  useEffect(() => {
    try {
      window.localStorage.setItem(collapsedKey(projectKey), JSON.stringify(collapsed))
    } catch {
      // The fold still holds for this visit; it just will not be remembered.
    }
  }, [projectKey, collapsed])

  const toggle = useCallback((columnId: string) => {
    setCollapsed((current) =>
      current.includes(columnId) ? current.filter((id) => id !== columnId) : [...current, columnId],
    )
  }, [])

  return { collapsed, toggle }
}

export function ProjectBoard() {
  const { projectKey } = useParams({ from: '/p/$projectKey/board' })
  const queryClient = useQueryClient()

  const [openTaskId, setOpenTaskId] = useState<string | null>(null)
  const [creatingTask, setCreatingTask] = useState(false)
  /** The parent a new sub-task is being written under, if one is. */
  const [splitting, setSplitting] = useState<string | null>(null)
  const [columnDialog, setColumnDialog] = useState<BoardColumn | 'new' | null>(null)
  const { collapsed, toggle } = useCollapsedColumns(projectKey)
  const { message, announce } = useAnnouncer()

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
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { keyboardCodes: DRAG_KEYS, coordinateGetter: moveByColumn }),
  )

  if (board.isPending || tasks.isPending) return <EmptyState>Loading the board…</EmptyState>
  if (board.error) return <ErrorBanner>{board.error.message}</ErrorBanner>
  if (tasks.error) return <ErrorBanner>{tasks.error.message}</ErrorBanner>

  const columns = board.data.columns
  const firstColumn = columns[0]
  const byColumn = new Map(columns.map((column) => [column.id, [] as Task[]]))
  for (const task of tasks.data) byColumn.get(task.column_id)?.push(task)

  const nameOfTask = (id: UniqueIdentifier) =>
    tasks.data?.find((task) => task.id === id)?.title ?? 'the card'
  const nameOfColumn = (id: UniqueIdentifier | undefined) =>
    columns.find((column) => column.id === id)?.name ?? 'nowhere'

  // Spoken by dnd-kit's own live region, so a keyboard drag is followed rather
  // than merely performed.
  const announcements: Announcements = {
    onDragStart: ({ active }) =>
      `Picked up ${nameOfTask(active.id)}. Use the left and right arrow keys to choose a column.`,
    onDragOver: ({ active, over }) =>
      over ? `${nameOfTask(active.id)} is over ${nameOfColumn(over.id)}.` : undefined,
    onDragEnd: ({ active, over }) =>
      over
        ? `Dropped ${nameOfTask(active.id)} into ${nameOfColumn(over.id)}.`
        : `${nameOfTask(active.id)} was left where it was.`,
    onDragCancel: ({ active }) => `Cancelled. ${nameOfTask(active.id)} is back where it was.`,
  }

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
        hold or blocked. Drag a card, or focus one and press space to move it with the arrow keys.
      </PageHead>

      {move.error ? <ErrorBanner>{move.error.message}</ErrorBanner> : null}
      <LiveRegion message={message} />

      <DndContext
        sensors={sensors}
        // Columns are the only drop targets and they never overlap, so nearest
        // centre is both the obvious answer and the one a keyboard drag — which
        // lands the card dead centre — can rely on.
        collisionDetection={closestCenter}
        accessibility={{ announcements, screenReaderInstructions: SCREEN_READER_INSTRUCTIONS }}
        onDragEnd={onDragEnd}
      >
        <div className={styles.board}>
          {columns.map((column) => (
            <Column
              key={column.id}
              column={column}
              tasks={byColumn.get(column.id) ?? []}
              isFirst={column.id === firstColumn?.id}
              collapsed={collapsed.includes(column.id)}
              onToggleCollapse={() => {
                toggle(column.id)
                announce(
                  collapsed.includes(column.id)
                    ? `${column.name} expanded.`
                    : `${column.name} collapsed.`,
                )
              }}
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
          announce={announce}
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
          announce={announce}
          onDone={refresh}
          onClose={() => setCreatingTask(false)}
        />
      ) : null}

      {/* One dialog at a time, never stacked: the sub-task's card needs every
          field a card needs, and a form drawn on top of the form it came from
          is two Save buttons with no way to tell which one is which. */}
      {splitting && firstColumn ? (
        <TaskDialog
          projectKey={projectKey}
          taskId={null}
          parentRef={splitting}
          columns={columns}
          firstColumn={firstColumn}
          announce={announce}
          onDone={refresh}
          onClose={() => setSplitting(null)}
        />
      ) : null}

      {openTaskId && firstColumn ? (
        <TaskDialog
          projectKey={projectKey}
          taskId={openTaskId}
          columns={columns}
          firstColumn={firstColumn}
          announce={announce}
          onOpenTask={setOpenTaskId}
          onSplit={(parentRef) => {
            setOpenTaskId(null)
            setSplitting(parentRef)
          }}
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
  collapsed,
  onToggleCollapse,
  onOpenTask,
  onAddTask,
  onEdit,
}: {
  column: BoardColumn
  tasks: Task[]
  isFirst: boolean
  collapsed: boolean
  onToggleCollapse: () => void
  onOpenTask: (taskId: string) => void
  onAddTask: () => void
  onEdit: () => void
}) {
  const { setNodeRef, isOver } = useDroppable({ id: column.id })
  const counted = `${tasks.length} ${tasks.length === 1 ? 'card' : 'cards'}`

  if (collapsed) {
    return (
      <section
        ref={setNodeRef}
        className={`${styles.rail} ${isOver ? styles.over : ''}`}
        aria-label={`${column.name}, ${counted}, collapsed`}
      >
        <button
          type="button"
          className={styles.railBody}
          aria-expanded={false}
          aria-label={`Expand ${column.name}`}
          onClick={onToggleCollapse}
        >
          <span aria-hidden="true">›</span>
          <span className={styles.count}>{tasks.length}</span>
          <span className={styles.railName}>{column.name}</span>
        </button>
        {/* The first column is where new work lands, so its "+" survives the
            fold: otherwise tidying the board away takes the add button with
            it. */}
        {isFirst ? (
          <button className={styles.railAdd} onClick={onAddTask} aria-label="Add a task">
            +
          </button>
        ) : null}
      </section>
    )
  }

  return (
    <section
      ref={setNodeRef}
      className={`${styles.column} ${isOver ? styles.over : ''}`}
      aria-label={`${column.name}, ${counted}`}
    >
      <div className={styles.head}>
        <div className={styles.headRow}>
          <h3>{column.name}</h3>
          <span className={styles.headActions}>
            <span className={styles.count}>{tasks.length}</span>
            <Button
              variant="ghost"
              small
              aria-expanded
              aria-label={`Collapse ${column.name}`}
              onClick={onToggleCollapse}
            >
              ‹
            </Button>
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

const STATUS_LABELS = {
  active: 'Active',
  hold: 'On hold',
  blocked: 'Blocked',
  cancelled: 'Cancelled',
} as const

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
        // Enter belongs to the card, every other key to the drag sensor —
        // whose own onKeyDown this handler has just replaced, so it has to be
        // called on rather than dropped. Not while the card is in the air,
        // though: opening a dialog over a drag in progress strands the drag.
        if (event.key === 'Enter' && !isDragging) {
          event.preventDefault()
          onOpen()
          return
        }
        listeners?.onKeyDown?.(event)
      }}
      aria-label={
        task.parent_reference
          ? `${task.reference}: ${task.title}, a sub-task of ${task.parent_reference}`
          : `${task.reference}: ${task.title}`
      }
      style={transform ? { transform: `translate3d(${transform.x}px, ${transform.y}px, 0)` } : {}}
      className={[styles.task, styles[task.status], isDragging && styles.dragging]
        .filter(Boolean)
        .join(' ')}
    >
      <div className={styles.taskRow}>
        <span className={styles.refs}>
          <span className={styles.reference}>{task.reference}</span>
          {/* A sub-task's own reference already carries its parent's number,
              but `ATL-41-2` only says so to a reader who knows the scheme —
              and on a board, where the two cards may be columns apart, the
              parent is the thing you need to recognise the card at all. */}
          {task.parent_reference ? (
            <span className={styles.parent} title={`Sub-task of ${task.parent_reference}`}>
              of {task.parent_reference}
            </span>
          ) : null}
        </span>
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
          {/* The one number on a card that can stop it moving: while it is
              above zero the server refuses the last column. */}
          {task.open_subtask_count ? (
            <span className={styles.open} title={`${task.open_subtask_count} sub-tasks still open`}>
              ☑ {task.open_subtask_count}
            </span>
          ) : null}
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
    <button
      className={styles.addColumn}
      // aria-disabled rather than disabled: the tile is where the limit is
      // explained, and a disabled button cannot be focused to read it.
      aria-disabled={full}
      onClick={() => {
        if (!full) onClick()
      }}
    >
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
  announce,
  onDone,
  onClose,
}: {
  projectKey: string
  column: BoardColumn | null
  canDelete: boolean
  announce: (message: string) => void
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
    onSuccess: async (saved) => {
      announce(column ? `Column renamed to ${saved.name}.` : `Column ${saved.name} added.`)
      await onDone()
      onClose()
    },
  })

  const remove = useMutation({
    mutationFn: () => api.deleteColumn(column?.id ?? ''),
    onSuccess: async () => {
      announce(`Column ${column?.name ?? ''} deleted.`)
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
          <p className={styles.note}>
            A board keeps at least two columns, so this one has to stay.
          </p>
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
