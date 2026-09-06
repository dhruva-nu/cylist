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
import { Link, useParams, useSearch } from '@tanstack/react-router'
import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react'
import {
  api,
  type Board,
  type BoardColumn,
  type ColumnInput,
  type Goal,
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
import { daysUntilDue, dueBucket, formatDue, formatDueLong } from '../components/dates'
import { GoalChip } from '../components/GoalMarks'
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
  PriorityIcon,
  SubStatusBar,
  TaskRef,
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
 * What is folded away, remembered per project.
 *
 * Two things fold on this board — a column, and, in lanes, a goal — and they
 * are the same list of ids under two storage keys. Per project rather than
 * board-wide: folding "Done" away on one project says nothing about what you
 * want to see on another. localStorage is wrapped because reading it throws
 * outright in a private window, and a board that will not render is a worse
 * outcome than one that forgets a preference.
 */
function readFolded(storageKey: string): string[] {
  try {
    const stored: unknown = JSON.parse(window.localStorage.getItem(storageKey) ?? '')
    return Array.isArray(stored) ? stored.filter((id): id is string => typeof id === 'string') : []
  } catch {
    return []
  }
}

function useFolded(storageKey: string) {
  const [folded, setFolded] = useState<string[]>(() => readFolded(storageKey))

  // Keyed on the storage key — which carries the project — so walking from one
  // board to another picks up that board's folds instead of carrying the last
  // one's across.
  useEffect(() => setFolded(readFolded(storageKey)), [storageKey])

  useEffect(() => {
    try {
      window.localStorage.setItem(storageKey, JSON.stringify(folded))
    } catch {
      // The fold still holds for this visit; it just will not be remembered.
    }
  }, [storageKey, folded])

  const toggle = useCallback((id: string) => {
    setFolded((current) =>
      current.includes(id) ? current.filter((one) => one !== id) : [...current, id],
    )
  }, [])

  return { folded, setFolded, toggle }
}

/**
 * Whether the board is split into one lane per goal, remembered per project.
 *
 * Kept the way the folded columns are, and for the same reason: how you look
 * at one board says nothing about how you want to look at another, and a
 * preference nobody can save is a smaller problem than a board that will not
 * render — see `readFolded`.
 */
function useLaneMode(projectKey: string) {
  const key = `cylist.board.lanes.${projectKey}`
  const read = useCallback(() => {
    try {
      return window.localStorage.getItem(`cylist.board.lanes.${projectKey}`) === 'true'
    } catch {
      return false
    }
  }, [projectKey])

  const [grouped, setGrouped] = useState<boolean>(read)

  useEffect(() => setGrouped(read()), [projectKey, read])

  useEffect(() => {
    try {
      window.localStorage.setItem(key, String(grouped))
    } catch {
      // The grouping still holds for this visit; it just is not remembered.
    }
  }, [key, grouped])

  return { grouped, setGrouped }
}

/**
 * The lane a card is in, as a string a drop target can be named after.
 *
 * A card on no goal is in a lane too — the one everything unclassified falls
 * into — so the absence needs a name of its own rather than a null that
 * cannot be part of an id.
 */
const NO_GOAL_LANE = 'none'

/**
 * The section of a divided column a drop target names.
 *
 * A drop target's id is its column, prefixed by whatever else narrows it: the
 * lane it is in, the section of the column it is. Every part but the last one
 * qualifies, and the column is always the last — which is what lets the two
 * qualifiers be read independently of each other and of how many there are.
 */
const OUTCOME_PREFIX = 'out:'

/** A drop target's column, whichever kind of target it is. */
function columnIdOf(over: string): string {
  return over.split('::').at(-1) ?? over
}

/** The outcome a drop target lands on, as an index, or null for a plain one. */
function outcomeIndexOf(over: string): number | null {
  const marked = over.split('::').find((part) => part.startsWith(OUTCOME_PREFIX))
  if (marked === undefined) return null
  const index = Number(marked.slice(OUTCOME_PREFIX.length))
  return Number.isInteger(index) ? index : null
}

/** The lane a drop target belongs to, or null when it is not in a lane. */
function laneKeyOf(over: string): string | null {
  if (!over.startsWith('lane:')) return null
  const separator = over.indexOf('::')
  return separator === -1 ? null : over.slice('lane:'.length, separator)
}

export function ProjectBoard() {
  const { projectKey } = useParams({ from: '/p/$projectKey/board' })
  const { q: initialQuery = '' } = useSearch({ from: '/p/$projectKey/board' })
  const queryClient = useQueryClient()

  const [openTaskId, setOpenTaskId] = useState<string | null>(null)
  const [creatingTask, setCreatingTask] = useState(false)
  /** The parent a new sub-task is being written under, if one is. */
  const [splitting, setSplitting] = useState<string | null>(null)
  const [columnDialog, setColumnDialog] = useState<BoardColumn | 'new' | null>(null)
  const [templatesOpen, setTemplatesOpen] = useState(false)
  // Seeded from `?q=`, which is how a goal's page hands the board its own
  // cards. Read once: after that the box is the reader's, and rewriting it
  // from the URL on every render would take the cursor with it.
  const [search, setSearch] = useState(() => initialQuery)
  /**
   * The quick filters: a goal and a status, picked from a dropdown rather than
   * typed. The search box already parses `goal:` and `blk:`/`hld:` tags, but a
   * tag has to be known to be typed — these are the same two questions asked
   * as a pair of selects, for whoever would rather point than type.
   */
  const [quickGoal, setQuickGoal] = useState<'all' | 'none' | (string & {})>('all')
  const [quickStatus, setQuickStatus] = useState<'all' | Task['status']>('all')
  /** The goal a card written from a lane starts on. */
  const [composingOn, setComposingOn] = useState<string | null>(null)
  /**
   * A drag that would take a finished card back onto the board, held until it
   * is confirmed.
   *
   * The one move worth asking about. Every other drag says where work has got
   * to; this one un-says it — the card stops being done, and its history
   * records that it was reopened. A card nudged out of the last column by a
   * slipped drop would rewrite that quietly, so the drop is held here and the
   * card stays where it was until somebody says yes.
   */
  const [reopening, setReopening] = useState<Reopening | null>(null)
  /** What is in the air, so the overlay knows what to draw. */
  const [dragging, setDragging] = useState<UniqueIdentifier | null>(null)
  const boardRef = useRef<HTMLDivElement>(null)
  const { folded: collapsed, toggle } = useFolded(`cylist.board.collapsed.${projectKey}`)
  /**
   * Which outcome sections are folded away, keyed by `${columnId}:${index}` —
   * an id built the same way a section's drop target is, so a fold survives
   * exactly what the section itself survives: a rename, another card landing
   * in it, anything short of the section no longer existing.
   */
  const { folded: collapsedOutcomes, toggle: toggleOutcome } = useFolded(
    `cylist.board.collapsedOutcomes.${projectKey}`,
  )
  /**
   * Which lanes are folded away, when the board is in lanes.
   *
   * The same fold a column has, turned ninety degrees: a board split by goal
   * is as tall as the sum of its goals, and the goal you are not working on
   * this week is a screen of scrolling between the two you are. A folded lane
   * keeps its heading — the name and the count are the part you still need —
   * and drops its row of cells.
   *
   * A folded lane is not a drop target, which is the one way it differs from a
   * folded column. A column keeps a rail you can drop onto because a card put
   * into "Done" is still a card put somewhere definite; a lane's cells are one
   * goal's share of every column at once, and there is no single place a card
   * dropped on a folded lane could honestly land.
   */
  const {
    folded: foldedLanes,
    setFolded: setFoldedLanes,
    toggle: toggleLane,
  } = useFolded(`cylist.board.lanes.folded.${projectKey}`)
  const { grouped, setGrouped } = useLaneMode(projectKey)
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
  // Needed for the `goal:` tag and its autocomplete, for the picker on the
  // card form, and for the lanes — which draw a lane per open goal whether or
  // not anything has been written under it yet, since an empty lane is where
  // the first card on a goal gets dropped.
  const goals = useQuery({
    queryKey: ['goals', projectKey],
    queryFn: () => api.listGoals(projectKey),
  })

  const tasksKey = ['tasks', projectKey]

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['board', projectKey] }),
      queryClient.invalidateQueries({ queryKey: tasksKey }),
      queryClient.invalidateQueries({ queryKey: ['task'] }), // every open card's timeline
      queryClient.invalidateQueries({ queryKey: ['project-summary', projectKey] }),
      queryClient.invalidateQueries({ queryKey: ['templates', projectKey] }),
      queryClient.invalidateQueries({ queryKey: ['goals', projectKey] }),
      queryClient.invalidateQueries({ queryKey: ['goal'] }),
    ])
  }

  /**
   * Where a dropped card lands: a column, and — when the board is in lanes —
   * the goal of the lane it was dropped into.
   *
   * One mutation rather than two because it is one gesture. Dragging a card
   * across into another goal's lane is how a card is put on a goal without
   * opening it, and a version of that which moved the card first and linked it
   * a moment later would show the reader two things happening where they did
   * one.
   */
  const move = useMutation<Task | null, Error, Move, { previous: Task[] | undefined }>({
    mutationFn: async ({ taskId, taskRef, columnId, position, outcome, goalId }) => {
      const moved = columnId ? await api.moveTask(taskId, columnId, position, outcome) : null
      // By reference rather than by id for the same reason the dialog does:
      // it is what the error message will name if the server refuses.
      return goalId === undefined ? moved : api.updateTask(taskRef, { goal_id: goalId })
    },
    onMutate: async ({ taskId, columnId, position, outcome, outcomeIndex, goalId }) => {
      await queryClient.cancelQueries({ queryKey: tasksKey })
      const previous = queryClient.getQueryData<Task[]>(tasksKey)
      const landed = goalId === undefined ? null : (goals.data ?? []).find((g) => g.id === goalId)
      queryClient.setQueryData<Task[]>(tasksKey, (current) =>
        current?.map((task) =>
          task.id === taskId
            ? {
                ...task,
                ...(columnId
                  ? { column_id: columnId, position, outcome, outcome_index: outcomeIndex }
                  : {}),
                ...(goalId === undefined
                  ? {}
                  : {
                      goal_id: goalId,
                      goal_name: landed?.name ?? null,
                      goal_colour: landed?.colour ?? null,
                      goal_reference: landed?.reference ?? null,
                    }),
              }
            : task,
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
    // The quick filters, ANDed on top of the search box the same way its own
    // tags are ANDed together — see `filterTasks`. Kept out of that function
    // rather than folded into `ParsedSearch`: these two already have their own
    // exact-match values (a goal's id, one of the four statuses) from a
    // dropdown, where `goal:` and `blk:`/`hld:` are typed and only ever
    // narrower questions — "on no goal" or "is blocked" — not this.
    .filter((task) => {
      if (quickGoal === 'all') return true
      return quickGoal === 'none' ? task.goal_id === null : task.goal_id === quickGoal
    })
    .filter((task) => quickStatus === 'all' || task.status === quickStatus)
  const byColumn = new Map(columns.map((column) => [column.id, [] as Task[]]))
  // The server sends top-level cards only, so nothing here is column-less; the
  // check is what makes that a statement rather than an assumption.
  for (const task of visibleTasks) {
    if (task.column_id !== null) byColumn.get(task.column_id)?.push(task)
  }

  const goalList = goals.data ?? []
  /**
   * Whether to draw the lanes yet.
   *
   * Not until the goals are in: every card would fall into the "No goal" lane
   * for as long as the request took, and the board would visibly re-sort
   * itself the moment it landed.
   */
  const inLanes = grouped && !goals.isPending
  /**
   * The lanes to draw, in the goals page's own order, with everything on no
   * goal at the bottom.
   *
   * Every open goal gets a lane whether or not a card is on it yet: an empty
   * lane is the drop target that puts the first card on a goal. A settled goal
   * only appears while it still has cards on the board, because a lane for
   * work that has stopped is a row of nothing that never goes away.
   */
  const lanes = [
    ...goalList
      .filter(
        (goal) => goal.status === 'open' || visibleTasks.some((task) => task.goal_id === goal.id),
      )
      .map((goal) => ({ key: goal.id, goal })),
    { key: NO_GOAL_LANE, goal: null },
  ]

  /**
   * The board's last column, which is what "done" means for a card: arriving
   * there writes the card's finishing time and leaving there clears it. A
   * position rather than a name — a column called Done with a column to its
   * right is not the end of the board.
   */
  const doneColumn = columns.at(-1)

  /** The grid the header row and every lane share, so their columns line up. */
  const laneTracks = columns
    .map((column) => (collapsed.includes(column.id) ? '52px' : 'minmax(280px, 400px)'))
    .join(' ')

  const tasksIn = (laneKey: string, columnId: string) =>
    (byColumn.get(columnId) ?? []).filter((task) => (task.goal_id ?? NO_GOAL_LANE) === laneKey)

  // A column's own draggable id is prefixed to keep it out of the task id
  // namespace — the two are otherwise both plain UUIDs.
  const columnDragPrefix = 'column:'
  const isColumnDrag = (id: UniqueIdentifier) =>
    typeof id === 'string' && id.startsWith(columnDragPrefix)

  const nameOfDragged = (id: UniqueIdentifier) =>
    isColumnDrag(id)
      ? nameOfColumnId(String(id).slice(columnDragPrefix.length))
      : (tasks.data?.find((task) => task.id === id)?.title ?? 'the card')
  const columnOf = (id: string) => columns.find((column) => column.id === id)
  const nameOfColumnId = (id: string) => columnOf(id)?.name ?? 'the column'
  const nameOfColumn = (id: UniqueIdentifier | undefined) =>
    // Through `columnIdOf`, because in lanes the target is a lane's cell in a
    // column rather than the column itself — and "over lane:…::uuid" is not a
    // sentence anybody wants read to them.
    typeof id === 'string' ? nameOfColumnId(columnIdOf(id)) : 'nowhere'

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

  function onToggleCollapse(column: BoardColumn) {
    toggle(column.id)
    announce(
      collapsed.includes(column.id) ? `${column.name} expanded.` : `${column.name} collapsed.`,
    )
  }

  function onToggleLane(laneKey: string, name: string) {
    toggleLane(laneKey)
    announce(foldedLanes.includes(laneKey) ? `${name} expanded.` : `${name} folded away.`)
  }

  /** One gesture for the whole board: everything away, or everything back. */
  const everyLaneFolded = lanes.length > 0 && lanes.every((lane) => foldedLanes.includes(lane.key))

  function onFoldAllLanes() {
    setFoldedLanes(everyLaneFolded ? [] : lanes.map((lane) => lane.key))
    announce(everyLaneFolded ? 'Every lane expanded.' : 'Every lane folded away.')
  }

  function onDragStart(event: DragStartEvent) {
    setDragging(event.active.id)
  }

  function onDragEnd(event: DragEndEvent) {
    // Cleared here rather than in each branch below: every one of them ends
    // the drag, and an overlay left on screen is a card stuck to the pointer.
    setDragging(null)

    const overId = event.over?.id
    if (typeof overId !== 'string') return

    // Every drop target names a column; in lanes it names the lane as well.
    const droppedIn = columnIdOf(overId)

    if (isColumnDrag(event.active.id)) {
      const columnId = String(event.active.id).slice(columnDragPrefix.length)
      moveColumnTo(
        columns.findIndex((column) => column.id === columnId),
        columns.findIndex((column) => column.id === droppedIn),
      )
      return
    }

    const task = tasks.data?.find((candidate) => candidate.id === event.active.id)
    if (!task) return

    // Refused here as well as by the server: a card that snaps back with an
    // error banner is how you find out afterwards, and the column already
    // greyed itself the moment the card was picked up. Said out loud by
    // `announcements.onDragEnd` rather than here — dnd-kit speaks on every
    // drop, and two live regions describing one gesture is one of them
    // talking over the other.
    if (forbiddenFor(task).has(droppedIn)) return

    const lane = laneKeyOf(overId)
    const wasOn = task.goal_id ?? NO_GOAL_LANE
    const changingGoal = lane !== null && lane !== wasOn
    const changingColumn = task.column_id !== droppedIn
    // The section of a divided column the card was dropped on. A card put in a
    // different section of the column it is already in has not moved by the
    // board's reckoning, but it has changed how the work ended — which is a
    // change worth making, so it counts here the way a column does.
    const landedOn = outcomeIndexOf(overId)
    const changingOutcome = landedOn !== null && landedOn !== task.outcome_index
    // A card put back where it came from is not a move, and a lane it was
    // already in is not a change of goal: neither is worth a request.
    if (!changingColumn && !changingGoal && !changingOutcome) return

    const wanted: Move = {
      taskId: task.id,
      taskRef: task.reference,
      // The outcome travels with the move, so a card only changing section
      // still names the column it is staying in.
      columnId: changingColumn || changingOutcome ? droppedIn : null,
      position: byColumn.get(droppedIn)?.length ?? 0,
      outcomeIndex: landedOn,
      outcome: landedOn === null ? null : (columnOf(droppedIn)?.outcomes[landedOn] ?? null),
      ...(changingGoal ? { goalId: lane === NO_GOAL_LANE ? null : lane } : {}),
    }

    if (changingColumn && doneColumn && task.column_id === doneColumn.id) {
      setReopening({ move: wanted, task, from: doneColumn.name, to: nameOfColumnId(droppedIn) })
      return
    }

    move.mutate(wanted)
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

      {/* The search takes the row and the buttons sit together at the end of
          it. They are one group in the markup as well as on the screen: the
          toolbar used to be a two-cell grid holding a search box and one
          button, and every button added since had been landing in a row of its
          own underneath, stretched across the board. */}
      <div className={styles.toolbar}>
        <SearchBar
          query={search}
          onChange={setSearch}
          columns={columns}
          members={memberList}
          goals={goalList}
        />
        <div className={styles.tools}>
          {/* A toggle rather than a second page: lanes are the same board read
              by goal instead of by column, and a board you have to leave to
              group is one you group once and then stop using. */}
          <Button
            small
            aria-pressed={grouped}
            className={grouped ? styles.groupedOn : undefined}
            onClick={() => setGrouped(!grouped)}
            title="Split the board into one lane per goal"
          >
            ▤ Lanes
            <span className={styles.hint}>{grouped ? 'on' : 'off'}</span>
          </Button>
          {/* Only while there are lanes to fold. Off the board, it is a button
              about nothing. */}
          {inLanes ? (
            <Button
              small
              onClick={onFoldAllLanes}
              title={
                everyLaneFolded ? 'Open every goal back up' : 'Fold every goal down to its heading'
              }
            >
              {everyLaneFolded ? '⌄ Open all' : '⌃ Fold all'}
            </Button>
          ) : null}
          <Button small onClick={() => setTemplatesOpen(true)}>
            + Templates
            <span className={styles.hint}>{templateList.length}</span>
          </Button>
          <AddColumnButton board={board.data} onClick={() => setColumnDialog('new')} />
          <QuickFilters
            goals={goalList}
            goal={quickGoal}
            onGoal={setQuickGoal}
            status={quickStatus}
            onStatus={setQuickStatus}
          />
        </div>
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
        {inLanes ? (
          <div ref={boardRef} className={styles.lanes}>
            {/* The column headings, once, above every lane — with the drag
                handle and the fold still on them. Repeating them down each
                lane would say the same five words as many times as there are
                goals, and put the board's own controls in five places. */}
            <div className={styles.laneRow} style={{ gridTemplateColumns: laneTracks }}>
              {columns.map((column) => (
                <Column
                  key={column.id}
                  part="head"
                  column={column}
                  tasks={byColumn.get(column.id) ?? []}
                  isFirst={column.id === firstColumn?.id}
                  collapsed={collapsed.includes(column.id)}
                  collapsedOutcomes={collapsedOutcomes}
                  forbidden={forbidden.has(column.id)}
                  onToggleCollapse={() => onToggleCollapse(column)}
                  onToggleOutcome={toggleOutcome}
                  onOpenTask={setOpenTaskId}
                  onMoveSubStatus={() => {}}
                  onAddTask={() => setCreatingTask(true)}
                  onEdit={() => setColumnDialog(column)}
                  members={memberList}
                />
              ))}
            </div>

            {lanes.map(({ key, goal }) => {
              const laneFolded = foldedLanes.includes(key)
              const name = goal?.name ?? 'No goal'
              return (
                <section key={key} className={styles.lane} aria-label={name}>
                  <LaneHead
                    goal={goal}
                    projectKey={projectKey}
                    count={columns.reduce(
                      (total, column) => total + tasksIn(key, column.id).length,
                      0,
                    )}
                    folded={laneFolded}
                    rowId={`lane-${key}`}
                    onToggleFold={() => onToggleLane(key, name)}
                    onAdd={() => {
                      setComposingOn(goal?.id ?? null)
                      setCreatingTask(true)
                    }}
                  />
                  {laneFolded ? null : (
                    <div
                      id={`lane-${key}`}
                      className={styles.laneRow}
                      style={{ gridTemplateColumns: laneTracks }}
                    >
                      {columns.map((column) => (
                        <Column
                          key={column.id}
                          part="cards"
                          laneKey={key}
                          column={column}
                          tasks={tasksIn(key, column.id)}
                          isFirst={column.id === firstColumn?.id}
                          collapsed={collapsed.includes(column.id)}
                          collapsedOutcomes={collapsedOutcomes}
                          forbidden={forbidden.has(column.id)}
                          onToggleCollapse={() => onToggleCollapse(column)}
                          onToggleOutcome={toggleOutcome}
                          onOpenTask={setOpenTaskId}
                          onMoveSubStatus={(taskId, index) =>
                            moveSubStatus.mutate({ taskId, index })
                          }
                          onAddTask={() => setCreatingTask(true)}
                          onEdit={() => setColumnDialog(column)}
                          members={memberList}
                        />
                      ))}
                    </div>
                  )}
                </section>
              )
            })}
          </div>
        ) : (
          <div ref={boardRef} className={styles.board}>
            {columns.map((column) => (
              <Column
                key={column.id}
                column={column}
                tasks={byColumn.get(column.id) ?? []}
                isFirst={column.id === firstColumn?.id}
                collapsed={collapsed.includes(column.id)}
                collapsedOutcomes={collapsedOutcomes}
                forbidden={forbidden.has(column.id)}
                onToggleCollapse={() => onToggleCollapse(column)}
                onToggleOutcome={toggleOutcome}
                onOpenTask={setOpenTaskId}
                onMoveSubStatus={(taskId, index) => moveSubStatus.mutate({ taskId, index })}
                onAddTask={() => setCreatingTask(true)}
                onEdit={() => setColumnDialog(column)}
                members={memberList}
              />
            ))}
          </div>
        )}

        {/* Outside the board, so the board's own overflow has nothing to say
            about where the card in the air is allowed to be drawn. */}
        <DragOverlay dropAnimation={null}>
          {draggedTask ? (
            <article
              // Filtered like the card in the column, and for the same reason:
              // an active task has no status class, and a template string would
              // put the word "undefined" in the class list instead of nothing.
              className={[
                styles.task,
                styles[`type_${draggedTask.type}`],
                styles[draggedTask.status],
                styles.lifted,
              ]
                .filter(Boolean)
                .join(' ')}
              style={
                draggedTask.goal_colour
                  ? ({ '--card-rail': draggedTask.goal_colour } as CSSProperties)
                  : undefined
              }
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
          isLast={columnDialog !== 'new' && columnDialog.id === doneColumn?.id}
          maxOutcomes={board.data.max_outcomes}
          announce={announce}
          onDone={refresh}
          onClose={() => setColumnDialog(null)}
        />
      ) : null}

      {reopening ? (
        <ReopenDialog
          reopening={reopening}
          onClose={() => setReopening(null)}
          onConfirm={() => {
            move.mutate(reopening.move)
            announce(`${reopening.task.reference} reopened into ${reopening.to}.`)
            setReopening(null)
          }}
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
          goals={goalList}
          defaultGoalId={composingOn}
          announce={announce}
          onDone={refresh}
          onClose={() => {
            setCreatingTask(false)
            setComposingOn(null)
          }}
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
          goals={goalList}
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
          goals={goalList}
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
  goals,
}: {
  query: string
  onChange: (query: string) => void
  columns: BoardColumn[]
  members: Person[]
  goals: Goal[]
}) {
  const [highlighted, setHighlighted] = useState(0)
  const [dismissed, setDismissed] = useState(false)
  const suggestions = dismissed ? [] : suggestionsFor(activeToken(query), columns, members, goals)

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
        placeholder='Search, or tag it: col:"In progress"  who:Aditi  goal:Search  blk:  hld:'
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
                <span className={styles.suggestionKind}>{SUGGESTION_KINDS[suggestion.kind]}</span>
                {/* The goal's own colour beside its name, because that is what
                    the tag is really selecting: the rail on the cards it is
                    about to leave on the board. */}
                {suggestion.colour ? (
                  <span
                    className={styles.suggestionDot}
                    style={{ background: suggestion.colour }}
                    aria-hidden="true"
                  />
                ) : null}
                {suggestion.value}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}

const SUGGESTION_KINDS: Record<Suggestion['kind'], string> = {
  column: 'Column',
  assignee: 'Assignee',
  goal: 'Goal',
}

/**
 * Two dropdowns, narrowing the board by goal and by status — tucked behind a
 * toggle rather than sitting on the toolbar all the time.
 *
 * A pair of selects rather than a row of chips: a project's goal list is
 * open-ended, and a chip for each would be the one control on the board that
 * grows with the data instead of the columns. Both ANDed onto whatever the
 * search box is already narrowing to — see the `.filter` calls around
 * `visibleTasks`.
 *
 * Collapsed by default and opened from the button at its right, which is also
 * where it closes back to: a board with two more selects parked on the
 * toolbar permanently is a toolbar that grew for a filter most visits do not
 * use. Closing it does not clear it — a filter left on while the drawer is
 * shut is still doing its job, which is what the dot on the button is for.
 */
function QuickFilters({
  goals,
  goal,
  onGoal,
  status,
  onStatus,
}: {
  goals: Goal[]
  goal: 'all' | 'none' | (string & {})
  onGoal: (goal: 'all' | 'none' | (string & {})) => void
  status: 'all' | Task['status']
  onStatus: (status: 'all' | Task['status']) => void
}) {
  const [open, setOpen] = useState(false)
  const active = goal !== 'all' || status !== 'all'

  return (
    <div className={styles.quickFilters}>
      <div
        className={`${styles.filterDrawer} ${open ? styles.filterDrawerOpen : ''}`}
        // Hidden from screen readers while shut, and each select pulled out of
        // tab order with it: the drawer's width collapses to nothing but the
        // selects inside it stay in the document, and a control a sighted user
        // cannot see is still one a keyboard user could tab into.
        aria-hidden={!open}
      >
        <select
          value={goal}
          onChange={(event) => onGoal(event.target.value)}
          tabIndex={open ? undefined : -1}
          aria-label="Filter the board by goal"
        >
          <option value="all">All goals</option>
          <option value="none">No goal</option>
          {goals.map((candidate) => (
            <option key={candidate.id} value={candidate.id}>
              {candidate.name}
            </option>
          ))}
        </select>
        <select
          value={status}
          onChange={(event) => onStatus(event.target.value as 'all' | Task['status'])}
          tabIndex={open ? undefined : -1}
          aria-label="Filter the board by status"
        >
          <option value="all">All statuses</option>
          {(Object.keys(STATUS_LABELS) as Task['status'][]).map((candidate) => (
            <option key={candidate} value={candidate}>
              {STATUS_LABELS[candidate]}
            </option>
          ))}
        </select>
      </div>
      <Button
        small
        aria-pressed={open}
        aria-expanded={open}
        className={open ? styles.groupedOn : undefined}
        onClick={() => setOpen(!open)}
        title={open ? 'Hide the quick filters' : 'Filter the board by goal or status'}
      >
        ⏷ Filters
        {active ? <span className={styles.filterDot} aria-hidden="true" /> : null}
      </Button>
    </div>
  )
}

/** A held drop: the move it would make, and the two columns it reads between. */
interface Reopening {
  move: Move
  task: Task
  from: string
  to: string
}

/**
 * Asks before a finished card goes back on the board.
 *
 * Worded as what it will do rather than as a warning, because reopening a card
 * is a perfectly ordinary thing to want: work comes back. What the reader is
 * being told is that it will be written down.
 */
function ReopenDialog({
  reopening,
  onClose,
  onConfirm,
}: {
  reopening: Reopening
  onClose: () => void
  onConfirm: () => void
}) {
  const { task, from, to } = reopening
  return (
    <Modal
      title={`Reopen ${task.reference}?`}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Leave it in {from}</Button>
          <Button variant="go" onClick={onConfirm}>
            Reopen it
          </Button>
        </>
      }
    >
      <ModalBody>
        <p className={styles.confirm}>
          <b>{task.title}</b> is finished — it is in {from}, which is the end of this board. Moving
          it to {to} puts it back to work: it stops counting as done, and its history records that
          it was reopened.
        </p>
      </ModalBody>
    </Modal>
  )
}

interface Move {
  taskId: string
  /** `ATL-41` — what an error about this card will name it by. */
  taskRef: string
  /** Null when the card was dropped in the column it was already in. */
  columnId: string | null
  position: number
  /**
   * The section of a divided column the card lands on, by name, and where the
   * board draws it while the request is in the air. Null for a drop on a
   * column that is not divided — every column but the board's last, and most
   * of those.
   */
  outcome: string | null
  outcomeIndex: number | null
  /**
   * The goal to put the card on, or null to take it off the one it is on.
   * Left out entirely — as opposed to null — when the drop said nothing about
   * a goal, which is every drop on a board that is not in lanes.
   */
  goalId?: string | null
}

/**
 * A goal's lane heading: what the lane is, and how much is in it.
 *
 * A full-width bar above the lane's cells rather than a label to one side.
 * A side rail would take 200px off every column on the board for a name that
 * is read once, and on a board scrolled sideways it would be the first thing
 * to scroll out of sight — which is the one thing about a lane you always need
 * to be able to see.
 */
function LaneHead({
  goal,
  projectKey,
  count,
  folded,
  rowId,
  onToggleFold,
  onAdd,
}: {
  /** Null for the lane everything on no goal falls into. */
  goal: Goal | null
  projectKey: string
  count: number
  folded: boolean
  /** The row this heading opens and closes, for `aria-controls`. */
  rowId: string
  onToggleFold: () => void
  onAdd: () => void
}) {
  const name = goal?.name ?? 'No goal'

  return (
    <div
      className={`${styles.laneHead} ${goal ? '' : styles.laneHeadLoose}`}
      style={goal ? ({ '--goal': goal.colour } as CSSProperties) : undefined}
    >
      {/* The fold, on the heading rather than beside it: the heading is the
          whole of a folded lane, and the control that brings it back has to be
          on the part that is still there. */}
      <button
        type="button"
        className={styles.laneFold}
        aria-expanded={!folded}
        aria-controls={rowId}
        aria-label={folded ? `Open ${name}` : `Fold ${name} away`}
        title={folded ? `Open ${name}` : `Fold ${name} away`}
        onClick={onToggleFold}
      >
        <span aria-hidden="true">{folded ? '▸' : '▾'}</span>
      </button>
      {goal ? (
        <Link
          to="/p/$projectKey/goals/$goalRef"
          params={{ projectKey, goalRef: goal.reference }}
          className={styles.laneName}
        >
          <span className={styles.laneDot} aria-hidden="true" />
          {goal.name}
          <span className={styles.laneRef}>{goal.reference}</span>
        </Link>
      ) : (
        <span className={styles.laneName}>
          <span className={styles.laneDot} aria-hidden="true" />
          No goal
        </span>
      )}
      <span className={styles.count}>{count}</span>
      <Button
        variant="ghost"
        small
        onClick={onAdd}
        aria-label={goal ? `Add a task on ${goal.name}` : 'Add a task on no goal'}
      >
        + Task
      </Button>
    </div>
  )
}

function Column({
  column,
  tasks,
  isFirst,
  collapsed,
  collapsedOutcomes,
  forbidden,
  onToggleCollapse,
  onToggleOutcome,
  onOpenTask,
  onMoveSubStatus,
  onAddTask,
  onEdit,
  members,
  part = 'all',
  laneKey,
}: {
  column: BoardColumn
  tasks: Task[]
  isFirst: boolean
  collapsed: boolean
  /**
   * Ids of the outcome sections folded away, board-wide — see `useFolded`. A
   * section's id is its column's plus its index, which is what a fold survives
   * a rename by naming instead of the label.
   */
  collapsedOutcomes: string[]
  /**
   * Whether the card currently in the air may not be dropped here — its
   * template's own stages rule this column out. False the rest of the time,
   * including while nothing is being dragged.
   */
  forbidden: boolean
  onToggleCollapse: () => void
  /** Fold or unfold one outcome section, by its id — see `collapsedOutcomes`. */
  onToggleOutcome: (id: string) => void
  onOpenTask: (taskId: string) => void
  onMoveSubStatus: (taskId: string, index: number) => void
  /** Open the whole form, which is where a card is written. */
  onAddTask: () => void
  onEdit: () => void
  /** The project's people, for the `@` tags in a stage label. */
  members: Person[]
  /**
   * Which half of a column this is drawing.
   *
   * `all` is the ordinary board: one box with the heading, the cards and the
   * composer in it. Grouped by goal, the column is split — `head` draws the
   * heading once above every lane, and `cards` draws one lane's share of the
   * cards, over and over down the board. Splitting the component rather than
   * repeating a whole column per lane is what keeps the fold, the reordering
   * handle and the column's own rules in one place.
   */
  part?: 'all' | 'head' | 'cards'
  /** Which lane this cell belongs to; only a `cards` part has one. */
  laneKey?: string
}) {
  // A lane's cell is its own drop target — the lane says which goal, the
  // column says which column, and a drop reads both off the one id.
  const dropId = part === 'cards' && laneKey ? `lane:${laneKey}::${column.id}` : column.id
  const { setNodeRef: setDropRef, isOver } = useDroppable({ id: dropId })
  // A column is draggable on the whole card (so it visually moves as one
  // piece) but only the handle carries the listeners — otherwise every
  // button in the header would start a drag instead of doing its own job.
  const {
    attributes,
    listeners,
    setNodeRef: setDragRef,
    transform,
    isDragging,
  } = useDraggable({
    id: `column:${column.id}`,
    // A lane's cells are not the column, they are one lane's share of it —
    // and eight copies of a column all claiming to be draggable is eight
    // things dnd-kit would have to be told apart by.
    disabled: part === 'cards',
  })
  const counted = `${tasks.length} ${tasks.length === 1 ? 'card' : 'cards'}`

  const setRefs = (node: HTMLElement | null) => {
    setDropRef(node)
    if (part !== 'cards') setDragRef(node)
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
        part === 'head' && styles.headOnly,
        part === 'cards' && styles.cardsOnly,
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
      {collapsed && part === 'cards' ? (
        // A folded column still has a lane's worth of cards in it; what it
        // does not have is room to draw them. The cell stays as a drop target
        // so a card can be put into a column that is folded away, which is
        // exactly what folding one is for.
        <div key="railcell" className={styles.railCell} aria-hidden="true">
          {tasks.length ? <span className={styles.count}>{tasks.length}</span> : null}
        </div>
      ) : collapsed ? (
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
          {part === 'cards' ? null : (
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
          )}

          {part === 'head' ? null : column.outcomes.length ? (
            // A divided column is still one column: one heading, one count,
            // one place on the board. What it has instead of a single stack of
            // cards is a stack per way the work can have ended, each its own
            // drop target, so which one a card is in is something you set by
            // putting it there.
            <div className={styles.sections}>
              {column.outcomes.map((label, index) => {
                const outcomeId = `${column.id}:${index}`
                return (
                  <OutcomeSection
                    key={index}
                    label={label}
                    dropId={`${OUTCOME_PREFIX}${index}::${dropId}`}
                    // Cards written before the column was divided, and any left
                    // behind by a section that was renamed away, sit in the
                    // first: better an honest heading than a stack with none.
                    tasks={tasks.filter((task) => (task.outcome_index ?? 0) === index)}
                    members={members}
                    collapsed={collapsedOutcomes.includes(outcomeId)}
                    onToggleCollapse={() => onToggleOutcome(outcomeId)}
                    onOpenTask={onOpenTask}
                    onMoveSubStatus={onMoveSubStatus}
                  />
                )
              })}
            </div>
          ) : (
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
          )}

          {isFirst && part === 'all' ? (
            <button className={styles.addTask} onClick={onAddTask}>
              + Add a task
            </button>
          ) : (
            <div className={styles.foot} />
          )}
        </div>
      )}
    </section>
  )
}

/**
 * One section of a divided column: how the work ended, and what ended that way.
 *
 * Its own drop target, and its own heading with its own count — a column's
 * count is still the sum of them, but "3 done, 1 cancelled" is the sentence
 * dividing the column was for. The heading stays when the section is empty,
 * because an empty section is the drop target that puts the first card in it.
 *
 * Bordered and tinted as a panel of its own, the way a column reads as a
 * panel against the board behind it — a rule between two stretches of the
 * same colour stops reading as a division the moment there are three of
 * them; a box does not.
 *
 * Its cards can be folded away behind the heading, the same fold a whole
 * column has and for the same reason: a section nobody is triaging today is
 * still a heading and a count worth keeping in view. It stays a drop target
 * either way, so a card can be put into a folded section without opening it
 * first.
 */
function OutcomeSection({
  label,
  dropId,
  tasks,
  members,
  collapsed,
  onToggleCollapse,
  onOpenTask,
  onMoveSubStatus,
}: {
  label: string
  dropId: string
  tasks: Task[]
  members: Person[]
  collapsed: boolean
  onToggleCollapse: () => void
  onOpenTask: (taskId: string) => void
  onMoveSubStatus: (taskId: string, index: number) => void
}) {
  const { setNodeRef, isOver } = useDroppable({ id: dropId })
  const counted = `${tasks.length} ${tasks.length === 1 ? 'card' : 'cards'}`

  return (
    <section
      ref={setNodeRef}
      className={`${styles.section} ${isOver ? styles.sectionOver : ''}`}
      aria-label={collapsed ? `${label}, ${counted}, collapsed` : `${label}, ${counted}`}
    >
      <h4 className={styles.sectionHead}>
        <Button
          variant="ghost"
          small
          aria-expanded={!collapsed}
          aria-label={collapsed ? `Expand ${label}` : `Collapse ${label}`}
          onClick={onToggleCollapse}
        >
          <span aria-hidden="true">{collapsed ? '▸' : '▾'}</span>
        </Button>
        <span className={styles.sectionLabel}>{label}</span>
        <span className={styles.count}>{tasks.length}</span>
      </h4>
      {collapsed ? null : (
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
      aria-label={[
        // The type leads, and is said at all, because the rail that carries
        // it cannot be: a colour down the edge of a card is the one thing on
        // it a screen reader has no way to reach. With the tile gone this is
        // the only place on the card the word appears — the goal below it has
        // the same problem and the same answer.
        `${TYPE_LABELS[task.type]} ${task.reference}: ${task.title}`,
        task.parent_reference ? `a sub-task of ${task.parent_reference}` : null,
        task.goal_name ? `on ${task.goal_name}` : null,
      ]
        .filter(Boolean)
        .join(', ')}
      // The rail's colour, when the card is on a goal. Read by `.task::before`
      // through a fallback, so a card on no goal keeps the status colour the
      // rail has always been — see ProjectBoard.module.css.
      style={task.goal_colour ? ({ '--card-rail': task.goal_colour } as CSSProperties) : undefined}
      // No transform of its own: while this card is in the air the DragOverlay
      // is the copy that follows the pointer, and this one stays put and dims.
      className={[
        styles.task,
        styles[`type_${task.type}`],
        styles[task.status],
        isDragging && styles.dragging,
      ]
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
  /* Cancelling the work is what stops it owing anybody a date, so a dropped
     card wears neither the tab nor the date that would otherwise stand in for
     it. A red "12d late" shouting from a card nobody is going to do is the
     loudest wrong thing the board could say. */
  const dated = task.status !== 'cancelled'
  const tab = dated ? dueTabMark(task.due_date) : null

  return (
    <>
      {/* Above the title and outside the card's outline, which is the whole of
          what makes it findable — see `DueTab`. */}
      {tab ? <DueTab mark={tab} /> : null}

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

      {/* Every piece of chrome the card has, on one line under the title.
          There used to be a row above the title as well, and between them they
          gave the same six-element tax to every card whether or not it had
          anything urgent to say — so the eye had to read three rows down to
          find the one sentence that differs between cards.

          The line has two halves and a gap that grows between them. On the
          left is what the card is: which one, whose part of what, and how it
          stands. On the right is what has accumulated around it — how much has
          been said, what it is tracked as elsewhere, how far through its parts
          it is, and who has it. The gap is what keeps the faces on one margin
          down the column however much or little sits to their left. */}
      <div className={styles.strip}>
        {/* Someday is the baseline every card starts on, so flagging it too
            would be noise on every single card — the same reasoning that keeps
            the status pill off an active task.

            Only urgent gets a filled pill. The four levels escalate in chrome
            and never in width, so a column of cards keeps one margin down its
            right-hand side however its work is prioritised. */}
        {task.priority !== 'someday' ? (
          <span
            className={`${styles.prio} ${styles[`priority_${task.priority}`]}`}
            title={PRIORITY_LABELS[task.priority]}
          >
            <PriorityIcon priority={task.priority} />
            <span className="visually-hidden">{PRIORITY_LABELS[task.priority]}</span>
          </span>
        ) : null}
        <span className={styles.reference}>{task.reference}</span>
        {/* A sub-task's own reference already carries its parent's number, but
            `ATL-41-2` only says so to a reader who knows the scheme — and on a
            board, where the two cards may be columns apart, the parent is the
            thing you need to recognise the card at all. */}
        {task.parent_reference ? (
          <span className={styles.parent} title={`Sub-task of ${task.parent_reference}`}>
            of {task.parent_reference}
          </span>
        ) : null}
        {/* The word the tile used to carry. It is the only thing left saying
            which of the four states a card is in, the tint underneath it
            aside, so it says it in full. */}
        {active ? null : (
          <span className={`${styles.pill} ${styles[`pill_${task.status}`]}`}>
            {STATUS_LABELS[task.status]}
          </span>
        )}
        {/* Only the dates the tab did not take. A date near enough to be asked
            something of is up on the tab; one further out is only reporting,
            so it stays down here as the date itself and the card never says it
            twice. No date, no mark either way — a dash where a date goes reads
            as a date that failed to load. */}
        {dated && !tab ? <DueMark iso={task.due_date} /> : null}
        {/* The name of the colour on the rail. A rail on its own is a legend
            you have to have learned; with the name beside it, one card teaches
            you the rest of the column. Last of the left-hand half and hard
            against the gap, because it is the one thing here that can afford
            to be cut. */}
        {task.goal_name && task.goal_colour ? (
          <GoalChip
            name={task.goal_name}
            colour={task.goal_colour}
            title={`On ${task.goal_name} (${task.goal_reference})`}
          />
        ) : null}
        <span className={styles.stripGap} />
        {task.comment_count ? (
          <span className={styles.meta} title={`${task.comment_count} comments`}>
            <CommentIcon />
            <span className={styles.metaValue} aria-hidden="true">
              {task.comment_count}
            </span>
            <span className="visually-hidden">{task.comment_count} comments</span>
          </span>
        ) : null}
        {/* Marks rather than identifiers here, and spelled out in the dialog.
            Quieting a board is only honest if what it stopped saying is one
            click away — see `TaskRef`. */}
        {task.jira_ref ? <TaskRef kind="jira" value={task.jira_ref} compact /> : null}
        {task.pr_ref ? <TaskRef kind="pr" value={task.pr_ref} compact /> : null}
        {/* Sits on the line rather than above it. The stage bar and these dots
            are drawn as different things because they are different things —
            the bar is one journey with a position along it, the dots are a set
            of items with some of them ticked — but a set of counts is what
            this half of the line already is, and given a row of its own the
            set read as a second bar. */}
        {task.subtask_count ? (
          <SubtaskDots total={task.subtask_count} open={task.open_subtask_count} />
        ) : null}
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
        {/* Small, like everything else on the line. The owner is still the
            last thing on the card and the only face at full strength, but it
            is no longer a 26px disc anchoring a row of 11px print — which is
            what made the line read as a second row of chrome. */}
        <Avatar name={task.assignee.name} colour={task.assignee.colour} small />
      </div>
    </>
  )
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
  isLast,
  maxOutcomes,
  announce,
  onDone,
  onClose,
}: {
  projectKey: string
  column: BoardColumn | null
  canDelete: boolean
  /**
   * Whether this is the board's last column, which is the only one that can be
   * divided into outcomes. A new column is added to the right, so it will be —
   * but it is not one yet, and offering the sections before the column exists
   * is asking about a thing that has nowhere to go.
   */
  isLast: boolean
  maxOutcomes: number
  announce: (message: string) => void
  onDone: () => Promise<void>
  onClose: () => void
}) {
  const [form, setForm] = useState<ColumnInput>({
    name: column?.name ?? '',
    description: column?.description ?? '',
    outcomes: column?.outcomes ?? [],
  })
  const outcomes = form.outcomes ?? []

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

  const complete =
    form.name.trim() && form.description.trim() && outcomes.every((label) => label.trim())
  const error = save.error ?? remove.error

  function setOutcomes(next: string[]) {
    setForm({ ...form, outcomes: next })
  }

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

        {/* Only at the end of the board. An outcome is how a piece of work
            ended, and nothing ends in the middle of one. */}
        {column && isLast ? (
          <Field
            label="Outcomes"
            hint={`How work can end here — up to ${maxOutcomes} sections this column is divided into. Leave it empty to draw no distinction.`}
          >
            <div className={styles.outcomes}>
              {outcomes.map((label, index) => (
                <div key={index} className={styles.outcomeRow}>
                  <span className={styles.outcomeNumber}>{index + 1}</span>
                  <input
                    value={label}
                    maxLength={40}
                    className={styles.grow}
                    aria-label={`Outcome ${index + 1}`}
                    placeholder={['Done', 'Cancelled', 'In prod'][index] ?? 'Outcome'}
                    onChange={(event) =>
                      setOutcomes(
                        outcomes.map((one, at) => (at === index ? event.target.value : one)),
                      )
                    }
                  />
                  <Button
                    small
                    variant="ghost"
                    aria-label={`Remove outcome ${index + 1}`}
                    onClick={() => setOutcomes(outcomes.filter((_, at) => at !== index))}
                  >
                    ×
                  </Button>
                </div>
              ))}
              {outcomes.length < maxOutcomes ? (
                <Button small variant="ghost" onClick={() => setOutcomes([...outcomes, ''])}>
                  + Add an outcome
                </Button>
              ) : null}
              {/* Said here rather than left to the refusal: the cards are
                  already in the sections, and what becomes of them is the
                  thing anybody shortening the list is actually asking. */}
              {column.outcomes.length > outcomes.length ? (
                <p className={styles.note}>
                  Cards in a section you have taken away move to the last one still standing.
                </p>
              ) : null}
            </div>
          </Field>
        ) : null}
      </ModalBody>
    </Modal>
  )
}

/**
 * How near a date has to be before the card grows a tab for it.
 *
 * One day, so: today and tomorrow. A team that works a week ahead wants 3 or
 * 5, and this line is the whole edit — everything downstream reads the number
 * rather than repeating the rule.
 */
const DUE_TAB_DAYS = 1

/** A date near enough to be worth breaking the card's outline for. */
interface DueTabMark {
  tone: 'late' | 'soon'
  /** The abbreviation on the tab. */
  text: string
  /** The same thing at length, for the tooltip and for a screen reader. */
  said: string
}

/**
 * Whether a date has earned a tab, and what it says if it has.
 *
 * Null for everything else, which is most cards: a date three weeks out is
 * only telling you something, and it stays down in the small print as the date
 * itself. Nothing that returns null here has been dropped — see the strip.
 */
function dueTabMark(iso: string | null): DueTabMark | null {
  if (!iso) return null

  const days = daysUntilDue(iso)
  const on = formatDueLong(iso)

  // Overdue always earns one, however long ago. A date does not stop mattering
  // because it passed a fortnight ago, and the count is the part anybody acts
  // on: one day over is a card to finish, three weeks over is a conversation
  // to have.
  if (days < 0) {
    const late = -days
    return {
      tone: 'late',
      text: `${late}d late`,
      said: `${late} ${late === 1 ? 'day' : 'days'} late, due ${on}`,
    }
  }

  if (days > DUE_TAB_DAYS) return null

  // Said as a countdown rather than as a date. "Due in 2 days" is the thing
  // being decided about; "8 Sep" is a fact you would have to work that out
  // from, and a card asking for something today should not make you.
  const text = days === 0 ? 'Due today' : days === 1 ? 'Due tomorrow' : `Due in ${days} days`

  return { tone: 'soon', text, said: `${text}, ${on}` }
}

/**
 * The one shape allowed to break the card.
 *
 * Everything else a card says is said inside its rectangle, and that is what
 * lets a column scan as a list rather than as a pile. A date that has arrived
 * or already gone is the one fact worth costing that: the tab is the only
 * silhouette on the board that is not a rounded rectangle, so a column of them
 * can be counted without reading a word.
 *
 * Cut rather than drawn — a clip path over a fill, overlapping the card's own
 * border by the pixel it is thick, so the two read as one outline rather than
 * as a shape parked on a box. No glyph inside it either: breaking the outline
 * is already the alarm, and a warning triangle on top would be the second
 * voice the filled due pill used to be.
 *
 * Because the tab carries lateness, the rail underneath is free to go on
 * saying what kind of work this is even while the card is overdue — which is
 * how the card keeps to one alarm apiece.
 */
function DueTab({ mark }: { mark: DueTabMark }) {
  return (
    <span
      className={`${styles.dueTab} ${mark.tone === 'late' ? styles.dueTabLate : styles.dueTabSoon}`}
      title={mark.said}
    >
      {/* Two elements because the cut and the join cannot be the same one: a
          clip path takes the element's own children and pseudo-elements with
          it, so anything meant to reach past the tab's edge has to hang off
          something outside the clip. The outer span is that something, and it
          is exactly as wide as the shape inside it. */}
      <span className={styles.dueTabFace} aria-hidden="true">
        {mark.text}
      </span>
      <span className="visually-hidden">{mark.said}</span>
    </span>
  )
}

/**
 * A due date on a card, drawn as near or as far as it is.
 *
 * The near end of this ramp is the tab's now, and which steps those are is
 * `DUE_TAB_DAYS`' to decide — so the five states stay written out here rather
 * than trimmed to the two a threshold of one day leaves reachable. Turning
 * that knob up is meant to be one line, not one line and an archaeology.
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
