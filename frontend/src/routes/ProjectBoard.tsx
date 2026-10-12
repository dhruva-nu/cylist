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
  useDndContext,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type Announcements,
  type ClientRect,
  type DragEndEvent,
  type DraggableAttributes,
  type DraggableSyntheticListeners,
  type DragStartEvent,
  type KeyboardCodes,
  type KeyboardCoordinateGetter,
  type ScreenReaderInstructions,
  type UniqueIdentifier,
} from '@dnd-kit/core'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams, useSearch } from '@tanstack/react-router'
import {
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
} from 'react'
import {
  api,
  type AgentPresence,
  type Board,
  type BoardColumn,
  type ColumnInput,
  type Goal,
  type Person,
  type PrRef,
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
import { hidesCancelled, hidesHold, isCancelled, splitOnHold } from './boardHiding'
import {
  agentCounts,
  agentIndicator,
  hasLiveAgent,
  isAnswerable,
  passesAgentFilter,
  type AgentFilter,
} from './agentState'
import { BoardAnnounce, CardPromptPopover, useCardPopover } from './CardPromptPopover'
import { useBoardSocket } from './useBoardSocket'
import { leastAdvancedPrState } from '../components/prRefs'
import { dueBucket, formatDue, formatDueLong } from '../components/dates'
import { GoalStatusPill } from '../components/GoalMarks'
import { Field, Modal, ModalBody } from '../components/Modal'
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
  JiraIcon,
  PrIcon,
  Tagged,
  TaskRef,
  jiraRefLabel,
  prRefLabel,
  prRefRepo,
  useAnnouncer,
} from '../components/ui'
import styles from './ProjectBoard.module.css'
import { usePermissions } from './usePermissions'
import { useViewerId } from './useViewerId'

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

/** Which way an arrow key walks a picked-up card: a column right, a column left, or nowhere. */
function columnStepFor(code: string): number {
  if (code === 'ArrowRight') return 1
  if (code === 'ArrowLeft') return -1
  return 0
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
  const step = columnStepFor(event.code)
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
 * One way of looking at a board, on or off, remembered per project — whether
 * it is split into lanes, whether the cancelled cards are shown.
 *
 * Kept the way the folded columns are, and for the same reason: how you look
 * at one board says nothing about how you want to look at another, and a
 * preference nobody can save is a smaller problem than a board that will not
 * render — see `readFolded`. The key carries the project, so walking to
 * another board reads that board's answer rather than carrying this one's.
 */
function useBoardToggle(storageKey: string): [boolean, (on: boolean) => void] {
  const read = useCallback(() => {
    try {
      return window.localStorage.getItem(storageKey) === 'true'
    } catch {
      return false
    }
  }, [storageKey])

  const [on, setOn] = useState<boolean>(read)

  useEffect(() => setOn(read()), [read])

  useEffect(() => {
    try {
      window.localStorage.setItem(storageKey, String(on))
    } catch {
      // It still holds for this visit; it just is not remembered.
    }
  }, [storageKey, on])

  return [on, setOn]
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

/**
 * A column's own draggable id is prefixed to keep it out of the task id
 * namespace — the two are otherwise both plain UUIDs.
 */
const COLUMN_DRAG_PREFIX = 'column:'

function isColumnDrag(id: UniqueIdentifier): boolean {
  return typeof id === 'string' && id.startsWith(COLUMN_DRAG_PREFIX)
}

/** The column a column drag is carrying, from the id its handle was given. */
function columnIdFromDrag(id: UniqueIdentifier): string {
  return String(id).slice(COLUMN_DRAG_PREFIX.length)
}

/** The goal filter's choices: every card, the cards on no goal, or one goal's id. */
type QuickGoal = 'all' | 'none' | (string & {})
type QuickStatus = 'all' | Task['status']

/**
 * How this reader has chosen to look at this board: what is folded away,
 * whether the board is in lanes, whether the cancelled cards are shown, and
 * which columns have opened their on-hold cards. All of it is remembered per
 * project — see `useFolded` and `useBoardToggle`.
 */
function useBoardView(projectKey: string) {
  const { folded: collapsedColumns, toggle: toggleColumn } = useFolded(
    `cylist.board.collapsed.${projectKey}`,
  )
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
  const [lanesOn, setLanesOn] = useBoardToggle(`cylist.board.lanes.${projectKey}`)
  /**
   * Whether the cancelled cards have been asked back onto the board.
   *
   * Stored the way round it is because the hide is the default (CYLIST-62):
   * work that is not going to happen is not what a board is for, and a board
   * carrying a year of it is one you would otherwise hide them on every visit.
   * Off is therefore the ordinary value, and `useBoardToggle` starts off.
   *
   * Remembered per project, like every other way of looking at a board: how
   * much dropped work one project is carrying says nothing about another. It
   * is only ever a way of reading the board — nothing about the cards changes.
   */
  const [showCancelled, setShowCancelled] = useBoardToggle(`cylist.board.cancelled.${projectKey}`)
  /**
   * Which columns have been opened at the foot to show the cards on hold.
   *
   * A list of the columns showing them rather than of the columns hiding them,
   * so the empty list — a reader who has never touched this — is every column
   * holding its held work back, which is the default the card asked for.
   *
   * Kept per column, and shared by a column's lane cells: in lanes a column is
   * drawn once per goal, and opening the foot is a question about the column
   * ("what has stalled in review?"), not about one goal's corner of it.
   */
  const { folded: holdShown, toggle: toggleHold } = useFolded(
    `cylist.board.holdShown.${projectKey}`,
  )

  return {
    collapsedColumns,
    toggleColumn,
    collapsedOutcomes,
    toggleOutcome,
    foldedLanes,
    setFoldedLanes,
    toggleLane,
    lanesOn,
    setLanesOn,
    showCancelled,
    setShowCancelled,
    holdShown,
    toggleHold,
  }
}

/**
 * How often to ask the server for the cards when nothing is telling the board.
 *
 * The fallback for when there is no socket — a proxy that will not carry an
 * upgrade, a browser that refused one. It is the behaviour this board had
 * before CYLIST-40 and it costs one boolean to keep, which is a good price for
 * never being worse than we were.
 */
function fallbackPollInterval(socketConnected: boolean, tasks: Task[]): number | false {
  if (socketConnected) return false
  return hasLiveAgent(tasks) ? 10_000 : false
}

/** Everything the board reads from the server, kept fresh by its socket. */
function useBoardData(projectKey: string) {
  const board = useQuery({
    queryKey: ['board', projectKey],
    queryFn: () => api.listColumns(projectKey),
  })
  // The board is told about changes rather than asking for them: one socket
  // per open board, and a refetch when it rings. See `useBoardSocket`.
  const socket = useBoardSocket(projectKey)
  const tasks = useQuery({
    queryKey: ['tasks', projectKey],
    queryFn: () => api.listTasks(projectKey),
    refetchInterval: (query) => fallbackPollInterval(socket.connected, query.state.data ?? []),
    refetchIntervalInBackground: false,
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

  return { board, tasks, members, templates, goals }
}

/**
 * A card as it will look once a move has landed, so the board can draw it
 * there before the server has answered.
 */
function withMoveApplied(task: Task, move: Move, landedGoal: Goal | null | undefined): Task {
  const { columnId, position, outcome, outcomeIndex, goalId } = move
  return {
    ...task,
    ...(columnId ? { column_id: columnId, position, outcome, outcome_index: outcomeIndex } : {}),
    ...(goalId === undefined
      ? {}
      : {
          goal_id: goalId,
          goal_name: landedGoal?.name ?? null,
          goal_colour: landedGoal?.colour ?? null,
          goal_reference: landedGoal?.reference ?? null,
        }),
  }
}

/**
 * Everything the board writes: a card moved, a stage slid along, the columns
 * put in a new order — and the refresh that follows each of them.
 *
 * `goals` is the project's goal list as it currently stands, which is what a
 * card dropped into another goal's lane borrows its name and colour from while
 * the move is in the air.
 */
function useBoardMutations(
  projectKey: string,
  goals: Goal[] | undefined,
  members: Person[] | undefined,
) {
  const queryClient = useQueryClient()
  const tasksKey = ['tasks', projectKey]
  const boardKey = ['board', projectKey]

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
  const moveTask = useMutation<Task | null, Error, Move, { previous: Task[] | undefined }>({
    mutationFn: async ({ taskId, taskRef, columnId, position, outcome, goalId }) => {
      const moved = columnId ? await api.moveTask(taskId, columnId, position, outcome) : null
      // By reference rather than by id for the same reason the dialog does:
      // it is what the error message will name if the server refuses.
      return goalId === undefined ? moved : api.updateTask(taskRef, { goal_id: goalId })
    },
    onMutate: async (move) => {
      await queryClient.cancelQueries({ queryKey: tasksKey })
      const previous = queryClient.getQueryData<Task[]>(tasksKey)
      const landedGoal =
        move.goalId === undefined ? null : (goals ?? []).find((goal) => goal.id === move.goalId)
      queryClient.setQueryData<Task[]>(tasksKey, (current) =>
        current?.map((task) =>
          task.id === move.taskId ? withMoveApplied(task, move, landedGoal) : task,
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

  /**
   * Handing a card to someone else from the board, without opening it —
   * optimistic for the same reason `moveSubStatus` is: the badge is the
   * control, so it has to move the moment it is clicked.
   */
  const setAssignee = useMutation<
    Task,
    Error,
    { taskId: string; taskRef: string; assigneeId: string },
    { previous: Task[] | undefined }
  >({
    mutationFn: ({ taskRef, assigneeId }) => api.updateTask(taskRef, { assignee_id: assigneeId }),
    onMutate: async ({ taskId, assigneeId }) => {
      await queryClient.cancelQueries({ queryKey: tasksKey })
      const previous = queryClient.getQueryData<Task[]>(tasksKey)
      const assignee = (members ?? []).find((person) => person.id === assigneeId)
      queryClient.setQueryData<Task[]>(tasksKey, (current) =>
        current?.map((task) => (task.id === taskId && assignee ? { ...task, assignee } : task)),
      )
      return { previous }
    },
    onError: (_error, _move, context) => {
      queryClient.setQueryData(tasksKey, context?.previous)
    },
    onSettled: refresh,
  })

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

  return { refresh, moveTask, moveSubStatus, setAssignee, reorderColumns }
}

/** What the reader has asked the board to narrow itself to. */
interface BoardFilters {
  search: string
  quickGoal: QuickGoal
  quickStatus: QuickStatus
  quickAgent: AgentFilter
  showCancelled: boolean
}

/**
 * The cards the board draws, once the search box, the quick filters and the
 * cancelled hide have each had their say — and how many that hide is holding
 * back.
 *
 * The cards on hold are not filtered here: they are held back at the foot of
 * each column, which is a decision each column makes for itself — see
 * `boardHiding.ts`. What this does settle for them is `hidingHold`, the half
 * of that decision the whole board shares.
 *
 * Client-side, over what's already fetched: the board holds every task in
 * memory regardless, and a search endpoint would be a second way to ask a
 * question this data already answers.
 */
function narrowBoard(
  tasks: Task[],
  { search, quickGoal, quickStatus, quickAgent, showCancelled }: BoardFilters,
  columns: BoardColumn[],
  members: Person[],
) {
  const parsed = parseQuery(tokenize(search))
  const narrowed = filterTasks(tasks, parsed, columns, members)
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
  /**
   * The hides, settled last — and only once everything else has had its say.
   *
   * Last because they are the two filters that answer to the others: asking
   * the board for the cancelled cards stands the cancelled hide down rather
   * than emptying the board, which is `hidesCancelled`'s whole job, and the
   * same goes for `hidesHold`. The count is taken off the narrowed set rather
   * than off every card there is, so the number on the button is how many this
   * board, as it is currently being read, is holding back.
   */
  const hidingCancelled = hidesCancelled(showCancelled, quickStatus, parsed)
  const cancelledCount = narrowed.filter(isCancelled).length
  const unhidden = hidingCancelled ? narrowed.filter((task) => !isCancelled(task)) : narrowed
  /**
   * The agent chips, last of all. Their counts are taken from the board as
   * everything else has narrowed it but before the chip itself has, so the
   * number on a chip is exactly how many cards pressing it leaves — and both
   * chips keep their numbers while either is pressed.
   */
  const agentTally = agentCounts(unhidden)
  const visibleTasks =
    quickAgent === 'all' ? unhidden : unhidden.filter((task) => passesAgentFilter(task, quickAgent))
  return { visibleTasks, cancelledCount, hidingCancelled, parsed, agentTally }
}

/** The cards to draw, sorted into their columns in the order they arrived. */
function tasksByColumn(columns: BoardColumn[], tasks: Task[]): Map<string, Task[]> {
  const byColumn = new Map(columns.map((column) => [column.id, [] as Task[]]))
  // The server sends top-level cards only, so nothing here is column-less; the
  // check is what makes that a statement rather than an assumption.
  for (const task of tasks) {
    if (task.column_id !== null) byColumn.get(task.column_id)?.push(task)
  }
  return byColumn
}

/** One lane of the board: a goal's share of every column, or the share on no goal. */
interface Lane {
  key: string
  goal: Goal | null
}

/**
 * The lanes to draw, in the goals page's own order, with everything on no
 * goal at the bottom.
 *
 * Every open goal gets a lane whether or not a card is on it yet: an empty
 * lane is the drop target that puts the first card on a goal. A settled goal
 * only appears while it still has cards on the board, because a lane for
 * work that has stopped is a row of nothing that never goes away.
 */
function lanesFor(goals: Goal[], visibleTasks: Task[]): Lane[] {
  return [
    ...goals
      .filter(
        (goal) => goal.status === 'open' || visibleTasks.some((task) => task.goal_id === goal.id),
      )
      .map((goal) => ({ key: goal.id, goal })),
    { key: NO_GOAL_LANE, goal: null },
  ]
}

export function ProjectBoard() {
  const { projectKey } = useParams({ from: '/p/$projectKey/board' })
  const may = usePermissions(projectKey)
  const { q: initialQuery = '' } = useSearch({ from: '/p/$projectKey/board' })

  const [openTaskId, setOpenTaskId] = useState<string | null>(null)
  const [creatingTask, setCreatingTask] = useState(false)
  /** The parent a new sub-task is being written under, if one is. */
  const [subtaskParentRef, setSubtaskParentRef] = useState<string | null>(null)
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
  const [quickGoal, setQuickGoal] = useState<QuickGoal>('all')
  const [quickStatus, setQuickStatus] = useState<QuickStatus>('all')
  /** The agent chips (CYLIST-90): every card, the ones that need you, or the busy ones. */
  const [quickAgent, setQuickAgent] = useState<AgentFilter>('all')
  /** The goal a card written from a lane starts on. */
  const [newTaskGoalId, setNewTaskGoalId] = useState<string | null>(null)
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
  const [draggingId, setDraggingId] = useState<UniqueIdentifier | null>(null)
  const boardRef = useRef<HTMLDivElement>(null)
  const {
    collapsedColumns,
    toggleColumn,
    collapsedOutcomes,
    toggleOutcome,
    foldedLanes,
    setFoldedLanes,
    toggleLane,
    lanesOn,
    setLanesOn,
    showCancelled,
    setShowCancelled,
    holdShown,
    toggleHold,
  } = useBoardView(projectKey)
  const { message, announce } = useAnnouncer()
  const { board, tasks, members, templates, goals } = useBoardData(projectKey)
  const { refresh, moveTask, moveSubStatus, setAssignee, reorderColumns } = useBoardMutations(
    projectKey,
    goals.data,
    members.data?.members,
  )

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
  const goalList = goals.data ?? []

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

  const { visibleTasks, cancelledCount, hidingCancelled, parsed, agentTally } = narrowBoard(
    tasks.data,
    { search, quickGoal, quickStatus, quickAgent, showCancelled },
    columns,
    memberList,
  )
  const byColumn = tasksByColumn(columns, visibleTasks)

  /**
   * Whether to draw the lanes yet.
   *
   * Not until the goals are in: every card would fall into the "No goal" lane
   * for as long as the request took, and the board would visibly re-sort
   * itself the moment it landed.
   */
  const inLanes = lanesOn && !goals.isPending
  const lanes = lanesFor(goalList, visibleTasks)

  /**
   * The board's last column, which is what "done" means for a card: arriving
   * there writes the card's finishing time and leaving there clears it. A
   * position rather than a name — a column called Done with a column to its
   * right is not the end of the board.
   */
  const doneColumn = columns.at(-1)

  /** The grid the header row and every lane share, so their columns line up. */
  const laneTracks = columns
    .map((column) => (collapsedColumns.includes(column.id) ? '52px' : 'minmax(280px, 400px)'))
    .join(' ')

  const tasksInLane = (laneKey: string, columnId: string) =>
    (byColumn.get(columnId) ?? []).filter((task) => (task.goal_id ?? NO_GOAL_LANE) === laneKey)

  const columnOf = (id: string) => columns.find((column) => column.id === id)
  const nameOfColumnId = (id: string) => columnOf(id)?.name ?? 'the column'
  const nameOfColumn = (id: UniqueIdentifier | undefined) =>
    // Through `columnIdOf`, because in lanes the target is a lane's cell in a
    // column rather than the column itself — and "over lane:…::uuid" is not a
    // sentence anybody wants read to them.
    typeof id === 'string' ? nameOfColumnId(columnIdOf(id)) : 'nowhere'
  const nameOfDragged = (id: UniqueIdentifier) =>
    isColumnDrag(id)
      ? nameOfColumnId(columnIdFromDrag(id))
      : (tasks.data?.find((task) => task.id === id)?.title ?? 'the card')

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
    draggingId !== null && !isColumnDrag(draggingId)
      ? (tasks.data?.find((task) => task.id === draggingId) ?? null)
      : null

  /** Greyed while a card is in the air, and empty the rest of the time. */
  const forbiddenColumns = forbiddenFor(draggedTask)

  function onToggleCollapse(column: BoardColumn) {
    toggleColumn(column.id)
    announce(
      collapsedColumns.includes(column.id)
        ? `${column.name} expanded.`
        : `${column.name} collapsed.`,
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
    setDraggingId(event.active.id)
  }

  function onDragEnd(event: DragEndEvent) {
    // Cleared here rather than in each branch below: every one of them ends
    // the drag, and an overlay left on screen is a card stuck to the pointer.
    setDraggingId(null)

    const overId = event.over?.id
    if (typeof overId !== 'string') return

    if (isColumnDrag(event.active.id)) dropColumn(columnIdFromDrag(event.active.id), overId)
    else dropCard(event.active.id, overId)
  }

  /** A column's handle let go over another column: the board is reordered. */
  function dropColumn(columnId: string, overId: string) {
    // Every drop target names a column; in lanes it names the lane as well.
    const droppedIn = columnIdOf(overId)
    moveColumnTo(
      columns.findIndex((column) => column.id === columnId),
      columns.findIndex((column) => column.id === droppedIn),
    )
  }

  /** A card let go over a drop target: a move, a change of goal, both, or nothing. */
  function dropCard(taskId: UniqueIdentifier, overId: string) {
    const task = tasks.data?.find((candidate) => candidate.id === taskId)
    if (!task) return

    // Every drop target names a column; in lanes it names the lane as well.
    const droppedIn = columnIdOf(overId)

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

    const move: Move = {
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
      setReopening({ move, task, from: doneColumn.name, to: nameOfColumnId(droppedIn) })
      return
    }

    moveTask.mutate(move)
  }

  /** What every drawing of a column is handed, whichever part of it is drawn. */
  const sharedColumnProps = (column: BoardColumn) => ({
    column,
    isFirst: column.id === firstColumn?.id,
    collapsed: collapsedColumns.includes(column.id),
    collapsedOutcomes,
    forbidden: forbiddenColumns.has(column.id),
    onToggleCollapse: () => onToggleCollapse(column),
    onToggleOutcome: toggleOutcome,
    onOpenTask: setOpenTaskId,
    onAddTask: may('tasks') ? () => setCreatingTask(true) : null,
    onEdit: () => setColumnDialog(column),
    members: memberList,
    // Settled per column, out of the column's own answer and the board's: see
    // `hidesHold`. Passed in rather than read inside `Column` because the
    // search box and the status filter are the board's, and a column that
    // reached for them would be reaching past its own props for them.
    hidingHold: hidesHold(holdShown.includes(column.id), quickStatus, parsed, quickAgent !== 'all'),
    onToggleHold: () => toggleHold(column.id),
  })

  const onMoveSubStatus = (taskId: string, index: number) => moveSubStatus.mutate({ taskId, index })
  const onSetAssignee = (taskId: string, taskRef: string, assigneeId: string) =>
    setAssignee.mutate({ taskId, taskRef, assigneeId })

  return (
    <>
      {/* No standing paragraph of instructions. What it said, the board says
          better by being used: cards enter at the first column because that is
          the only one with a "+", and a card is dragged by dragging it. The
          keyboard's share of it is the part that is not self-evident, and that
          is spoken by SCREEN_READER_INSTRUCTIONS to the people it is for. */}
      {moveTask.error ? <ErrorBanner>{moveTask.error.message}</ErrorBanner> : null}
      <LiveRegion message={message} />

      {/* One bar: what the page is, the two counts worth glancing at, then the
          search and the board's own controls. The title used to be a heading
          of its own above a toolbar of six buttons, which spent two rows of
          the window before the first card.

          The counts sit with the title rather than with the controls. They
          are a signal — how many cards wait on a person, how many have an
          agent busy — and a signal drawn the size of a button beside the
          search box was read as one more thing to press. */}
      <div className={styles.toolbar}>
        <div className={styles.titleGroup}>
          <h1 className={styles.pageTitle}>Kanban board</h1>
          <AgentChips
            value={quickAgent}
            onChange={setQuickAgent}
            needsYou={agentTally.needsYou}
            working={agentTally.working}
          />
        </div>
        <SearchBar
          query={search}
          onChange={setSearch}
          columns={columns}
          members={memberList}
          goals={goalList}
        />
        <div className={styles.tools}>
          <QuickFilters
            goals={goalList}
            goal={quickGoal}
            onGoal={setQuickGoal}
            status={quickStatus}
            onStatus={setQuickStatus}
            showCancelled={showCancelled}
            onShowCancelled={setShowCancelled}
            cancelledCount={cancelledCount}
            hiding={hidingCancelled}
          />
          {/* Columns or lanes: the same board read by column, or split into
              one lane per goal. Two segments rather than a button that says
              "on" and "off", so the board says which way it is being read. */}
          <div className={styles.segmented} role="group" aria-label="Board layout">
            <button type="button" aria-pressed={!lanesOn} onClick={() => setLanesOn(false)}>
              <ColumnsIcon />
              Columns
            </button>
            <button
              type="button"
              aria-pressed={lanesOn}
              onClick={() => setLanesOn(true)}
              title="Split the board into one lane per goal"
            >
              <LanesIcon />
              Lanes
            </button>
          </div>
          {/* Only while there are lanes to fold. Off the board, it is a button
              about nothing. */}
          {inLanes ? (
            <button
              type="button"
              className={styles.iconButton}
              onClick={onFoldAllLanes}
              aria-label={everyLaneFolded ? 'Open every lane' : 'Fold every lane'}
              title={
                everyLaneFolded ? 'Open every goal back up' : 'Fold every goal down to its heading'
              }
            >
              {everyLaneFolded ? '⌄' : '⌃'}
            </button>
          ) : null}
          {/* Both change the shape of the board rather than what is on it,
              which is one permission, and neither is drawn without it. */}
          {may('board') ? (
            <BoardMenu
              board={board.data}
              templateCount={templateList.length}
              onTemplates={() => setTemplatesOpen(true)}
              onAddColumn={() => setColumnDialog('new')}
            />
          ) : null}
        </div>
      </div>

      <BoardAnnounce.Provider value={announce}>
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
          onDragCancel={() => setDraggingId(null)}
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
                    {...sharedColumnProps(column)}
                    tasks={byColumn.get(column.id) ?? []}
                    onMoveSubStatus={() => {}}
                    onSetAssignee={() => {}}
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
                        (total, column) => total + tasksInLane(key, column.id).length,
                        0,
                      )}
                      folded={laneFolded}
                      rowId={`lane-${key}`}
                      onToggleFold={() => onToggleLane(key, name)}
                      onAdd={
                        may('tasks')
                          ? () => {
                              setNewTaskGoalId(goal?.id ?? null)
                              setCreatingTask(true)
                            }
                          : null
                      }
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
                            {...sharedColumnProps(column)}
                            tasks={tasksInLane(key, column.id)}
                            onMoveSubStatus={onMoveSubStatus}
                            onSetAssignee={onSetAssignee}
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
                  {...sharedColumnProps(column)}
                  tasks={byColumn.get(column.id) ?? []}
                  onMoveSubStatus={onMoveSubStatus}
                  onSetAssignee={onSetAssignee}
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
      </BoardAnnounce.Provider>

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
            moveTask.mutate(reopening.move)
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
          defaultGoalId={newTaskGoalId}
          announce={announce}
          onDone={refresh}
          onClose={() => {
            setCreatingTask(false)
            setNewTaskGoalId(null)
          }}
        />
      ) : null}

      {/* One dialog at a time, never stacked: the sub-task's card needs every
          field a card needs, and a form drawn on top of the form it came from
          is two Save buttons with no way to tell which one is which. */}
      {subtaskParentRef && firstColumn ? (
        <TaskDialog
          projectKey={projectKey}
          taskId={null}
          parentRef={subtaskParentRef}
          columns={columns}
          firstColumn={firstColumn}
          templates={templateList}
          goals={goalList}
          announce={announce}
          onDone={refresh}
          onClose={() => setSubtaskParentRef(null)}
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
            setSubtaskParentRef(parentRef)
          }}
          onDone={refresh}
          onClose={() => setOpenTaskId(null)}
        />
      ) : null}
    </>
  )
}

/**
 * The board's search box: free text plus `col:`, `who:`, `blk:`, `hld:` and
 * `cnl:` tags (see `boardSearch.ts`). Typing `col:` or `who:` opens a suggestion
 * list of the matching columns or people — arrow keys to move through it,
 * enter or a click to accept, escape to dismiss it without losing the token
 * being typed.
 *
 * A query long enough to be worth tagging is long enough to be a nuisance to
 * select and delete, so once there is anything in the box an × sits at its
 * right to empty it in one go. It appears only when there is something to
 * clear — an × over an empty box is a control that does nothing — and hands
 * focus back to the input, because clearing the search is almost always the
 * start of the next one rather than the end of searching.
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
  const [focused, setFocused] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const suggestions = dismissed ? [] : suggestionsFor(activeToken(query), columns, members, goals)

  function pick(suggestion: Suggestion) {
    onChange(applySuggestion(query, suggestion))
    setHighlighted(0)
  }

  /** The suggestion list's keys: up and down through it, enter to take one, escape to shut it. */
  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
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
  }

  function clear() {
    onChange('')
    // The list is keyed off the token being typed, so emptying the box closes
    // it anyway; the reset is for the next query, which would otherwise open
    // its list still dismissed and with a stale row highlighted.
    setDismissed(false)
    setHighlighted(0)
    input.current?.focus()
  }

  return (
    <div className={styles.search}>
      <input
        ref={input}
        type="text"
        className={query ? styles.clearable : undefined}
        value={query}
        onChange={(event) => {
          onChange(event.target.value)
          setDismissed(false)
          setHighlighted(0)
        }}
        onKeyDown={onKeyDown}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        placeholder="Search cards"
        aria-label="Search tasks"
        aria-autocomplete="list"
        aria-expanded={suggestions.length > 0}
      />
      {query ? (
        <button
          type="button"
          className={styles.clearSearch}
          aria-label="Clear the search"
          // Both, and each for its own reason: mousedown to swallow the focus
          // the pointer would otherwise take off the input, click to do the
          // work — a keyboard press on the button raises click and no
          // mousedown at all, and clearing on mousedown alone leaves the ×
          // dead to everyone tabbing to it.
          onMouseDown={(event) => event.preventDefault()}
          onClick={clear}
        >
          ×
        </button>
      ) : null}
      {/* The tags, offered while the box is empty and waiting. They used to
          be the placeholder, which made the box a line of syntax to read
          past every visit; here they are a list to pick from the once you
          want one, and gone the moment you type. */}
      {focused && !query && !dismissed ? (
        <ul className={styles.suggestions} aria-label="Search tags">
          {SEARCH_TAGS.map((tag) => (
            <li key={tag.token}>
              <button
                type="button"
                tabIndex={-1}
                onMouseDown={(event) => {
                  event.preventDefault()
                  onChange(tag.token)
                }}
              >
                <code className={styles.tagToken}>{tag.token}</code>
                <span className={styles.tagSays}>{tag.says}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
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

/** The tags the search box understands, as offered under it — see `boardSearch.ts`. */
const SEARCH_TAGS = [
  { token: 'col:', says: 'in a column' },
  { token: 'who:', says: 'owned by someone' },
  { token: 'goal:', says: 'on a goal' },
  { token: 'blk:', says: 'blocked' },
  { token: 'hld:', says: 'on hold' },
  { token: 'cnl:', says: 'cancelled' },
] as const

const SUGGESTION_KINDS: Record<Suggestion['kind'], string> = {
  column: 'Column',
  assignee: 'Assignee',
  goal: 'Goal',
}

/**
 * Open and shut for a popover anchored under its button: shut again by a
 * press anywhere outside it or by Escape, which hands focus back to the
 * button — the place a keyboard user opened it from.
 */
function usePopover() {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const button = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    function onPointerDown(event: PointerEvent) {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false)
    }
    function onKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key !== 'Escape') return
      setOpen(false)
      button.current?.focus()
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  return { open, setOpen, root, button }
}

/**
 * Goal, status and the cancelled hide, narrowing the board — in a popover
 * under one button rather than sitting on the toolbar all the time.
 *
 * All of it ANDed onto whatever the search box is already narrowing to — see
 * the `.filter` calls around `visibleTasks`. Closing it does not clear it: a
 * filter left on while the popover is shut is still doing its job, which is
 * what the number on the button is for.
 *
 * The cancelled hide is a switch rather than a third list because it has one
 * question and two answers, and because it is the one of the three the board
 * arrives with already set: the status list asks "show me only these", the
 * hide asks "stop showing me those", and it is on from the first visit — which
 * is why it says how many it is holding back, and why it says so even while it
 * is standing down. The cards on hold are not here at all; they are held back
 * and let out at the foot of each column, because which of them matters is a
 * question about one column rather than about the board.
 */
function QuickFilters({
  goals,
  goal,
  onGoal,
  status,
  onStatus,
  showCancelled,
  onShowCancelled,
  cancelledCount,
  hiding,
}: {
  goals: Goal[]
  goal: QuickGoal
  onGoal: (goal: QuickGoal) => void
  status: QuickStatus
  onStatus: (status: QuickStatus) => void
  /** Whether the reader has asked for the cancelled cards back. Off by
   * default, which is the hide being on. */
  showCancelled: boolean
  onShowCancelled: (show: boolean) => void
  /** How many cancelled cards the board is holding, before the hide is
   * applied. */
  cancelledCount: number
  /** Whether the hide is actually in force — see `hidesCancelled`. */
  hiding: boolean
}) {
  const { open, setOpen, root, button } = usePopover()
  // The cancelled hide is on by default, so it is not what makes the filter
  // worth looking in — the number counts what somebody changed, and the
  // ordinary board would otherwise wear one permanently.
  const active = [goal !== 'all', status !== 'all', showCancelled].filter(Boolean).length
  const goalOptions: { value: QuickGoal; name: string; colour: string | null }[] = [
    { value: 'all', name: 'All goals', colour: null },
    { value: 'none', name: 'No goal', colour: null },
    ...goals.map((candidate) => ({
      value: candidate.id,
      name: candidate.name,
      colour: candidate.colour,
    })),
  ]

  return (
    <div className={styles.popRoot} ref={root}>
      <button
        ref={button}
        type="button"
        className={`${styles.toolButton} ${open ? styles.toolButtonOpen : ''}`}
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen(!open)}
        title={open ? 'Hide the filters' : 'Filter the board by goal or status'}
      >
        <FilterIcon />
        Filter
        {active ? (
          <span className={styles.toolBadge}>
            {active}
            <span className="visually-hidden"> on</span>
          </span>
        ) : null}
      </button>
      {open ? (
        <div
          className={`${styles.popover} ${styles.filterPopover}`}
          role="dialog"
          aria-label="Filter"
        >
          <div className={styles.popHead}>
            <span className={styles.popTitle}>Filter</span>
            {active ? (
              <button
                type="button"
                className={styles.popLink}
                onClick={() => {
                  onGoal('all')
                  onStatus('all')
                  onShowCancelled(false)
                }}
              >
                Clear
              </button>
            ) : null}
          </div>

          <div className={styles.popLabel} id="filter-goal">
            Goal
          </div>
          <div className={styles.optionList} role="group" aria-labelledby="filter-goal">
            {goalOptions.map((option) => (
              <button
                key={option.value}
                type="button"
                className={styles.option}
                aria-pressed={goal === option.value}
                onClick={() => onGoal(option.value)}
              >
                <span
                  className={styles.optionSwatch}
                  style={option.colour ? { background: option.colour } : undefined}
                  aria-hidden="true"
                />
                <span className={styles.optionName}>{option.name}</span>
                {goal === option.value ? <TickIcon /> : null}
              </button>
            ))}
          </div>

          <div className={styles.popLabel} id="filter-status">
            Status
          </div>
          <div className={styles.statusPicks} role="group" aria-labelledby="filter-status">
            {(['all', ...Object.keys(STATUS_LABELS)] as QuickStatus[]).map((candidate) => (
              <button
                key={candidate}
                type="button"
                className={styles.statusPick}
                aria-pressed={status === candidate}
                onClick={() => onStatus(candidate)}
              >
                {candidate === 'all' ? 'All' : STATUS_LABELS[candidate]}
              </button>
            ))}
          </div>

          <div className={styles.popRule} />

          {/* Pressed, but not necessarily doing anything: `hiding` is what the
              board actually did, and the two part company when the status
              list is asking for the very cards this puts away. The switch
              stays on and says so in its own words rather than silently
              turning itself off — the setting is still on, it is just being
              overruled by the more specific question next to it. */}
          <button
            type="button"
            className={styles.switchRow}
            role="switch"
            aria-checked={!showCancelled}
            onClick={() => onShowCancelled(!showCancelled)}
          >
            <span className={styles.switchText}>
              Hide cancelled
              <span className={styles.switchHint}>
                {cancelledHint(hiding, showCancelled, cancelledCount)}
              </span>
            </span>
            <span className={styles.switch} aria-hidden="true" />
          </button>
        </div>
      ) : null}
    </div>
  )
}

/**
 * "Needs you" and "working" (CYLIST-90): the two questions a board with agents
 * on it gets asked most, as two small counts beside the page's title.
 *
 * Small and quiet on purpose: a dot, a number and two words, with no fill
 * until one is pressed. They are worth a glance, not a toolbar's worth of
 * chrome — and drawn the size of buttons beside the search box they were read
 * as the board's main controls. A zero goes grey rather than away, so the
 * pair never moves the title.
 *
 * Pressing one narrows the board to those cards, ANDed with the search box and
 * the filter like every other filter; pressing it again, or the other one,
 * lets go. One at a time, because "needs you or working" is just "has a live
 * agent", which the borders already show.
 *
 * The counts move with the board's own socket: a `board.changed` refetches the
 * cards and these are counted off them, so there is no polling of their own.
 */
function AgentChips({
  value,
  onChange,
  needsYou,
  working,
}: {
  value: AgentFilter
  onChange: (value: AgentFilter) => void
  needsYou: number
  working: number
}) {
  const chips = [
    {
      key: 'needs_you' as const,
      label: 'need you',
      count: needsYou,
      title: 'Only the cards whose agent is waiting on a person',
      className: styles.needsChip,
    },
    {
      key: 'working' as const,
      label: 'working',
      count: working,
      title: 'Only the cards whose agent is working',
      className: styles.workingChip,
    },
  ]

  return (
    <div className={styles.agentChips} role="group" aria-label="Agent filters">
      {chips.map((chip) => {
        const on = value === chip.key
        return (
          <button
            key={chip.key}
            type="button"
            aria-pressed={on}
            className={[
              styles.agentChip,
              chip.className,
              chip.count > 0 ? styles.agentChipLive : '',
              on ? styles.agentChipOn : '',
            ]
              .filter(Boolean)
              .join(' ')}
            title={on ? 'Show every card again' : chip.title}
            onClick={() => onChange(on ? 'all' : chip.key)}
          >
            <span className={styles.agentChipDot} aria-hidden="true" />
            <span className={styles.agentChipCount}>{chip.count}</span>
            {chip.label}
          </button>
        )
      })}
    </div>
  )
}

/**
 * What the cancelled hide's button says beside its name: how many cards it is
 * holding back, that it has been overruled, or — once it is off — how many it
 * has let through, which is the number the reader turned it off to see.
 */
function cancelledHint(hiding: boolean, showCancelled: boolean, cancelledCount: number) {
  if (hiding) return `${cancelledCount} hidden`
  if (!showCancelled) return 'overruled by the status'
  return `${cancelledCount} shown`
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
  /** Null where the reader's role does not allow cards — see `Column`. */
  onAdd: (() => void) | null
}) {
  const name = goal?.name ?? 'No goal'
  // A settled goal is only in lanes at all while cards are still on it — see
  // `lanesFor`. Those cards are work that has outlived the goal they were
  // written under, which is the one thing about the lane worth saying, so the
  // heading is drawn back and carries the pill rather than reading exactly
  // like the lanes still being worked on.
  const settled = goal !== null && goal.status !== 'open'

  return (
    <div
      className={[
        styles.laneHead,
        goal ? '' : styles.laneHeadLoose,
        settled ? styles.laneSettled : '',
      ]
        .filter(Boolean)
        .join(' ')}
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
        <>
          <Link
            to="/p/$projectKey/goals/$goalRef"
            params={{ projectKey, goalRef: goal.reference }}
            className={styles.laneName}
          >
            <span className={styles.laneDot} aria-hidden="true" />
            {goal.name}
            <span className={styles.laneRef}>{goal.reference}</span>
          </Link>
          <GoalStatusPill status={goal.status} />
        </>
      ) : (
        <span className={styles.laneName}>
          <span className={styles.laneDot} aria-hidden="true" />
          No goal
        </span>
      )}
      <span className={styles.count}>{count}</span>
      {onAdd ? (
        <Button
          variant="ghost"
          small
          onClick={onAdd}
          aria-label={goal ? `Add a task on ${goal.name}` : 'Add a task on no goal'}
        >
          + Task
        </Button>
      ) : null}
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
  onSetAssignee,
  onAddTask,
  onEdit,
  members,
  hidingHold,
  onToggleHold,
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
  /** Hand a card to someone else, by their id, from the board itself. */
  onSetAssignee: (taskId: string, taskRef: string, assigneeId: string) => void
  /**
   * Open the whole form, which is where a card is written. Null for a reader
   * whose role does not allow cards, which is what takes the "+" off the
   * column rather than leaving one that refuses.
   */
  onAddTask: (() => void) | null
  onEdit: () => void
  /** The project's people, for the `@` tags in a stage label. */
  members: Person[]
  /**
   * Whether this column is keeping its on-hold cards back — decided by
   * `hidesHold` out of this column's own foot and what the board is being
   * asked. False means they are drawn in place, wherever in the stack they sit.
   */
  hidingHold: boolean
  /** Open the column's foot, or shut it again. */
  onToggleHold: () => void
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
    id: `${COLUMN_DRAG_PREFIX}${column.id}`,
    // A lane's cells are not the column, they are one lane's share of it —
    // and eight copies of a column all claiming to be draggable is eight
    // things dnd-kit would have to be told apart by.
    disabled: part === 'cards',
  })
  const counted = `${tasks.length} ${tasks.length === 1 ? 'card' : 'cards'}`
  /**
   * The cards this column draws, and the ones it is sitting on.
   *
   * `counted` above stays the whole column either way: the heading is the
   * column's size, the foot is what of it is put down, and the two together
   * are why the gap between the heading's number and the cards you can count
   * is never a surprise.
   */
  const { shown, onHold } = splitOnHold(tasks, hidingHold)

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
        <ColumnRail
          key="rail"
          column={column}
          count={tasks.length}
          onExpand={onToggleCollapse}
          // The first column is where new work lands, so its "+" survives the
          // fold: otherwise tidying the board away takes the add button with
          // it.
          onAddTask={isFirst ? onAddTask : null}
        />
      ) : (
        <div key="open" className={styles.unfolded}>
          {part === 'cards' ? null : (
            <ColumnHead
              column={column}
              count={tasks.length}
              dragAttributes={attributes}
              dragListeners={listeners}
              onCollapse={onToggleCollapse}
              onEdit={onEdit}
            />
          )}

          {part === 'head' ? null : (
            <ColumnCards
              column={column}
              tasks={shown}
              dropId={dropId}
              members={members}
              collapsedOutcomes={collapsedOutcomes}
              onToggleOutcome={onToggleOutcome}
              onOpenTask={onOpenTask}
              onMoveSubStatus={onMoveSubStatus}
              onSetAssignee={onSetAssignee}
            />
          )}

          {/* The foot, under the cards and above the composer: the held work
              is the bottom of this column, so the line that lets it out is
              too. Drawn only when there is something behind it — a column
              with nothing on hold has nothing to say here, and a row of "0 on
              hold" down an untroubled board is noise on every column at
              once. */}
          {part !== 'head' && onHold.length > 0 ? (
            <button
              type="button"
              className={styles.holdFoot}
              aria-expanded={!hidingHold}
              onClick={onToggleHold}
              title={
                hidingHold
                  ? `Show the ${onHold.length === 1 ? 'card' : 'cards'} on hold in ${column.name}`
                  : `Hide the ${onHold.length === 1 ? 'card' : 'cards'} on hold in ${column.name}`
              }
            >
              <span aria-hidden="true">{hidingHold ? '+' : '−'}</span>
              {onHold.length} on hold
            </button>
          ) : null}

          {isFirst && part === 'all' && onAddTask ? (
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

/** A column folded down to a rail: its count and its name, turned on their side. */
function ColumnRail({
  column,
  count,
  onExpand,
  onAddTask,
}: {
  column: BoardColumn
  count: number
  onExpand: () => void
  /** Null unless the rail keeps the column's "+". */
  onAddTask: (() => void) | null
}) {
  return (
    <div className={styles.rail}>
      <button
        type="button"
        className={styles.railBody}
        aria-expanded={false}
        aria-label={`Expand ${column.name}`}
        onClick={onExpand}
      >
        <span aria-hidden="true">›</span>
        <span className={styles.count}>{count}</span>
        <span className={styles.railName}>{column.name}</span>
      </button>
      {onAddTask ? (
        <button className={styles.railAdd} onClick={onAddTask} aria-label="Add a task">
          +
        </button>
      ) : null}
    </div>
  )
}

/**
 * An open column's heading: the handle it is reordered by, its name and
 * count, and its fold and edit buttons.
 *
 * The drag wiring is handed in rather than set up here, because it belongs to
 * the whole column — see `Column` — and only the handle carries it.
 */
function ColumnHead({
  column,
  count,
  dragAttributes,
  dragListeners,
  onCollapse,
  onEdit,
}: {
  column: BoardColumn
  count: number
  dragAttributes: DraggableAttributes
  dragListeners: DraggableSyntheticListeners
  onCollapse: () => void
  onEdit: () => void
}) {
  return (
    <div className={styles.head}>
      <div className={styles.headRow}>
        <button
          type="button"
          className={styles.dragHandle}
          {...dragAttributes}
          {...dragListeners}
          aria-label={`Reorder ${column.name}. Press space to pick up, then the left and right arrow keys to move it.`}
          title="Drag to reorder"
        >
          ⠿
        </button>
        <h3>{column.name}</h3>
        <span className={styles.headActions}>
          <span className={styles.count}>{count}</span>
          <Button
            variant="ghost"
            small
            aria-expanded
            aria-label={`Collapse ${column.name}`}
            onClick={onCollapse}
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
  )
}

/**
 * The cards in an open column, or in one lane's share of it.
 *
 * A divided column is still one column: one heading, one count, one place on
 * the board. What it has instead of a single stack of cards is a stack per way
 * the work can have ended, each its own drop target, so which one a card is in
 * is something you set by putting it there.
 */
function ColumnCards({
  column,
  tasks,
  dropId,
  members,
  collapsedOutcomes,
  onToggleOutcome,
  onOpenTask,
  onMoveSubStatus,
  onSetAssignee,
}: {
  column: BoardColumn
  tasks: Task[]
  /** The column's own drop target, which each section's id is built on. */
  dropId: string
  members: Person[]
  collapsedOutcomes: string[]
  onToggleOutcome: (id: string) => void
  onOpenTask: (taskId: string) => void
  onMoveSubStatus: (taskId: string, index: number) => void
  onSetAssignee: (taskId: string, taskRef: string, assigneeId: string) => void
}) {
  if (!column.outcomes.length) {
    return (
      <CardList
        tasks={tasks}
        members={members}
        onOpenTask={onOpenTask}
        onMoveSubStatus={onMoveSubStatus}
        onSetAssignee={onSetAssignee}
      />
    )
  }

  return (
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
            onSetAssignee={onSetAssignee}
          />
        )
      })}
    </div>
  )
}

/** A stack of cards, top to bottom, as a column or a section of one holds them. */
function CardList({
  tasks,
  members,
  onOpenTask,
  onMoveSubStatus,
  onSetAssignee,
}: {
  tasks: Task[]
  members: Person[]
  onOpenTask: (taskId: string) => void
  onMoveSubStatus: (taskId: string, index: number) => void
  onSetAssignee: (taskId: string, taskRef: string, assigneeId: string) => void
}) {
  return (
    <div className={styles.cards}>
      {tasks.map((task) => (
        <TaskCard
          key={task.id}
          task={task}
          members={members}
          onOpen={() => onOpenTask(task.id)}
          onOpenTask={onOpenTask}
          onMoveSubStatus={(index) => onMoveSubStatus(task.id, index)}
          onSetAssignee={(assigneeId) => onSetAssignee(task.id, task.reference, assigneeId)}
        />
      ))}
    </div>
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
  onSetAssignee,
}: {
  label: string
  dropId: string
  tasks: Task[]
  members: Person[]
  collapsed: boolean
  onToggleCollapse: () => void
  onOpenTask: (taskId: string) => void
  onMoveSubStatus: (taskId: string, index: number) => void
  onSetAssignee: (taskId: string, taskRef: string, assigneeId: string) => void
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
        <CardList
          tasks={tasks}
          members={members}
          onOpenTask={onOpenTask}
          onMoveSubStatus={onMoveSubStatus}
          onSetAssignee={onSetAssignee}
        />
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
  p0: 'P0 — drop what you are doing',
  p1: 'P1 — as soon as P0 is clear',
  p2: 'P2 — this week',
  p3: 'P3 — some time',
} as const

function TaskCard({
  task,
  members,
  onOpen,
  onOpenTask,
  onMoveSubStatus,
  onSetAssignee,
}: {
  task: Task
  members: Person[]
  onOpen: () => void
  /** Opens another card's dialog — one of this card's sub-tasks. */
  onOpenTask: (taskId: string) => void
  onMoveSubStatus: (index: number) => void
  onSetAssignee: (assigneeId: string) => void
}) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({ id: task.id })
  const now = new Date()
  const viewerId = useViewerId()
  const agent = agentIndicator(task.agent_session, now, viewerId)
  // A question or permission waiting on this card opens a popover under it on
  // hover (CYLIST-86) — never while anything on the board is being dragged.
  const prompt = task.agent_session?.prompt ?? null
  const answerable = isAnswerable(prompt, now)
  const { active } = useDndContext()
  const popover = useCardPopover({ answerable, dragging: active !== null })
  const setCardRef = useCallback(
    (element: HTMLElement | null) => {
      setNodeRef(element)
      popover.cardRef.current = element
    },
    [setNodeRef, popover.cardRef],
  )

  return (
    <>
      <article
        ref={setCardRef}
        {...attributes}
        {...listeners}
        {...popover.card}
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
          // The border is colour and motion alone, so the words go here too.
          agent ? [agent.label, agent.detail].filter(Boolean).join(', ') : null,
          // Said, because the popover that opens on focus is otherwise news.
          answerable ? 'its choices follow the card' : null,
        ]
          .filter(Boolean)
          .join(', ')}
        // The rail's colour, when the card is on a goal. Read by `.task::before`
        // through a fallback, so a card on no goal keeps the status colour the
        // rail has always been — see ProjectBoard.module.css.
        style={
          task.goal_colour ? ({ '--card-rail': task.goal_colour } as CSSProperties) : undefined
        }
        // No transform of its own: while this card is in the air the DragOverlay
        // is the copy that follows the pointer, and this one stays put and dims.
        className={[
          styles.task,
          styles[`type_${task.type}`],
          styles[task.status],
          // The agent channel is the outer border, which nothing else on the
          // card uses — the rail is already contested three ways.
          agent && styles[agent.className],
          isDragging && styles.dragging,
        ]
          .filter(Boolean)
          .join(' ')}
      >
        <TaskCardBody
          task={task}
          members={members}
          onMoveSubStatus={onMoveSubStatus}
          onSetAssignee={onSetAssignee}
          onOpenTask={onOpenTask}
        />
      </article>
      {popover.open && answerable ? (
        <CardPromptPopover
          taskRef={task.reference}
          prompt={prompt}
          cardRef={popover.cardRef}
          popoverRef={popover.popoverRef}
          handlers={popover.popover}
          onAnswered={popover.onAnswered}
        />
      ) : null}
    </>
  )
}

/**
 * The date a card is being asked for now, and the column it is wanted in by
 * then — nulls for a card that owes nothing.
 */
function owedDateOf(task: Task) {
  /* Cancelling the work is what stops it owing anybody a date, and finishing
     it does the same — a card already in the board's last column has nothing
     left to be late for. A red "12d late" shouting from a card that is done,
     same as one nobody is going to do, is the loudest wrong thing the board
     could say. */
  const dated = task.status !== 'cancelled' && task.finished_at === null
  /* The next date owed rather than the last column's: a card wanted in Review
     by Friday is late on Saturday, whatever the end of the board says. The
     server settles a date once the card reaches the column it was for, so a
     card that has arrived stops being asked for it — and a card in the last
     column, which is done, owes nothing at all. */
  const owed = dated ? task.next_due_date : null
  const stage =
    task.column_due_dates.find((entry) => !entry.met && entry.due_date === owed)?.column_name ??
    null
  return { owed, stage }
}

/**
 * The marks on a card that open into what they stand for: the pull-request
 * mark into the list of them, the stage bar into the stages, the sub-task
 * count into the sub-tasks.
 */
type CardFocus = 'prs' | 'stages' | 'subs'

/** How long a mark has to be rested on before it opens: long enough that
 * passing over one on the way to another does nothing. */
const MARK_REST_MS = 200

/** How long an open list waits after the pointer leaves before it shuts —
 * long enough to travel from one mark to the next, so the slot goes straight
 * from one list to the other instead of dropping back to the facts between. */
const MARK_GRACE_MS = 350

/**
 * Which of those marks has opened its list, and which is waiting to.
 *
 * A mark opens only after the pointer has rested on it, never as it passes:
 * the stage bar and the sub-task count sit on the row above the pull-request
 * mark, and a mark that opened on contact opened on the way down to the one
 * below it. Focus opens at once, because a keyboard does not pass over
 * anything, and so does a click — for a pointer that cannot hover. A click
 * never shuts: the pointer that clicked has usually just watched the list
 * open, and shutting it then would undo what it came for. Leaving shuts.
 *
 * Whatever opens goes into the one slot under the card's bottom line, and the
 * slot holds itself open while the pointer is in it, so its rows can be used.
 */
function useCardFocus() {
  const [focus, setFocus] = useState<CardFocus | null>(null)
  const [arming, setArming] = useState<CardFocus | null>(null)
  // Each list mounts the first time it opens and stays, so it has something
  // to slide out with when the next one takes the slot.
  const [opened, setOpened] = useState<ReadonlySet<CardFocus>>(() => new Set())
  const openTimer = useRef<number | undefined>(undefined)
  const closeTimer = useRef<number | undefined>(undefined)
  useEffect(
    () => () => {
      window.clearTimeout(openTimer.current)
      window.clearTimeout(closeTimer.current)
    },
    [],
  )

  const show = useCallback((which: CardFocus | null) => {
    window.clearTimeout(openTimer.current)
    window.clearTimeout(closeTimer.current)
    setArming(null)
    setFocus(which)
    if (which) setOpened((seen) => (seen.has(which) ? seen : new Set(seen).add(which)))
  }, [])
  const arm = (which: CardFocus) => {
    window.clearTimeout(openTimer.current)
    window.clearTimeout(closeTimer.current)
    if (focus === which) return
    setArming(which)
    openTimer.current = window.setTimeout(() => show(which), MARK_REST_MS)
  }
  const letGo = () => {
    window.clearTimeout(openTimer.current)
    setArming(null)
    closeTimer.current = window.setTimeout(() => setFocus(null), MARK_GRACE_MS)
  }
  const keep = () => window.clearTimeout(closeTimer.current)

  /** What a mark spreads onto itself. */
  const mark = (which: CardFocus) => ({
    onPointerEnter: () => arm(which),
    onPointerLeave: letGo,
    onFocus: () => show(which),
    onBlur: letGo,
    onPick: () => show(which),
  })
  /** What the slot under the bottom line spreads onto itself. */
  const slot = { onPointerEnter: keep, onPointerLeave: letGo, onFocus: keep, onBlur: letGo }

  return { focus, arming, opened, mark, slot }
}

/** The handlers `useCardFocus` hands a mark. */
type MarkHandlers = ReturnType<ReturnType<typeof useCardFocus>['mark']>

/** A mark's own events, without the click it takes for itself. */
function markEvents(handlers: MarkHandlers) {
  const { onPointerEnter, onPointerLeave, onFocus, onBlur } = handlers
  return { onPointerEnter, onPointerLeave, onFocus, onBlur }
}

/** A mark's classes: open, or filling its hairline while it waits to open. */
function markClass(kind: string | undefined, open: boolean, arming: boolean) {
  return [styles.mark, kind, open && styles.markOpen, arming && styles.markArming]
    .filter(Boolean)
    .join(' ')
}

/**
 * What a card says, without any of what a card does.
 *
 * Drawn twice while a card is being dragged — dimmed in the column it came
 * from, and solid in the overlay — so it lives apart from the drag wiring that
 * only the one in the column has. The callbacks are optional because the
 * overlay's copy has nothing to click: it is a picture of a card moving.
 *
 * The face is what every card in a column is compared on, and nothing else:
 * the title and its priority, how far through its stages and sub-tasks it is,
 * and one line of which card it is, when it is owed, where it is tracked and
 * whose it is. Under that is one slot, and only one: the card's other facts
 * after a short rest on the card, or — after a short rest on a mark — the
 * stages, the sub-tasks or the pull requests. Nothing that opens ever takes
 * the place of a row, so nothing the pointer is aiming at moves. The goal is
 * the rail down the card's left edge, named when the rail is pointed at.
 */
function TaskCardBody({
  task,
  members,
  onMoveSubStatus,
  onSetAssignee,
  onOpenTask,
}: {
  task: Task
  members: Person[]
  onMoveSubStatus?: (index: number) => void
  /** Undefined for the overlay's copy — see `onMoveSubStatus`. */
  onSetAssignee?: (assigneeId: string) => void
  /** Opens a sub-task's own dialog. Undefined for the overlay's copy. */
  onOpenTask?: (taskId: string) => void
}) {
  const { owed, stage } = owedDateOf(task)
  const viewerId = useViewerId()
  const agent = agentIndicator(task.agent_session, new Date(), viewerId)
  const { focus, arming, opened, mark, slot } = useCardFocus()
  const staged = task.sub_statuses.length > 0
  // While an agent is on the card, the line saying what it is doing takes the
  // place of the stages: that is the progress worth reading right then, and
  // the two together were a card made of status lines.
  const progress = !agent && (staged || task.subtask_count > 0)

  return (
    <div className={[styles.cardBody, focus && styles[`focus_${focus}`]].filter(Boolean).join(' ')}>
      {/* Named on the rail rather than written on the card. The rail is the
          goal already, and one card that says so teaches the column. */}
      {task.goal_name && !agent ? (
        <span className={styles.goalHit} aria-hidden="true">
          <span className={styles.goalTab}>{task.goal_name}</span>
        </span>
      ) : null}

      <div className={styles.headLine}>
        <div className={styles.title}>{task.title}</div>
        <PriorityMark priority={task.priority} />
      </div>

      {agent ? <AgentLine session={task.agent_session} /> : null}

      {progress ? (
        <div className={styles.progress}>
          {staged ? (
            <StageMark
              task={task}
              onMove={onMoveSubStatus}
              open={focus === 'stages'}
              arming={arming === 'stages'}
              handlers={mark('stages')}
            />
          ) : null}
          {task.subtask_count ? (
            <SubtaskMark
              task={task}
              open={focus === 'subs'}
              arming={arming === 'subs'}
              handlers={mark('subs')}
            />
          ) : null}
        </div>
      ) : null}

      <div className={styles.faceLine}>
        <span className={styles.reference}>{task.reference}</span>
        <DueMark iso={owed} column={stage} />
        {task.jira_ref ? <TaskRef kind="jira" value={task.jira_ref} compact /> : null}
        {task.pr_refs.length ? (
          <PrMark
            refs={task.pr_refs}
            open={focus === 'prs'}
            arming={arming === 'prs'}
            handlers={mark('prs')}
          />
        ) : null}
        <span className={styles.faceGap} />
        {onSetAssignee ? (
          <AssigneePicker task={task} members={members} onSetAssignee={onSetAssignee} />
        ) : (
          <Avatar name={task.assignee.name} colour={task.assignee.colour} small />
        )}
      </div>

      {/* The one slot. Opened by a short rest on the card rather than the
          moment the pointer crosses it — a column you are only moving across
          should not ripple — or at once by a mark that has opened. Every list
          shares the one cell, in the order of the marks, so switching from
          one to another slides the way the pointer moved. */}
      <div className={styles.more} {...slot}>
        <div className={styles.slotStack}>
          <div className={`${styles.slotPane} ${styles.factsPane}`}>
            <div className={styles.paneInner}>
              <CardFacts task={task} />
            </div>
          </div>
          {staged && progress ? (
            <div className={`${styles.slotPane} ${styles.stagesPane}`}>
              <div className={styles.paneInner}>
                <StageList task={task} members={members} onMove={onMoveSubStatus} />
              </div>
            </div>
          ) : null}
          {task.subtask_count && progress && opened.has('subs') ? (
            <div className={`${styles.slotPane} ${styles.subsPane}`}>
              <div className={styles.paneInner}>
                <SubtaskList task={task} onOpenTask={onOpenTask} />
              </div>
            </div>
          ) : null}
          {task.pr_refs.length ? (
            <div className={`${styles.slotPane} ${styles.prsPane}`}>
              <div className={styles.paneInner}>
                <PrList refs={task.pr_refs} />
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  )
}

/**
 * The priority, as its glyph and its name. P3 is the baseline every card
 * starts on, so flagging it too would be noise on every single card.
 */
function PriorityMark({ priority }: { priority: Task['priority'] }) {
  if (priority === 'p3') return null

  return (
    <span
      className={`${styles.prio} ${styles[`priority_${priority}`]}`}
      title={PRIORITY_LABELS[priority]}
    >
      <PriorityIcon priority={priority} size={12} />
      <span aria-hidden="true">{priority.toUpperCase()}</span>
      <span className="visually-hidden">{PRIORITY_LABELS[priority]}</span>
    </span>
  )
}

/**
 * The stage a card is on, as a short bar and its name — and the control that
 * moves it, from the keyboard. Pointed at, it opens the stages themselves
 * (see `StageList`), which is where a pointer moves it.
 *
 * Every key and press stops here: the card around this is dnd-kit's drag
 * handle and the button that opens the dialog, and an Enter left to bubble
 * would open a task the reader was only stepping through.
 */
function StageMark({
  task,
  onMove,
  open,
  arming,
  handlers,
}: {
  task: Task
  onMove: ((index: number) => void) | undefined
  open: boolean
  arming: boolean
  handlers: MarkHandlers
}) {
  const labels = task.sub_statuses
  const index = task.sub_status_index ?? 0
  const current = labels[index] ?? ''
  const done = index === labels.length - 1

  return (
    <span
      className={markClass(styles.stageMark, open, arming)}
      role={onMove ? 'slider' : undefined}
      tabIndex={onMove ? 0 : undefined}
      aria-label={onMove ? 'Stage' : undefined}
      aria-valuemin={onMove ? 1 : undefined}
      aria-valuemax={onMove ? labels.length : undefined}
      aria-valuenow={onMove ? index + 1 : undefined}
      aria-valuetext={`Stage ${index + 1} of ${labels.length}: ${current}`}
      aria-expanded={open}
      {...markEvents(handlers)}
      onPointerDown={(event) => event.stopPropagation()}
      onClick={(event) => {
        event.stopPropagation()
        handlers.onPick()
      }}
      onKeyDown={(event) => {
        if (!onMove) return
        event.stopPropagation()
        const to = {
          ArrowLeft: index - 1,
          ArrowDown: index - 1,
          ArrowRight: index + 1,
          ArrowUp: index + 1,
          Home: 0,
          End: labels.length - 1,
        }[event.key]
        if (to === undefined) return
        event.preventDefault()
        if (to !== index && to >= 0 && to < labels.length) onMove(to)
      }}
    >
      <span className={styles.stageTrack} aria-hidden="true">
        {labels.map((label, position) => (
          <span
            key={`${position}-${label}`}
            className={[
              styles.stageStep,
              (position < index || (position === index && done)) && styles.stageStepDone,
              position === index && !done && styles.stageStepHere,
            ]
              .filter(Boolean)
              .join(' ')}
          />
        ))}
      </span>
      <span className={styles.stageName}>{current}</span>
      <span className={styles.stageCount}>
        {index + 1}/{labels.length}
      </span>
    </span>
  )
}

/**
 * Every stage on one line, where the bar was a picture of them: done, here,
 * and still to come. Each is a button while the card can be moved, so a click
 * on a stage is a move to it.
 */
function StageList({
  task,
  members,
  onMove,
}: {
  task: Task
  members: Person[]
  onMove: ((index: number) => void) | undefined
}) {
  const index = task.sub_status_index ?? 0
  const last = task.sub_statuses.length - 1

  return (
    <div className={styles.stageList}>
      {task.sub_statuses.map((label, position) => {
        const state =
          position < index || (position === index && index === last)
            ? styles.stageDone
            : position === index
              ? styles.stageHere
              : ''
        const text = <Tagged text={label} members={members} />
        return onMove ? (
          <button
            key={`${position}-${label}`}
            type="button"
            className={`${styles.stage} ${state}`}
            aria-current={position === index ? 'step' : undefined}
            title={position === index ? 'You are here' : `Move to ${label}`}
            onPointerDown={(event) => event.stopPropagation()}
            onKeyDown={(event) => event.stopPropagation()}
            onClick={(event) => {
              event.stopPropagation()
              if (position !== index) onMove(position)
            }}
          >
            {text}
          </button>
        ) : (
          <span key={`${position}-${label}`} className={`${styles.stage} ${state}`}>
            {text}
          </span>
        )
      })}
    </div>
  )
}

/**
 * How much of the card's work is split off and how much of that is done: a
 * tick and a count. Pointed at, it opens the parts themselves.
 */
function SubtaskMark({
  task,
  open,
  arming,
  handlers,
}: {
  task: Task
  open: boolean
  arming: boolean
  handlers: MarkHandlers
}) {
  const done = task.subtask_count - task.open_subtask_count
  const label = `${done} of ${task.subtask_count} sub-tasks finished`

  return (
    <button
      type="button"
      className={markClass(styles.subtaskMark, open, arming)}
      aria-label={label}
      aria-expanded={open}
      {...markEvents(handlers)}
      onPointerDown={(event) => event.stopPropagation()}
      onKeyDown={(event) => event.stopPropagation()}
      onClick={(event) => {
        event.stopPropagation()
        handlers.onPick()
      }}
    >
      <TickIcon />
      <span aria-hidden="true">
        {done}/{task.subtask_count}
      </span>
    </button>
  )
}

/**
 * The card's parts, one short row each: the sub-tasks with a reference of
 * their own, then the tick boxes. Fetched when first asked for, since the
 * board itself only carries the counts — and kept, under the same key the
 * card's dialog reads, so opening the card after looking costs nothing.
 *
 * Every row is something to act on, not a picture of one. The tick finishes
 * the part, or opens it again; the rest of a sub-task's row opens that
 * sub-task. A tick box has no dialog of its own, so its whole row is the tick.
 * Every click stops at the row: the card underneath would otherwise open the
 * parent as well.
 */
function SubtaskList({
  task,
  onOpenTask,
}: {
  task: Task
  onOpenTask: ((taskId: string) => void) | undefined
}) {
  const queryClient = useQueryClient()
  const announce = useContext(BoardAnnounce)
  const detail = useQuery({
    queryKey: ['task', task.id],
    queryFn: () => api.getTask(task.reference),
  })

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: ['task', task.id] })
    // The count on the card is the board's, so the board is out of date too.
    await queryClient.invalidateQueries({ queryKey: ['tasks'] })
  }
  const finish = useMutation({
    mutationFn: ({ part, finished }: { part: Task; finished: boolean }) =>
      api.finishTask(part.id, finished),
    onSuccess: async (part, { finished }) => {
      announce(`${part.reference} is ${finished ? 'finished' : 'open again'}.`)
      await refresh()
    },
    onError: (error) => announce(error.message),
  })
  const tick = useMutation({
    mutationFn: ({ id, done }: { id: string; done: boolean }) =>
      api.updateChecklistItem(id, { state: done ? 'done' : 'open' }),
    onSuccess: async (item) => {
      announce(`${item.title} is ${item.state === 'done' ? 'done' : 'open again'}.`)
      await refresh()
    },
    onError: (error) => announce(error.message),
  })
  const busy = finish.isPending || tick.isPending

  if (!detail.data) {
    return <div className={styles.partNote}>{detail.error ? 'Could not load' : 'Loading…'}</div>
  }

  const subtasks = detail.data.subtasks.filter((subtask) => subtask.status !== 'cancelled')
  const ticks = detail.data.checklist.filter((item) => item.state !== 'cancelled')
  const stop = {
    onPointerDown: (event: { stopPropagation: () => void }) => event.stopPropagation(),
    onKeyDown: (event: { stopPropagation: () => void }) => event.stopPropagation(),
  }

  return (
    <ul className={styles.parts}>
      {subtasks.map((subtask) => {
        const finished = subtask.finished_at !== null
        return (
          <li key={subtask.id} className={`${styles.part} ${finished ? styles.partDone : ''}`}>
            <button
              type="button"
              className={styles.partTick}
              aria-pressed={finished}
              aria-label={
                finished ? `Open ${subtask.reference} again` : `Finish ${subtask.reference}`
              }
              title={finished ? 'Open it again' : 'Finish it'}
              disabled={busy}
              {...stop}
              onClick={(event) => {
                event.stopPropagation()
                finish.mutate({ part: subtask, finished: !finished })
              }}
            >
              <TickIcon />
            </button>
            {onOpenTask ? (
              <button
                type="button"
                className={styles.partOpen}
                title={`Open ${subtask.reference}`}
                {...stop}
                onClick={(event) => {
                  event.stopPropagation()
                  onOpenTask(subtask.id)
                }}
              >
                <PartLabel subtask={subtask} />
                <ChevronIcon />
              </button>
            ) : (
              <span className={styles.partOpen}>
                <PartLabel subtask={subtask} />
              </span>
            )}
          </li>
        )
      })}
      {ticks.map((item) => {
        const done = item.state === 'done'
        return (
          <li key={item.id} className={`${styles.part} ${done ? styles.partDone : ''}`}>
            <button
              type="button"
              className={styles.partWhole}
              aria-pressed={done}
              disabled={busy}
              {...stop}
              onClick={(event) => {
                event.stopPropagation()
                tick.mutate({ id: item.id, done: !done })
              }}
            >
              <span className={styles.partTick} aria-hidden="true">
                <TickIcon />
              </span>
              <span className={styles.partTitle}>{item.title}</span>
            </button>
          </li>
        )
      })}
    </ul>
  )
}

/** A sub-task's reference, title and owner, as its row says them. */
function PartLabel({ subtask }: { subtask: Task }) {
  return (
    <>
      <span className={styles.partRef}>{subtask.reference}</span>
      <span className={styles.partTitle}>{subtask.title}</span>
      <Avatar name={subtask.assignee.name} colour={subtask.assignee.colour} small />
    </>
  )
}

/** A row that opens something: shown on hover, so a list at rest stays quiet. */
function ChevronIcon() {
  return (
    <svg className={styles.go} viewBox="0 0 16 16" aria-hidden="true">
      <path d="m6 3.5 4.5 4.5L6 12.5" />
    </svg>
  )
}

/** A row that opens elsewhere, in a new tab. */
function OutIcon() {
  return (
    <svg className={styles.go} viewBox="0 0 16 16" aria-hidden="true">
      <path d="M9.5 2.5h4v4M13.5 2.5 7.5 8.5M12 9.5V13a.5.5 0 0 1-.5.5h-8A.5.5 0 0 1 3 13V5a.5.5 0 0 1 .5-.5H7" />
    </svg>
  )
}

/**
 * Every pull request on the card as one mark: the furthest behind decides its
 * colour and drawing, since the card is not merged while any of its work is
 * still in review, and a count says how many it stands for. Pointed at, it
 * swaps the card's line of facts for the list of them.
 */
function PrMark({
  refs,
  open,
  arming,
  handlers,
}: {
  refs: PrRef[]
  open: boolean
  arming: boolean
  handlers: MarkHandlers
}) {
  const state = leastAdvancedPrState(refs) ?? 'raised'
  const only = refs.length === 1 ? refs[0] : undefined
  const label = only
    ? `Pull request ${prRefLabel(only.url)}, ${state}`
    : `${refs.length} pull requests, the furthest behind ${state}`

  return (
    <button
      type="button"
      className={`${markClass(styles.prMark, open, arming)} ${styles[`pr_${state}`]}`}
      aria-label={label}
      aria-expanded={open}
      title={label}
      {...markEvents(handlers)}
      onPointerDown={(event) => event.stopPropagation()}
      onKeyDown={(event) => event.stopPropagation()}
      onClick={(event) => {
        event.stopPropagation()
        handlers.onPick()
      }}
    >
      <PrIcon state={state} size={13} />
      {refs.length > 1 ? <span className={styles.prCount}>×{refs.length}</span> : null}
    </button>
  )
}

/**
 * Each pull request on its own line: where it is, which one, how far it got.
 * The whole line is the link, so it opens wherever on it you click; one that
 * is not a URL has nowhere to go and is only the line.
 */
function PrList({ refs }: { refs: PrRef[] }) {
  return (
    <ul className={styles.prList}>
      {refs.map((ref) => {
        const repo = prRefRepo(ref.url)
        const href = /^https?:\/\//i.test(ref.url) ? ref.url : null
        const line = (
          <>
            <PrIcon state={ref.state} size={12} />
            {repo ? <span className={styles.prRepo}>{repo}</span> : null}
            <span className={styles.prNumber}>{prRefLabel(ref.url)}</span>
            <span className={`${styles.prState} ${styles[`pr_${ref.state}`]}`}>{ref.state}</span>
          </>
        )
        return (
          <li key={ref.url}>
            {href ? (
              <a
                className={`${styles.prRow} ${styles.prRowLink}`}
                href={href}
                target="_blank"
                rel="noreferrer"
                title={`Open ${repo ? `${repo} ` : ''}${prRefLabel(ref.url)} in a new tab`}
                onClick={(event) => event.stopPropagation()}
                onPointerDown={(event) => event.stopPropagation()}
                onKeyDown={(event) => event.stopPropagation()}
              >
                {line}
                <OutIcon />
              </a>
            ) : (
              <span className={styles.prRow}>{line}</span>
            )}
          </li>
        )
      })}
    </ul>
  )
}

/**
 * The facts a card keeps off its face: how much has been said, how it stands,
 * who it waits on, what it is part of, who owns its parts and the Jira key the
 * face only drew the mark for. Icon and value, one line, nothing labelled —
 * the icon is the label.
 */
function CardFacts({ task }: { task: Task }) {
  return (
    <div className={styles.facts}>
      {task.comment_count ? (
        <span className={styles.fact} title={`${task.comment_count} comments`}>
          <CommentIcon size={13} />
          <span aria-hidden="true">{task.comment_count}</span>
          <span className="visually-hidden">{task.comment_count} comments</span>
        </span>
      ) : null}
      {task.status === 'active' ? null : (
        <span className={`${styles.fact} ${styles.status} ${styles[`status_${task.status}`]}`}>
          {STATUS_LABELS[task.status]}
        </span>
      )}
      {task.waiting_on.length ? (
        <span className={styles.fact} title="Waiting on">
          <ClockIcon size={13} />
          {task.waiting_on.map((person) => `@${person.name.split(' ')[0]}`).join(' ')}
          <span className="visually-hidden">waiting on them</span>
        </span>
      ) : null}
      {task.parent_reference ? (
        <span className={styles.fact} title={`Sub-task of ${task.parent_reference}`}>
          <span aria-hidden="true">↳</span>
          <span className={styles.factMono}>{task.parent_reference}</span>
        </span>
      ) : null}
      {task.subtask_assignees.length ? (
        <span
          className={`${styles.fact} ${styles.owners}`}
          title={`Sub-tasks: ${task.subtask_assignees.map((person) => person.name).join(', ')}`}
        >
          {task.subtask_assignees.map((person) => (
            <Avatar key={person.id} name={person.name} colour={person.colour} small />
          ))}
        </span>
      ) : null}
      {task.jira_ref ? (
        <span className={styles.fact}>
          <JiraIcon size={13} />
          <span className={styles.factMono}>{jiraRefLabel(task.jira_ref)}</span>
        </span>
      ) : null}
    </div>
  )
}

/**
 * What an agent on the card is doing. Drawn in the slot the stages take, behind
 * a rule in the colour the card's border is in, so the line and the border can
 * never disagree about what state it is.
 */
function AgentLine({ session }: { session: AgentPresence | null }) {
  const viewerId = useViewerId()
  const agent = agentIndicator(session, new Date(), viewerId)
  if (!agent) return null

  return (
    <div
      className={styles.agentLine}
      // The line is cut to the card's width; the whole of it is a hover away.
      title={agent.detail ? `${agent.label} · ${agent.detail}` : agent.label}
    >
      <span className={styles.agentLabel}>{agent.label}</span>
      {agent.detail ? <span className={styles.agentDetail}>{agent.detail}</span> : null}
      {session && session.count > 1 ? (
        <span className={styles.agentCount} title={`${session.count} sessions`}>
          ×{session.count}
        </span>
      ) : null}
    </div>
  )
}

/** A tick, for a finished part — the same one the sub-task count leads with. */
function TickIcon() {
  return (
    <svg
      className={styles.tick}
      viewBox="0 0 16 16"
      width="12"
      height="12"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3.5 8.5 6.5 11.5 12.5 4.5" />
    </svg>
  )
}

/**
 * The owner's badge, made a control: a native select sized over the disc so
 * clicking it reassigns the card without leaving the board for the dialog —
 * the same disguise `ProjectSwitcher` wears on the breadcrumb (Shell.tsx).
 *
 * Every event the select can fire is stopped here before it reaches the
 * card underneath: the card is both a drag handle and a button that opens
 * the task, and a click, a keystroke or a pointer-down left to bubble would
 * do one of those instead of reassigning anybody — see `StageMark` for
 * the same guard around the stage slider.
 */
function AssigneePicker({
  task,
  members,
  onSetAssignee,
}: {
  task: Task
  members: Person[]
  onSetAssignee: (assigneeId: string) => void
}) {
  return (
    <span className={styles.assigneePicker} onPointerDown={(event) => event.stopPropagation()}>
      <Avatar name={task.assignee.name} colour={task.assignee.colour} small />
      <select
        className={styles.assigneeSelect}
        aria-label={`Reassign ${task.reference}, now ${task.assignee.name}`}
        value={task.assignee.id}
        onClick={(event) => event.stopPropagation()}
        onKeyDown={(event) => event.stopPropagation()}
        onChange={(event) => {
          event.stopPropagation()
          const next = event.target.value
          if (next !== task.assignee.id) onSetAssignee(next)
        }}
      >
        {members.map((person) => (
          <option key={person.id} value={person.id}>
            {person.name}
          </option>
        ))}
      </select>
    </span>
  )
}

/**
 * The board's shape, behind one button: its card templates, and another
 * column. Both are pressed once or twice in a board's life, and two buttons
 * for them on the toolbar every day was a toolbar mostly about setup.
 *
 * The column item says how many of the board's columns are used, and at the
 * limit greys rather than goes: this is where the limit is explained, and a
 * missing item cannot say why it is missing.
 */
function BoardMenu({
  board,
  templateCount,
  onTemplates,
  onAddColumn,
}: {
  board: Board
  templateCount: number
  onTemplates: () => void
  onAddColumn: () => void
}) {
  const { open, setOpen, root, button } = usePopover()
  const used = board.columns.length
  const full = used >= board.max_columns

  return (
    <div className={styles.popRoot} ref={root}>
      <button
        ref={button}
        type="button"
        className={`${styles.toolButton} ${open ? styles.toolButtonOpen : ''}`}
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen(!open)}
      >
        Board
        <span aria-hidden="true">⌄</span>
      </button>
      {open ? (
        <div className={`${styles.popover} ${styles.menuPopover}`} role="menu" aria-label="Board">
          <button
            type="button"
            role="menuitem"
            className={styles.menuItem}
            onClick={() => {
              setOpen(false)
              onTemplates()
            }}
          >
            <span className={styles.menuText}>
              Card templates
              <span className={styles.menuHint}>
                {templateCount === 1 ? '1 saved' : `${templateCount} saved`}
              </span>
            </span>
          </button>
          <button
            type="button"
            role="menuitem"
            className={styles.menuItem}
            // aria-disabled rather than disabled, so it can still be reached
            // and read: see the note on the component.
            aria-disabled={full}
            onClick={() => {
              if (full) return
              setOpen(false)
              onAddColumn()
            }}
          >
            <span className={styles.menuText}>
              Add a column
              <span className={styles.menuHint}>
                {full
                  ? `All ${board.max_columns} used — a board holds ${board.max_columns} columns`
                  : `${used} of ${board.max_columns} used`}
              </span>
              <span className={styles.meter} aria-hidden="true">
                {Array.from({ length: board.max_columns }, (_, index) => (
                  <i key={index} className={index < used ? styles.meterOn : undefined} />
                ))}
              </span>
            </span>
          </button>
        </div>
      ) : null}
    </div>
  )
}

/** Three columns, for the board read by column. */
function ColumnsIcon() {
  return (
    <svg className={styles.toolIcon} viewBox="0 0 16 16" aria-hidden="true">
      <rect x="2" y="2.5" width="3.2" height="11" rx="1" />
      <rect x="6.4" y="2.5" width="3.2" height="7.5" rx="1" />
      <rect x="10.8" y="2.5" width="3.2" height="9.5" rx="1" />
    </svg>
  )
}

/** Stacked bands, for the board split into a lane per goal. */
function LanesIcon() {
  return (
    <svg className={styles.toolIcon} viewBox="0 0 16 16" aria-hidden="true">
      <rect x="2" y="2.5" width="12" height="3" rx="1" />
      <rect x="2" y="6.5" width="12" height="3" rx="1" />
      <rect x="2" y="10.5" width="12" height="3" rx="1" />
    </svg>
  )
}

/** A funnel, for the filter. */
function FilterIcon() {
  return (
    <svg className={styles.toolIcon} viewBox="0 0 16 16" aria-hidden="true">
      <path d="M2.5 3.5h11l-4.2 5v3.8l-2.6 1.2v-5z" />
    </svg>
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
          <OutcomesField
            saved={column.outcomes}
            outcomes={outcomes}
            maxOutcomes={maxOutcomes}
            onChange={setOutcomes}
          />
        ) : null}
      </ModalBody>
    </Modal>
  )
}

/** What an outcome box suggests while it is empty, by its place in the list. */
const OUTCOME_PLACEHOLDERS = ['Done', 'Cancelled', 'In prod']

/**
 * The sections the board's last column is divided into, as a list to edit:
 * a box per section, a × to take one away, and a button to add another.
 */
function OutcomesField({
  saved,
  outcomes,
  maxOutcomes,
  onChange,
}: {
  /** The sections as the server has them, before this edit. */
  saved: string[]
  outcomes: string[]
  maxOutcomes: number
  onChange: (outcomes: string[]) => void
}) {
  return (
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
              placeholder={OUTCOME_PLACEHOLDERS[index] ?? 'Outcome'}
              onChange={(event) =>
                onChange(outcomes.map((one, at) => (at === index ? event.target.value : one)))
              }
            />
            <Button
              small
              variant="ghost"
              aria-label={`Remove outcome ${index + 1}`}
              onClick={() => onChange(outcomes.filter((_, at) => at !== index))}
            >
              ×
            </Button>
          </div>
        ))}
        {outcomes.length < maxOutcomes ? (
          <Button small variant="ghost" onClick={() => onChange([...outcomes, ''])}>
            + Add an outcome
          </Button>
        ) : null}
        {/* Said here rather than left to the refusal: the cards are
            already in the sections, and what becomes of them is the
            thing anybody shortening the list is actually asking. */}
        {saved.length > outcomes.length ? (
          <p className={styles.note}>
            Cards in a section you have taken away move to the last one still standing.
          </p>
        ) : null}
      </div>
    </Field>
  )
}

/**
 * A due date on a card, drawn as near or as far as it is.
 *
 * The two ends of the ramp are the design: a date that has passed or is
 * passing today says what it means in words and in colour, because it is
 * asking for something; a date further out is the date itself in quiet ink,
 * because it is only telling you. Nothing between them changes size, so a
 * column of cards keeps one edge down its right-hand side.
 *
 * The visible text is abbreviated in four of the five states, so each mark
 * carries the whole sentence as well — spoken instead of the abbreviation, and
 * shown on hover. `column` is named in that sentence when the date is a stage's
 * rather than the card's own: the mark itself is the same either way, because
 * what it is asking for is the same.
 */
function DueMark({ iso, column }: { iso: string | null; column: string | null }) {
  const bucket = dueBucket(iso)
  if (iso === null || bucket.kind === 'none') return null

  const when = `${formatDueLong(iso)}${column ? ` in ${column}` : ''}`
  const { className, icon, text, said } = {
    late: {
      className: styles.dueLate,
      icon: <AlertIcon size={13} />,
      text: `${'days' in bucket ? bucket.days : 0}d late`,
      said: `${'days' in bucket ? bucket.days : 0} days late, due ${when}`,
    },
    today: {
      className: styles.dueToday,
      icon: <ClockIcon size={13} />,
      text: 'Today',
      said: `Due today, ${when}`,
    },
    tomorrow: {
      className: styles.dueTomorrow,
      icon: <CalendarIcon size={13} />,
      text: 'Tomorrow',
      said: `Due tomorrow, ${when}`,
    },
    soon: {
      className: styles.dueSoon,
      icon: <CalendarIcon size={13} />,
      text: formatDue(iso),
      said: `Due ${when}`,
    },
    later: {
      className: styles.dueLater,
      icon: <CalendarIcon size={13} />,
      text: formatDue(iso),
      said: `Due ${when}`,
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
