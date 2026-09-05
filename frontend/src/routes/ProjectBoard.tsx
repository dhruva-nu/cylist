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
 * A card created from a template goes only where that template allows. While
 * such a card is in the air the columns it cannot be dropped in are greyed
 * and refuse the drop, so the rule is visible before the server has to state
 * it — see `forbiddenFor`. The rule itself lives on the server; this is only
 * the board saying out loud what it already knows.
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
  type TaskPriority,
  type TaskType,
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
import { TemplateDialog } from '../components/TemplateDialog'
import {
  AlertIcon,
  Avatar,
  Button,
  CalendarIcon,
  ClockIcon,
  CommentIcon,
  EmptyState,
  ErrorBanner,
  LiveRegion,
  PlusIcon,
  PriorityIcon,
  StatusIcon,
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
  const [templatesOpen, setTemplatesOpen] = useState(false)
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
  // Needed for `who:` search matches and its autocomplete, and for the `@`
  // tags in a stage label — not for the assignee on a card, which every card
  // already carries.
  const members = useQuery({
    queryKey: ['members', projectKey],
    queryFn: () => api.listMembers(projectKey),
  })
  // Needed twice over: the New task form offers them, and the board reads the
  // columns each one allows to know where a card being dragged may be dropped.
  const templates = useQuery({
    queryKey: ['templates', projectKey],
    queryFn: () => api.listTemplates(projectKey),
  })

  const tasksKey = ['tasks', projectKey]

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['board', projectKey] }),
      queryClient.invalidateQueries({ queryKey: tasksKey }),
      queryClient.invalidateQueries({ queryKey: ['task'] }), // every open card's timeline
      queryClient.invalidateQueries({ queryKey: ['project-summary', projectKey] }),
      queryClient.invalidateQueries({ queryKey: ['templates', projectKey] }),
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
  const templateList = templates.data ?? []

  /**
   * The columns a card may not be dropped in, by its template's own stages.
   *
   * Empty for a card with no template, and for one whose template has no
   * stages: silence is not a ban. Empty is also what an unloaded template
   * list gives, which is the right failure — the board waves the card through
   * and the server, which is where the rule actually lives, has the last word.
   */
  const forbiddenFor = (task: Task | null | undefined): Set<string> => {
    const template = templateList.find((candidate) => candidate.id === task?.template_id)
    if (!template || !template.allowed_column_ids.length) return new Set()
    return new Set(
      columns
        .filter((column) => !template.allowed_column_ids.includes(column.id))
        .map((column) => column.id),
    )
  }

  // Client-side, over what's already fetched: the board holds every task in
  // memory regardless, and a search endpoint would be a second way to ask a
  // question this data already answers.
  const visibleTasks = filterTasks(tasks.data, parseQuery(tokenize(search)), columns, memberList)
  const byColumn = new Map(columns.map((column) => [column.id, [] as Task[]]))
  // The server sends top-level cards only, so nothing here is column-less; the
  // check is what makes that a statement rather than an assumption.
  for (const task of visibleTasks) {
    if (task.column_id !== null) byColumn.get(task.column_id)?.push(task)
  }

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
    onDragEnd: ({ active, over }) => {
      if (!over) return `${nameOfDragged(active.id)} was left where it was.`
      if (isColumnDrag(active.id)) {
        return `${nameOfDragged(active.id)} moved next to ${nameOfColumn(over.id)}.`
      }
      // A refused drop has to be announced as refused. dnd-kit's own wording
      // is "dropped into", which is the one thing that did not happen.
      const card = tasks.data?.find((candidate) => candidate.id === active.id)
      if (typeof over.id === 'string' && forbiddenFor(card).has(over.id)) {
        return (
          `${card?.template_name ?? 'This card'} cards do not go to ${nameOfColumn(over.id)}. ` +
          `${nameOfDragged(active.id)} is back where it was.`
        )
      }
      return `Dropped ${nameOfDragged(active.id)} into ${nameOfColumn(over.id)}.`
    },
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

  /** Greyed while a card is in the air, and empty the rest of the time. */
  const forbidden = forbiddenFor(draggedTask)

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

    // Refused here as well as by the server: a card that snaps back with an
    // error banner is how you find out afterwards, and the column already
    // greyed itself the moment the card was picked up. Said out loud by
    // `announcements.onDragEnd` rather than here — dnd-kit speaks on every
    // drop, and two live regions describing one gesture is one of them
    // talking over the other.
    if (forbiddenFor(task).has(overId)) return

    move.mutate({ taskId: task.id, columnId: overId, position: byColumn.get(overId)?.length ?? 0 })
  }

  return (
    <>
      {/* No standing paragraph of instructions. What it said, the board says
          better by being used: cards enter at the first column because that is
          the only one with a "+", and a card is dragged by dragging it. The
          keyboard's share of it is the part that is not self-evident, and that
          is spoken by SCREEN_READER_INSTRUCTIONS to the people it is for. */}
      <PageHead title="Kanban board" />

      {move.error ? <ErrorBanner>{move.error.message}</ErrorBanner> : null}
      <LiveRegion message={message} />

      <div className={styles.toolbar}>
        <SearchBar query={search} onChange={setSearch} columns={columns} members={memberList} />
        <Button small onClick={() => setTemplatesOpen(true)}>
          + Templates
          <span className={styles.hint}>{templateList.length}</span>
        </Button>
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
              projectKey={projectKey}
              tasks={byColumn.get(column.id) ?? []}
              isFirst={column.id === firstColumn?.id}
              collapsed={collapsed.includes(column.id)}
              forbidden={forbidden.has(column.id)}
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
              onCreated={refresh}
              onEdit={() => setColumnDialog(column)}
              members={memberList}
              announce={announce}
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
              <TaskCardBody task={draggedTask} members={memberList} />
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

      {templatesOpen ? (
        <TemplateDialog
          projectKey={projectKey}
          columns={columns}
          announce={announce}
          onDone={refresh}
          onClose={() => setTemplatesOpen(false)}
        />
      ) : null}

      {creatingTask && firstColumn ? (
        <TaskDialog
          projectKey={projectKey}
          taskId={null}
          columns={columns}
          firstColumn={firstColumn}
          templates={templateList}
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
          templates={templateList}
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
          templates={templateList}
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
  projectKey,
  tasks,
  isFirst,
  collapsed,
  forbidden,
  onToggleCollapse,
  onOpenTask,
  onMoveSubStatus,
  onAddTask,
  onCreated,
  onEdit,
  members,
  announce,
}: {
  column: BoardColumn
  projectKey: string
  tasks: Task[]
  isFirst: boolean
  collapsed: boolean
  /**
   * Whether the card currently in the air may not be dropped here — its
   * template's own stages rule this column out. False the rest of the time,
   * including while nothing is being dragged.
   */
  forbidden: boolean
  onToggleCollapse: () => void
  onOpenTask: (taskId: string) => void
  onMoveSubStatus: (taskId: string, index: number) => void
  /** The long way round: the whole form, for a column folded away to a rail. */
  onAddTask: () => void
  /** A card written in the composer has landed; re-read the board. */
  onCreated: () => Promise<void>
  onEdit: () => void
  /** The project's people, for the `@` tags in a stage label. */
  members: Person[]
  announce: (message: string) => void
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

  return (
    <section
      ref={setRefs}
      style={dragStyle}
      className={[
        styles.column,
        collapsed && styles.collapsed,
        // Not `over` when the drop would be refused: highlighting a column the
        // card cannot land in is the board promising something it will not do.
        isOver && !forbidden && styles.over,
        forbidden && styles.forbidden,
        isDragging && styles.columnDragging,
      ]
        .filter(Boolean)
        .join(' ')}
      aria-label={
        collapsed ? `${column.name}, ${counted}, collapsed` : `${column.name}, ${counted}`
      }
    >
      {/* Folded and unfolded are two fillings of one box, not two boxes. The
          box is what animates — it is the same element either way, so its
          width has somewhere to travel from — and the keys are what make the
          filling inside it a swap React remounts, so the fade runs each time
          rather than only on the first. */}
      {collapsed ? (
        <div key="rail" className={styles.rail}>
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
        </div>
      ) : (
        <div key="open" className={styles.unfolded}>
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
                members={members}
                onOpen={() => onOpenTask(task.id)}
                onMoveSubStatus={(index) => onMoveSubStatus(task.id, index)}
              />
            ))}
          </div>

          {isFirst ? (
            <TaskComposer
              projectKey={projectKey}
              members={members}
              announce={announce}
              onCreated={onCreated}
            />
          ) : (
            <div className={styles.foot} />
          )}
        </div>
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
  members,
  onOpen,
  onMoveSubStatus,
}: {
  task: Task
  members: Person[]
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
      <TaskCardBody task={task} members={members} onMoveSubStatus={onMoveSubStatus} />
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
  members,
  onMoveSubStatus,
}: {
  task: Task
  members: Person[]
  onMoveSubStatus?: (index: number) => void
}) {
  const active = task.status === 'active'
  /* What the tile at the head of the card is standing for. A card that is
     simply running wears its type there, which is the more useful of the two
     facts; one that is held, blocked or given up wears that instead, and the
     word itself stays on the pill across the row. */
  const tileLabel = active ? TYPE_LABELS[task.type] : STATUS_LABELS[task.status]

  return (
    <>
      <div className={styles.taskRow}>
        <span className={styles.refs}>
          {/* Icons rather than words, with the word each one stands for kept on
              the tile: as a tooltip, and as text only a screen reader reads.
              "This week · feature" spelled out was the widest thing on a row
              that repeats down every card in the column, and the least worth
              reading twice — but it is still what the mark means, so nothing
              that cannot see the icon loses it.

              A tinted tile rather than a bordered chip. The type is the one
              fact on the card that is the same shape on every card, so it is
              what the eye uses to find its place in a column — and an outline
              in the row's own ink was not enough to be found by. */}
          <span
            className={`${styles.tile} ${active ? styles[`tile_${task.type}`] : styles[`tile_${task.status}`]}`}
            title={tileLabel}
          >
            {active ? (
              <TypeIcon type={task.type} size={15} />
            ) : (
              <StatusIcon status={task.status} size={15} />
            )}
            <span className="visually-hidden">{tileLabel}</span>
          </span>
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
          {/* Someday is the baseline every card starts on, so flagging it too
              would be noise on every single card — the same reasoning that
              keeps the status pill off an active task.

              Only urgent gets a filled pill. The four levels escalate in
              chrome and never in width, so a column of cards keeps one margin
              down its right-hand side however its work is prioritised. */}
          {task.priority !== 'someday' ? (
            <span
              className={`${styles.prio} ${styles[`priority_${task.priority}`]}`}
              title={PRIORITY_LABELS[task.priority]}
            >
              <PriorityIcon priority={task.priority} />
              <span className="visually-hidden">{PRIORITY_LABELS[task.priority]}</span>
            </span>
          ) : null}
          {active ? null : (
            <span className={`${styles.pill} ${styles[`pill_${task.status}`]}`}>
              {STATUS_LABELS[task.status]}
            </span>
          )}
          {/* No date, no mark. A dash where a date goes reads as a date that
              failed to load; the absence of one says it plainly. */}
          <DueMark iso={task.due_date} />
        </span>
      </div>

      <div className={styles.title}>{task.title}</div>

      {task.sub_statuses.length && onMoveSubStatus ? (
        <SubStatusBar
          labels={task.sub_statuses}
          index={task.sub_status_index ?? 0}
          members={members}
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

      {/* The card's own small print, all of it on one row: how much has been
          said about the card, what it is tracked as elsewhere, and how far
          through its parts it is. Each is a drawn mark and a number, so the
          row reads as a set of counts rather than as a sentence. */}
      <div className={styles.taskRow}>
        <div className={styles.links}>
          {task.comment_count ? (
            <span className={styles.meta} title={`${task.comment_count} comments`}>
              <CommentIcon />
              <span className={styles.metaValue} aria-hidden="true">
                {task.comment_count}
              </span>
              <span className="visually-hidden">{task.comment_count} comments</span>
            </span>
          ) : null}
          {task.jira_ref ? <TaskRef kind="jira" value={task.jira_ref} /> : null}
          {task.pr_ref ? <TaskRef kind="pr" value={task.pr_ref} /> : null}
          {/* Sits in the row rather than above it. The stage bar and these
              dots are drawn as different things because they are different
              things — the bar is one journey with a position along it, the
              dots are a set of items with some of them ticked — but a set of
              counts is what this row already is, and given a line of its own
              the set read as a second bar. */}
          {task.subtask_count ? (
            <SubtaskDots total={task.subtask_count} open={task.open_subtask_count} />
          ) : null}
        </div>
        <span className={styles.trailing}>
          {/* Everyone who owes this card something, beside the person who owns
              it. The board stopped showing where a sub-task is when it stopped
              putting one in a column, so this is what it shows instead. */}
          {task.subtask_assignees.length ? (
            <span
              className={styles.owners}
              title={`Sub-tasks: ${task.subtask_assignees.map((person) => person.name).join(', ')}`}
            >
              {task.subtask_assignees.map((person) => (
                <Avatar key={person.id} name={person.name} colour={person.colour} small />
              ))}
            </span>
          ) : null}
          <Avatar name={task.assignee.name} colour={task.assignee.colour} />
        </span>
      </div>
    </>
  )
}

/** The three kinds, in the order the tiles are drawn. */
const TYPES: TaskType[] = ['feature', 'bug', 'chore']

const PRIORITIES: TaskPriority[] = ['urgent', 'asap', 'week', 'someday']

/**
 * Writing the next card, in the column it is going to land in.
 *
 * At rest this is the slot the card will fill: a dashed outline at the card's
 * own width and radius, sitting exactly where the card will sit. Opened, the
 * outline becomes the card — same width, same corners, on paper — and what you
 * type appears on the line the title will occupy. Nothing moves between the two
 * states except what is inside the box.
 *
 * A form on the board rather than the dialog, because the dialog is the wrong
 * size for the job. Adding a card is a sentence and four small decisions, and
 * every one of the four has a sensible default; a modal that covers the board
 * to ask for a title is a lot of ceremony for a line of text, and it hides the
 * column you are adding to while you decide. The long way round is still there
 * — the rail's `+` on a folded column opens the full form, which is where a
 * description, stages, a template and the tracking references live.
 *
 * It stays open after a card is added, cleared and focused. Cards arrive in
 * runs — a standup, a planning session — and closing after each one would make
 * the second card cost as much as the first.
 */
function TaskComposer({
  projectKey,
  members,
  announce,
  onCreated,
}: {
  projectKey: string
  members: Person[]
  announce: (message: string) => void
  onCreated: () => Promise<void>
}) {
  const [open, setOpen] = useState(false)
  const [title, setTitle] = useState('')
  const [type, setType] = useState<TaskType>('feature')
  const [priority, setPriority] = useState<TaskPriority>('someday')
  const [due, setDue] = useState('')
  const [assigneeId, setAssigneeId] = useState('')
  const titleField = useRef<HTMLInputElement>(null)
  const dateField = useRef<HTMLInputElement>(null)

  // Whoever is first in the directory until somebody says otherwise, which is
  // the same default the full form takes.
  const assignee = members.find((person) => person.id === assigneeId) ?? members[0] ?? null
  const ready = title.trim().length > 0 && assignee !== null

  useEffect(() => {
    if (open) titleField.current?.focus()
  }, [open])

  const create = useMutation({
    mutationFn: () =>
      api.createTask(projectKey, {
        title: title.trim(),
        description: '',
        type,
        priority,
        sub_statuses: [],
        due_date: due || null,
        assignee_id: assignee?.id ?? '',
        template_id: null,
        jira_ref: null,
        pr_ref: null,
      }),
    onSuccess: async (saved) => {
      announce(`${saved.reference} added.`)
      // The title alone. The four decisions beside it are usually the same for
      // the next card in a run — three bugs are three bugs — and re-picking
      // them each time is the cost the composer exists to remove.
      setTitle('')
      await onCreated()
      titleField.current?.focus()
    },
  })

  function close() {
    setOpen(false)
    setTitle('')
    create.reset()
  }

  if (!open) {
    return (
      <button type="button" className={styles.addTask} onClick={() => setOpen(true)}>
        <span className={styles.addTaskMark}>
          <PlusIcon />
        </span>
        Add a task
      </button>
    )
  }

  return (
    <form
      className={styles.composer}
      onSubmit={(event) => {
        event.preventDefault()
        if (ready && !create.isPending) create.mutate()
      }}
      // Escape closes, and stops there: the board's own key handling would
      // otherwise take it as cancelling a drag that is not happening.
      onKeyDown={(event) => {
        if (event.key !== 'Escape') return
        event.stopPropagation()
        close()
      }}
    >
      <input
        ref={titleField}
        className={styles.composerTitle}
        value={title}
        aria-label="Task title"
        placeholder="What needs doing?"
        onChange={(event) => setTitle(event.target.value)}
      />

      <div className={styles.composerRow}>
        {/* The same tiles the cards wear, doing the choosing instead of the
            reporting. Selected, a tile takes its type's tint; the other two
            stay grey, so the row says which kind this is at the same glance
            the column of cards above it is read with. */}
        <div className={styles.types} role="group" aria-label="Type">
          {TYPES.map((kind) => (
            <button
              key={kind}
              type="button"
              aria-pressed={type === kind}
              title={TYPE_LABELS[kind]}
              className={`${styles.tile} ${styles.typeToggle} ${
                type === kind ? styles[`tile_${kind}`] : styles.tileOff
              }`}
              onClick={() => setType(kind)}
            >
              <TypeIcon type={kind} size={15} />
              <span className="visually-hidden">{TYPE_LABELS[kind]}</span>
            </button>
          ))}
        </div>

        {/* Set, each chip takes the colour the card will wear — the urgent
            fill, the overdue pill — so the form is a preview of the card
            rather than a description of one. Unset, both are outlines. */}
        <label className={`${styles.chip} ${priorityChipStyle(priority)}`}>
          <PriorityIcon priority={priority} size={13} />
          <span className="visually-hidden">Priority</span>
          <select
            className={styles.chipControl}
            value={priority}
            onChange={(event) => setPriority(event.target.value as TaskPriority)}
          >
            {PRIORITIES.map((level) => (
              <option key={level} value={level}>
                {PRIORITY_LABELS[level]}
              </option>
            ))}
          </select>
        </label>

        <label
          className={`${styles.chip} ${dueChipStyle(due || null)}`}
          // The native indicator is hidden — there is already a calendar on
          // this chip — so the label is what opens the picker. Guarded because
          // a browser without it, or one that dislikes the gesture, must still
          // leave a typable date field behind.
          onClick={() => {
            try {
              dateField.current?.showPicker()
            } catch {
              dateField.current?.focus()
            }
          }}
        >
          {dueChipIcon(due || null)}
          <span className="visually-hidden">Due date</span>
          <input
            ref={dateField}
            type="date"
            className={`${styles.chipControl} ${styles.chipDate}`}
            value={due}
            onChange={(event) => setDue(event.target.value)}
          />
        </label>
      </div>

      {create.error ? <ErrorBanner>{create.error.message}</ErrorBanner> : null}

      <div className={styles.composerFoot}>
        <label className={styles.chip}>
          {assignee ? (
            <Avatar name={assignee.name} colour={assignee.colour} small />
          ) : (
            <PlusIcon size={13} />
          )}
          <span className="visually-hidden">Assignee</span>
          <select
            className={styles.chipControl}
            value={assignee?.id ?? ''}
            onChange={(event) => setAssigneeId(event.target.value)}
          >
            {members.length === 0 ? <option value="">Nobody on this project</option> : null}
            {members.map((person) => (
              <option key={person.id} value={person.id}>
                {person.name}
              </option>
            ))}
          </select>
        </label>

        <span className={styles.composerGo}>
          <button type="button" className={styles.composerCancel} onClick={close}>
            Cancel
          </button>
          <button
            type="submit"
            className={styles.composerAdd}
            disabled={!ready || create.isPending}
          >
            {create.isPending ? 'Adding…' : 'Add'}
          </button>
        </span>
      </div>
    </form>
  )
}

/** The priority chip's fill, in the four steps `.prio` escalates through. */
function priorityChipStyle(priority: TaskPriority): string {
  return (
    {
      urgent: styles.chipUrgent,
      asap: styles.chipAsap,
      week: styles.chipWeek,
      someday: '',
    }[priority] ?? ''
  )
}

/** The due chip's fill, in the same five steps a card's own date is drawn in. */
function dueChipStyle(iso: string | null): string {
  return (
    {
      late: styles.chipLate,
      today: styles.chipToday,
      tomorrow: styles.chipTomorrow,
      soon: styles.chipSoon,
      later: styles.chipLater,
      none: '',
    }[dueBucket(iso).kind] ?? ''
  )
}

/** And its mark: the alarm, the clock, or the plain calendar. */
function dueChipIcon(iso: string | null) {
  const { kind } = dueBucket(iso)
  if (kind === 'late') return <AlertIcon size={13} />
  if (kind === 'today') return <ClockIcon size={13} />
  return <CalendarIcon size={13} />
}

/**
 * How much of a card exists yet: one dot per sub-task, filled as each is done.
 *
 * When a sub-task was a card of its own you could see the split by looking at
 * the board — three cards in three columns. Nothing on the board says it any
 * more, so this does.
 *
 * Dots rather than a bar, and that is the whole design. A card may carry the
 * stage bar as well, and the two are not the same kind of fact: a bar is one
 * journey with a position along it, where the part behind you is finished and
 * the part ahead is not. Sub-tasks have no order and no position — they are a
 * set of things, ticked off in whatever order they get done — and drawn as a
 * second bar they read as the first one split in half. Countable marks say
 * "some of these" the way a filled track cannot.
 *
 * Not a control. Every other mark on a card moves when you click it; these
 * stand for work that is settled elsewhere, and the way to get at it is to open
 * the card, which is what clicking anywhere on them already does.
 */
function SubtaskDots({ total, open }: { total: number; open: number }) {
  const done = total - open
  const label = `${done} of ${total} sub-tasks finished`
  /* Six, then a number. Past about half a dozen the dots stop being countable
     at a glance — which is the whole reason they are dots — and a seventh
     line of them only pushes the row they share wider. The six drawn are the
     finished ones first, so the mark still says how far along the card is;
     what the number says is that there is more of this than a card can show,
     and the place to look at it is the card itself. */
  const shown = Math.min(total, 6)
  const rest = total - shown

  return (
    // The dots are decoration twice over — they are the label drawn — so the
    // sentence is what is read out and they are passed over in silence.
    <span className={styles.dots} title={label}>
      {Array.from({ length: shown }, (_, index) => (
        <span
          key={index}
          aria-hidden="true"
          className={`${styles.dot} ${index < done ? styles.dotDone : ''}`}
        />
      ))}
      {rest ? (
        <span className={styles.dotRest} aria-hidden="true">
          +{rest}
        </span>
      ) : null}
      <span className="visually-hidden">{label}</span>
    </span>
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

/** The same date said in full, for the tooltip on a mark that abbreviates it. */
function formatDueLong(iso: string): string {
  return localDate(iso).toLocaleDateString('en-GB', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  })
}

/**
 * How near a due date is, in the five steps a card draws differently.
 *
 * This was a boolean — overdue or not — and a boolean is the wrong shape for
 * the question. Every date that had not yet passed looked identical, so a card
 * wanted this afternoon sat in the column looking exactly like one wanted next
 * month, and the only moment the card ever changed was the moment it was too
 * late to matter. Five steps put the colour where the urgency is.
 *
 * `late` carries its own day count because "how far past" is the part anybody
 * acts on: a card one day over is a card to finish, and a card three weeks
 * over is a card to have a conversation about.
 */
type DueBucket =
  | { kind: 'late'; days: number }
  | { kind: 'today' }
  | { kind: 'tomorrow' }
  | { kind: 'soon' }
  | { kind: 'later' }
  | { kind: 'none' }

/** A card with no date is never late: there is no day it was wanted by. */
function dueBucket(iso: string | null): DueBucket {
  if (!iso) return { kind: 'none' }

  const today = new Date()
  today.setHours(0, 0, 0, 0)
  // Rounded, not truncated: the two midnights are an hour apart rather than a
  // whole number of days across a daylight-saving change, and a date that came
  // out at 6.96 days would otherwise be filed a step nearer than it is.
  const days = Math.round((localDate(iso).getTime() - today.getTime()) / 86_400_000)

  if (days < 0) return { kind: 'late', days: -days }
  if (days === 0) return { kind: 'today' }
  if (days === 1) return { kind: 'tomorrow' }
  if (days <= 7) return { kind: 'soon' }
  return { kind: 'later' }
}

/**
 * A due date on a card, drawn as near or as far as it is.
 *
 * The two ends of the ramp are the design: a date that has passed or is
 * passing today gets a filled pill and says what it means in words, because it
 * is asking for something; a date further out is the date itself in quieter
 * and quieter ink, because it is only telling you. Nothing between them
 * changes size, so a column of cards keeps one edge down its right-hand side.
 *
 * The visible text is abbreviated in four of the five states, so each mark
 * carries the whole sentence as well — spoken instead of the abbreviation, and
 * shown on hover.
 */
function DueMark({ iso }: { iso: string | null }) {
  const bucket = dueBucket(iso)
  if (iso === null || bucket.kind === 'none') return null

  const on = `due ${formatDueLong(iso)}`
  const { className, icon, text, said } = {
    late: {
      className: styles.dueLate,
      icon: <AlertIcon size={13} />,
      text: `${'days' in bucket ? bucket.days : 0}d late`,
      said: `${'days' in bucket ? bucket.days : 0} days late, ${on}`,
    },
    today: {
      className: styles.dueToday,
      icon: <ClockIcon size={13} />,
      text: 'Today',
      said: `Due today, ${formatDueLong(iso)}`,
    },
    tomorrow: {
      className: styles.dueTomorrow,
      icon: <CalendarIcon size={13} />,
      text: 'Tomorrow',
      said: `Due tomorrow, ${formatDueLong(iso)}`,
    },
    soon: {
      className: styles.dueSoon,
      icon: <CalendarIcon size={13} />,
      text: formatDue(iso),
      said: `Due ${formatDueLong(iso)}`,
    },
    later: {
      className: styles.dueLater,
      icon: <CalendarIcon size={13} />,
      text: formatDue(iso),
      said: `Due ${formatDueLong(iso)}`,
    },
  }[bucket.kind]

  return (
    <span className={`${styles.due} ${className}`} title={said}>
      {icon}
      <span className={styles.dueText} aria-hidden="true">
        {text}
      </span>
      <span className="visually-hidden">{said}</span>
    </span>
  )
}
