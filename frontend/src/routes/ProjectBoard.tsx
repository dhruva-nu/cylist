/**
 * The Kanban board.
 *
 * Two rules from the API are visible in the UI rather than only enforced by
 * it: "+ Add a task" appears under the first column alone, because that is
 * where new work lands; and "+ Column" in the toolbar counts down to eight and
 * then goes grey, because that is where a board stops being readable.
 *
 * Every column is a fixed height, whatever it is holding. A column that grew
 * with its cards meant dropping one moved every other column on the row out
 * from under the pointer — the board rearranging itself is a worse cost than a
 * short column having some empty space in it. Width is the other way about:
 * it answers to how many columns are open, not to what is in them, so the
 * columns share the row out between them and fold one to give the rest more.
 *
 * Dragging updates the cache before the request goes out. A card that snaps
 * back is how you find out the move failed — waiting for a round trip to see a
 * card move makes the board feel broken even when it is working.
 *
 * The card under the pointer is drawn twice over: dimmed where it started, and
 * again in a `DragOverlay` that floats above the board. A card moved in place
 * instead would be dragged around inside a column that scrolls its own cards,
 * which clips it at the column's edge the moment it leaves — and grows that
 * column's scrollable area as it goes, so the list shifts under the pointer
 * that is dragging out of it.
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
  DragOverlay,
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
  type DragStartEvent,
  type KeyboardCodes,
  type KeyboardCoordinateGetter,
  type ScreenReaderInstructions,
  type UniqueIdentifier,
} from '@dnd-kit/core'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams } from '@tanstack/react-router'
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  api,
  type Board,
  type BoardColumn,
  type ColumnInput,
  type Person,
  type Task,
} from '../api/client'
import {
  activeToken,
  applySuggestion,
  filterTasks,
  parseQuery,
  suggestionsFor,
  tokenize,
  type Suggestion,
} from './boardSearch'
import { Field, Modal, ModalBody } from '../components/Modal'
import { PageHead } from '../components/Shell'
import { TaskDialog } from '../components/TaskDialog'
import {
  Avatar,
  Button,
  EmptyState,
  ErrorBanner,
  LiveRegion,
  PriorityIcon,
  SubStatusBar,
  TaskRef,
  TypeIcon,
  useAnnouncer,
} from '../components/ui'
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
  const [search, setSearch] = useState('')
  /** What is in the air, so the overlay knows what to draw. */
  const [dragging, setDragging] = useState<UniqueIdentifier | null>(null)
  const boardRef = useRef<HTMLDivElement>(null)
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
  // Needed for `who:` search matches and its autocomplete, not for rendering
  // the board itself — every card already carries its own assignee.
  const members = useQuery({
    queryKey: ['members', projectKey],
    queryFn: () => api.listMembers(projectKey),
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

  /**
   * Dragging the sub-status slider on a card. Optimistic, because the whole
   * point of the control is that a stage moves under the cursor rather than
   * after a round trip.
   */
  const moveSubStatus = useMutation<
    Task,
    Error,
    { taskId: string; index: number },
    { previous: Task[] | undefined }
  >({
    mutationFn: ({ taskId, index }) => api.setSubStatus(taskId, index),
    onMutate: async ({ taskId, index }) => {
      await queryClient.cancelQueries({ queryKey: tasksKey })
      const previous = queryClient.getQueryData<Task[]>(tasksKey)
      queryClient.setQueryData<Task[]>(tasksKey, (current) =>
        current?.map((task) => (task.id === taskId ? { ...task, sub_status_index: index } : task)),
      )
      return { previous }
    },
    onError: (_error, _move, context) => {
      queryClient.setQueryData(tasksKey, context?.previous)
    },
    onSettled: refresh,
  })

  const boardKey = ['board', projectKey]

  const reorderColumns = useMutation<Board, Error, string[], { previous: Board | undefined }>({
    mutationFn: (columnIds) => api.reorderColumns(projectKey, columnIds),
    onMutate: async (columnIds) => {
      await queryClient.cancelQueries({ queryKey: boardKey })
      const previous = queryClient.getQueryData<Board>(boardKey)
      queryClient.setQueryData<Board>(boardKey, (current) => {
        if (!current) return current
        const byId = new Map(current.columns.map((column) => [column.id, column]))
        const reordered = columnIds
          .map((id) => byId.get(id))
          .filter((column): column is BoardColumn => column !== undefined)
        return { ...current, columns: reordered }
      })
      return { previous }
    },
    onError: (_error, _columnIds, context) => {
      queryClient.setQueryData(boardKey, context?.previous)
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
  const memberList = members.data?.members ?? []

  // Client-side, over what's already fetched: the board holds every task in
  // memory regardless, and a search endpoint would be a second way to ask a
  // question this data already answers.
  const visibleTasks = filterTasks(tasks.data, parseQuery(tokenize(search)), columns, memberList)
  const byColumn = new Map(columns.map((column) => [column.id, [] as Task[]]))
  for (const task of visibleTasks) byColumn.get(task.column_id)?.push(task)

  // A column's own draggable id is prefixed to keep it out of the task id
  // namespace — the two are otherwise both plain UUIDs.
  const columnDragPrefix = 'column:'
  const isColumnDrag = (id: UniqueIdentifier) =>
    typeof id === 'string' && id.startsWith(columnDragPrefix)

  const nameOfDragged = (id: UniqueIdentifier) =>
    isColumnDrag(id)
      ? nameOfColumnId(String(id).slice(columnDragPrefix.length))
      : (tasks.data?.find((task) => task.id === id)?.title ?? 'the card')
  const nameOfColumnId = (id: string) =>
    columns.find((column) => column.id === id)?.name ?? 'the column'
  const nameOfColumn = (id: UniqueIdentifier | undefined) =>
    typeof id === 'string' ? nameOfColumnId(id) : 'nowhere'

  // Spoken by dnd-kit's own live region, so a keyboard drag is followed rather
  // than merely performed. Shared between task cards and column handles: both
  // drag over the same set of column drop targets, so one set of wording
  // covers either, naming whichever is actually being moved.
  const announcements: Announcements = {
    onDragStart: ({ active }) =>
      `Picked up ${nameOfDragged(active.id)}. Use the left and right arrow keys to choose a column.`,
    onDragOver: ({ active, over }) =>
      over ? `${nameOfDragged(active.id)} is over ${nameOfColumn(over.id)}.` : undefined,
    onDragEnd: ({ active, over }) =>
      over
        ? isColumnDrag(active.id)
          ? `${nameOfDragged(active.id)} moved next to ${nameOfColumn(over.id)}.`
          : `Dropped ${nameOfDragged(active.id)} into ${nameOfColumn(over.id)}.`
        : `${nameOfDragged(active.id)} was left where it was.`,
    onDragCancel: ({ active }) => `Cancelled. ${nameOfDragged(active.id)} is back where it was.`,
  }

  function moveColumnTo(fromIndex: number, toIndex: number) {
    if (fromIndex < 0 || toIndex < 0 || toIndex >= columns.length || fromIndex === toIndex) return
    const reordered = [...columns]
    const [moved] = reordered.splice(fromIndex, 1)
    if (!moved) return
    reordered.splice(toIndex, 0, moved)
    reorderColumns.mutate(reordered.map((column) => column.id))
    announce(`${moved.name} moved ${toIndex < fromIndex ? 'left' : 'right'}.`)
  }

  // Only a card gets an overlay. A column is dragged where it stands: it is
  // not inside anything that scrolls or clips, so it has no need of one.
  const draggedTask =
    dragging !== null && !isColumnDrag(dragging)
      ? (tasks.data?.find((task) => task.id === dragging) ?? null)
      : null

  function onDragStart(event: DragStartEvent) {
    setDragging(event.active.id)
  }

  function onDragEnd(event: DragEndEvent) {
    // Cleared here rather than in each branch below: every one of them ends
    // the drag, and an overlay left on screen is a card stuck to the pointer.
    setDragging(null)

    const overId = event.over?.id
    if (typeof overId !== 'string') return

    if (isColumnDrag(event.active.id)) {
      const columnId = String(event.active.id).slice(columnDragPrefix.length)
      moveColumnTo(
        columns.findIndex((column) => column.id === columnId),
        columns.findIndex((column) => column.id === overId),
      )
      return
    }

    const task = tasks.data?.find((candidate) => candidate.id === event.active.id)
    if (!task || task.column_id === overId) return

    move.mutate({ taskId: task.id, columnId: overId, position: byColumn.get(overId)?.length ?? 0 })
  }

  return (
    <>
      <PageHead title="Kanban board">
        Cards enter at the first column and move wherever the work does. Colour flags anything on
        hold or blocked. Drag a card, or focus one and press space to move it with the arrow keys. A
        column moves the same way, by its ⠿ handle.
      </PageHead>

      {move.error ? <ErrorBanner>{move.error.message}</ErrorBanner> : null}
      <LiveRegion message={message} />

      <div className={styles.toolbar}>
        <SearchBar query={search} onChange={setSearch} columns={columns} members={memberList} />
        <AddColumnButton board={board.data} onClick={() => setColumnDialog('new')} />
      </div>

      <DndContext
        sensors={sensors}
        // Columns are the only drop targets and they never overlap, so nearest
        // centre is both the obvious answer and the one a keyboard drag — which
        // lands the card dead centre — can rely on.
        collisionDetection={closestCenter}
        accessibility={{ announcements, screenReaderInstructions: SCREEN_READER_INSTRUCTIONS }}
        // The board scrolls sideways to reach a column that is off the edge,
        // and nothing else scrolls at all. Left to itself dnd-kit picks the
        // nearest scrollable ancestor, which is the card list you are dragging
        // out of: the column then scrolls its own cards away under the pointer
        // for as long as you hold one near its top or bottom.
        autoScroll={{ canScroll: (element) => element === boardRef.current }}
        onDragStart={onDragStart}
        onDragEnd={onDragEnd}
        onDragCancel={() => setDragging(null)}
      >
        <div ref={boardRef} className={styles.board}>
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
              onMoveSubStatus={(taskId, index) => moveSubStatus.mutate({ taskId, index })}
              onAddTask={() => setCreatingTask(true)}
              onEdit={() => setColumnDialog(column)}
            />
          ))}
        </div>

        {/* Outside the board, so the board's own overflow has nothing to say
            about where the card in the air is allowed to be drawn. */}
        <DragOverlay dropAnimation={null}>
          {draggedTask ? (
            <article
              // Filtered like the card in the column, and for the same reason:
              // an active task has no status class, and a template string would
              // put the word "undefined" in the class list instead of nothing.
              className={[styles.task, styles[draggedTask.status], styles.lifted]
                .filter(Boolean)
                .join(' ')}
            >
              <TaskCardBody task={draggedTask} />
            </article>
          ) : null}
        </DragOverlay>
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

/**
 * The board's search box: free text plus `col:`, `who:`, `blk:` and `hld:`
 * tags (see `boardSearch.ts`). Typing `col:` or `who:` opens a suggestion
 * list of the matching columns or people — arrow keys to move through it,
 * enter or a click to accept, escape to dismiss it without losing the token
 * being typed.
 */
function SearchBar({
  query,
  onChange,
  columns,
  members,
}: {
  query: string
  onChange: (query: string) => void
  columns: BoardColumn[]
  members: Person[]
}) {
  const [highlighted, setHighlighted] = useState(0)
  const [dismissed, setDismissed] = useState(false)
  const suggestions = dismissed ? [] : suggestionsFor(activeToken(query), columns, members)

  function pick(suggestion: Suggestion) {
    onChange(applySuggestion(query, suggestion))
    setHighlighted(0)
  }

  return (
    <div className={styles.search}>
      <input
        type="text"
        value={query}
        onChange={(event) => {
          onChange(event.target.value)
          setDismissed(false)
          setHighlighted(0)
        }}
        onKeyDown={(event) => {
          if (!suggestions.length) return
          if (event.key === 'ArrowDown') {
            event.preventDefault()
            setHighlighted((current) => (current + 1) % suggestions.length)
          } else if (event.key === 'ArrowUp') {
            event.preventDefault()
            setHighlighted((current) => (current - 1 + suggestions.length) % suggestions.length)
          } else if (event.key === 'Enter') {
            const choice = suggestions[highlighted]
            if (choice) {
              event.preventDefault()
              pick(choice)
            }
          } else if (event.key === 'Escape') {
            setDismissed(true)
          }
        }}
        placeholder='Search, or tag it: col:"In progress"  who:Aditi  blk:  hld:'
        aria-label="Search tasks"
        aria-autocomplete="list"
        aria-expanded={suggestions.length > 0}
      />
      {suggestions.length ? (
        <ul className={styles.suggestions} role="listbox">
          {suggestions.map((suggestion, index) => (
            <li key={`${suggestion.kind}:${suggestion.value}`} role="presentation">
              <button
                type="button"
                role="option"
                aria-selected={index === highlighted}
                className={index === highlighted ? styles.suggestionActive : ''}
                // mousedown, not click: click fires after the input's blur, by
                // which point the list has already unmounted for having lost
                // focus, and the pick never happens.
                onMouseDown={(event) => {
                  event.preventDefault()
                  pick(suggestion)
                }}
              >
                <span className={styles.suggestionKind}>
                  {suggestion.kind === 'column' ? 'Column' : 'Assignee'}
                </span>
                {suggestion.value}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
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
  onMoveSubStatus,
  onAddTask,
  onEdit,
}: {
  column: BoardColumn
  tasks: Task[]
  isFirst: boolean
  collapsed: boolean
  onToggleCollapse: () => void
  onOpenTask: (taskId: string) => void
  onMoveSubStatus: (taskId: string, index: number) => void
  onAddTask: () => void
  onEdit: () => void
}) {
  const { setNodeRef: setDropRef, isOver } = useDroppable({ id: column.id })
  // A column is draggable on the whole card (so it visually moves as one
  // piece) but only the handle carries the listeners — otherwise every
  // button in the header would start a drag instead of doing its own job.
  const {
    attributes,
    listeners,
    setNodeRef: setDragRef,
    transform,
    isDragging,
  } = useDraggable({ id: `column:${column.id}` })
  const counted = `${tasks.length} ${tasks.length === 1 ? 'card' : 'cards'}`

  const setRefs = (node: HTMLElement | null) => {
    setDropRef(node)
    setDragRef(node)
  }
  const dragStyle = transform
    ? { transform: `translate3d(${transform.x}px, ${transform.y}px, 0)` }
    : undefined

  if (collapsed) {
    return (
      <section
        ref={setDropRef}
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
      ref={setRefs}
      style={dragStyle}
      className={[styles.column, isOver && styles.over, isDragging && styles.columnDragging]
        .filter(Boolean)
        .join(' ')}
      aria-label={`${column.name}, ${counted}`}
    >
      <div className={styles.head}>
        <div className={styles.headRow}>
          <button
            type="button"
            className={styles.dragHandle}
            {...attributes}
            {...listeners}
            aria-label={`Reorder ${column.name}. Press space to pick up, then the left and right arrow keys to move it.`}
            title="Drag to reorder"
          >
            ⠿
          </button>
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
          <TaskCard
            key={task.id}
            task={task}
            onOpen={() => onOpenTask(task.id)}
            onMoveSubStatus={(index) => onMoveSubStatus(task.id, index)}
          />
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

const TYPE_LABELS = {
  feature: 'Feature',
  bug: 'Bug',
  chore: 'Chore',
} as const

const PRIORITY_LABELS = {
  urgent: 'Urgent',
  asap: 'ASAP',
  week: 'This week',
  someday: 'Someday',
} as const

function TaskCard({
  task,
  onOpen,
  onMoveSubStatus,
}: {
  task: Task
  onOpen: () => void
  onMoveSubStatus: (index: number) => void
}) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({ id: task.id })

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
      // No transform of its own: while this card is in the air the DragOverlay
      // is the copy that follows the pointer, and this one stays put and dims.
      className={[styles.task, styles[task.status], isDragging && styles.dragging]
        .filter(Boolean)
        .join(' ')}
    >
      <TaskCardBody task={task} onMoveSubStatus={onMoveSubStatus} />
    </article>
  )
}

/**
 * What a card says, without any of what a card does.
 *
 * Drawn twice while a card is being dragged — dimmed in the column it came
 * from, and solid in the overlay — so it lives apart from the drag wiring that
 * only the one in the column has.
 *
 * `onMoveSubStatus` is optional because the overlay's copy has nothing to
 * click: it is a picture of a card moving, and the stage it is on cannot be
 * advanced in mid-air.
 */
function TaskCardBody({
  task,
  onMoveSubStatus,
}: {
  task: Task
  onMoveSubStatus?: (index: number) => void
}) {
  const late = isOverdue(task.due_date)

  return (
    <>
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
        <span className={styles.badges}>
          {/* Icons rather than words, with the word each one stands for kept on
              the chip: as a tooltip, and as text only a screen reader reads.
              "This week · feature" spelled out was the widest thing on a row
              that repeats down every card in the column, and the least worth
              reading twice — but it is still what the chip means, so nothing
              that cannot see the icon loses it.

              Someday is the baseline every card starts on, so flagging it too
              would be noise on every single card — the same reasoning that
              keeps the status pill off an active task. */}
          {task.priority !== 'someday' ? (
            <span
              className={`${styles.chip} ${styles.icon} ${styles[`priority_${task.priority}`]}`}
              title={PRIORITY_LABELS[task.priority]}
            >
              <PriorityIcon priority={task.priority} />
              <span className="visually-hidden">{PRIORITY_LABELS[task.priority]}</span>
            </span>
          ) : null}
          {task.status === 'active' ? (
            <span
              className={`${styles.chip} ${styles.icon} ${styles[`type_${task.type}`]}`}
              title={TYPE_LABELS[task.type]}
            >
              <TypeIcon type={task.type} />
              <span className="visually-hidden">{TYPE_LABELS[task.type]}</span>
            </span>
          ) : (
            <span className={`${styles.pill} ${styles[`pill_${task.status}`]}`}>
              {STATUS_LABELS[task.status]}
            </span>
          )}
        </span>
      </div>

      <div className={styles.title}>{task.title}</div>

      {task.sub_statuses.length && onMoveSubStatus ? (
        <SubStatusBar
          labels={task.sub_statuses}
          index={task.sub_status_index ?? 0}
          onMove={onMoveSubStatus}
        />
      ) : null}

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
          {task.jira_ref ? <TaskRef kind="jira" value={task.jira_ref} /> : null}
          {task.pr_ref ? <TaskRef kind="pr" value={task.pr_ref} /> : null}
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
    </>
  )
}

/**
 * Adds a column, from the toolbar rather than from a tile on the row's end.
 *
 * A full-height dashed tile spent a column's worth of the board on a button
 * pressed once or twice in a board's life, and pushed the last real column off
 * the edge to do it. Greyed at the limit rather than removed, so the button is
 * still there to say why it will not open.
 */
function AddColumnButton({ board, onClick }: { board: Board; onClick: () => void }) {
  const full = board.columns.length >= board.max_columns

  return (
    <Button
      small
      className={styles.addColumn}
      // aria-disabled rather than disabled: this is where the limit is
      // explained, and a disabled button cannot be focused to read it. Greying
      // itself at that point is the Button's own — see `[aria-disabled]`.
      aria-disabled={full}
      title={
        full
          ? `Column limit reached — a board holds ${board.max_columns} columns.`
          : `${board.columns.length} of ${board.max_columns} columns used`
      }
      onClick={() => {
        if (!full) onClick()
      }}
    >
      + Column
      <span className={styles.hint}>
        {board.columns.length}/{board.max_columns}
      </span>
    </Button>
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
