/**
 * The Kanban board.
 *
 * Two rules from the API are visible in the UI rather than only enforced by
 * it: "+ Add a task" appears under the first column alone, because that is
 * where new work lands; and "Add a column" in the Board menu counts its
 * columns up to eight and then goes grey, because that is where a board stops
 * being readable.
 *
 * This file is the page: it loads the board, holds what the reader has asked
 * of it, and puts the pieces together. The pieces live in `./board/` — the
 * columns and cards, the toolbar, the drag and drop, and the arithmetic of
 * which cards are drawn where.
 *
 * Dragging is not only a pointer gesture. A focused card is picked up with
 * space and walked between columns with the arrow keys, which is the same
 * operation through the same code path — see `board/keyboardDrag.ts`.
 */

import { closestCenter, DndContext, DragOverlay } from '@dnd-kit/core'
import { useNavigate, useParams, useSearch } from '@tanstack/react-router'
import { useMemo, useRef, useState } from 'react'
import type { Board, BoardColumn, Goal, Person, Task, Template } from '../api/client'
import { useIdentity } from '../api/queries'
import { TaskDialog } from '../components/TaskDialog'
import { TemplateDialog } from '../components/TemplateDialog'
import { EmptyState, ErrorNotice, LiveRegion, useAnnouncer } from '../components/ui'
import { anyAgentCounted } from './agentState'
import { hidesHold } from './boardHiding'
import { BoardGrid, type LanesLayout, type SharedColumnProps } from './board/BoardGrid'
import { peopleFromSearch, searchFromPeople } from './board/boardPeople'
import { BoardToolbar, type ChosenFilters } from './board/BoardToolbar'
import { CardActionsContext, type CardActions } from './board/cardActions'
import { ColdStorageDialog } from './board/ColdStorageDialog'
import { ColumnDialog } from './board/ColumnDialog'
import { SCREEN_READER_INSTRUCTIONS } from './board/keyboardDrag'
import { lanesFor, narrowBoard, tasksByColumn, type Lane } from './board/narrowBoard'
import { NobodysCards } from './board/PeopleFilter'
import { ReopenDialog } from './board/ReopenDialog'
import { LiftedTaskCard } from './board/TaskCard'
import { useBoardData } from './board/useBoardData'
import { useBoardDrag } from './board/useBoardDrag'
import { useBoardMutations } from './board/useBoardMutations'
import { useBoardView } from './board/useBoardView'
import { BoardAnnounce } from './CardPromptPopover'
import { usePermissions } from './usePermissions'

export function ProjectBoard() {
  const { projectKey } = useParams({ from: '/p/$projectKey/board' })
  const { q: initialQuery = '', who } = useSearch({ from: '/p/$projectKey/board' })
  const { board, tasks, members, templates, goals } = useBoardData(projectKey)
  // Already in hand: the sign-in gate does not draw a page until it is.
  const identity = useIdentity()

  // The members too, this time: without them the board cannot tell whether
  // to open on you, and guessing either way redraws it a moment later.
  if (board.isPending || tasks.isPending || members.isPending) {
    return <EmptyState>Loading the board…</EmptyState>
  }
  if (board.error || tasks.error) return <ErrorNotice error={board.error ?? tasks.error} />

  return (
    // Keyed on the project, so walking to another board starts it afresh: a
    // search, an open card or a held drop belongs to the board it was made on.
    <LoadedBoard
      key={projectKey}
      projectKey={projectKey}
      initialQuery={initialQuery}
      who={who}
      meId={identity.data?.person?.id ?? null}
      board={board.data}
      tasks={tasks.data}
      members={members.data?.members ?? EMPTY}
      templates={templates.data ?? EMPTY}
      goals={goals.data ?? EMPTY}
      goalsLoaded={!goals.isPending}
    />
  )
}

/**
 * One shared empty list for whatever has not arrived yet, so a missing list is
 * the same list on every render rather than a new one each time.
 */
const EMPTY: never[] = []

/** Which dialog, if any, is open over the board. One at a time, never stacked. */
type BoardDialog =
  | { kind: 'task'; taskId: string }
  | { kind: 'newTask'; goalId: string | null }
  | { kind: 'newSubtask'; parentRef: string }
  | { kind: 'column'; column: BoardColumn | null }
  | { kind: 'templates' }
  | { kind: 'coldStorage' }

function LoadedBoard({
  projectKey,
  initialQuery,
  who,
  meId,
  board,
  tasks,
  members,
  templates,
  goals,
  goalsLoaded,
}: {
  projectKey: string
  /**
   * `?q=`, which is how a goal's page hands the board its own cards. Read once:
   * after that the box is the reader's, and rewriting it from the URL on every
   * render would take the cursor with it.
   */
  initialQuery: string
  /** `?who=`, whose cards the board shows — see `peopleFromSearch`. */
  who: string | undefined
  /** The person signed in, where the session is one. */
  meId: string | null
  board: Board
  tasks: Task[]
  members: Person[]
  templates: Template[]
  goals: Goal[]
  goalsLoaded: boolean
}) {
  const may = usePermissions(projectKey)
  const { message, announce } = useAnnouncer()
  const view = useBoardView(projectKey)
  const { refresh, moveTask, moveSubStatus, setAssignee, reorderColumns } = useBoardMutations(
    projectKey,
    goals,
    members,
  )
  const boardRef = useRef<HTMLDivElement>(null)
  const [dialog, setDialog] = useState<BoardDialog | null>(null)
  /**
   * The search box and the quick filters. The search box already parses
   * `goal:` and `blk:`/`hld:` tags, but a tag has to be known to be typed —
   * the goal and status selects are the same two questions asked as a pair of
   * dropdowns, for whoever would rather point than type, and the agent chips
   * (CYLIST-90) are every card, the ones that need you, or the busy ones.
   */
  const [chosen, setChosen] = useState<Omit<ChosenFilters, 'people'>>(() => ({
    search: initialQuery,
    goal: 'all',
    status: 'all',
    agent: 'all',
  }))
  /**
   * Whose cards (CYLIST-99), which unlike the rest lives in the URL rather
   * than in this component: it is the one filter a board is opened onto, so a
   * link has to be able to carry it. Replaced rather than pushed, so Back
   * leaves the board instead of walking through every face pressed on it.
   */
  const navigate = useNavigate({ from: '/p/$projectKey/board' })
  const filters: ChosenFilters = { ...chosen, people: peopleFromSearch(who, meId, members) }
  function setFilters(changes: Partial<ChosenFilters>) {
    const { people, ...rest } = changes
    if (people !== undefined) {
      void navigate({
        search: (current) => ({ ...current, who: searchFromPeople(people) }),
        replace: true,
      })
    }
    setChosen((current) => ({ ...current, ...rest }))
  }
  // A chip pressed when the last agent on the board stops has nothing left to
  // narrow to, and the chips themselves go (CYLIST-97) — so the board lets go
  // of it rather than sitting empty under a filter nobody can see to release.
  // Settled during render, React's own pattern for state that follows props.
  if (chosen.agent !== 'all' && !anyAgentCounted(tasks)) {
    setChosen((current) => ({ ...current, agent: 'all' }))
  }

  const columns = board.columns
  const firstColumn = columns[0]
  const doneColumn = columns.at(-1)

  const { visibleTasks, cancelledCount, hidingCancelled, parsed, agentTally, peopleTally } =
    narrowBoard(tasks, { ...filters, showCancelled: view.showCancelled }, columns, members)
  const byColumn = tasksByColumn(columns, visibleTasks)

  const drag = useBoardDrag({
    columns,
    tasks,
    byColumn,
    templates,
    moveTask: moveTask.mutate,
    reorderColumns: reorderColumns.mutate,
    announce,
  })

  // Memoised so every card is not handed a new context value on renders where
  // nothing it can do has changed. A mutation's `mutate` is stable.
  const { mutate: mutateSubStatus } = moveSubStatus
  const { mutate: mutateAssignee } = setAssignee
  const cardActions = useMemo<CardActions>(
    () => ({
      projectKey,
      members,
      openTask: (taskId) => setDialog({ kind: 'task', taskId }),
      moveSubStatus: (taskId, index) => mutateSubStatus({ taskId, index }),
      setAssignee: (taskId, taskRef, assigneeId) => mutateAssignee({ taskId, taskRef, assigneeId }),
    }),
    [projectKey, members, mutateSubStatus, mutateAssignee],
  )

  /**
   * Whether to draw the lanes yet.
   *
   * Not until the goals are in: every card would fall into the "No goal" lane
   * for as long as the request took, and the board would visibly re-sort
   * itself the moment it landed.
   */
  const inLanes = view.lanesOn && goalsLoaded
  const lanes = lanesFor(goals, visibleTasks)
  /** One gesture for the whole board: everything away, or everything back. */
  const everyLaneFolded =
    lanes.length > 0 && lanes.every((lane) => view.foldedLanes.includes(lane.key))
  const mayWriteCards = may('tasks')

  function toggleColumn(column: BoardColumn) {
    view.toggleColumn(column.id)
    announce(
      view.collapsedColumns.includes(column.id)
        ? `${column.name} expanded.`
        : `${column.name} collapsed.`,
    )
  }

  function toggleLane(lane: Lane) {
    const name = lane.goal?.name ?? 'No goal'
    view.toggleLane(lane.key)
    announce(view.foldedLanes.includes(lane.key) ? `${name} expanded.` : `${name} folded away.`)
  }

  function toggleEveryLane() {
    view.setFoldedLanes(everyLaneFolded ? [] : lanes.map((lane) => lane.key))
    announce(everyLaneFolded ? 'Every lane expanded.' : 'Every lane folded away.')
  }

  const columnProps = (column: BoardColumn): SharedColumnProps => ({
    column,
    isFirst: column.id === firstColumn?.id,
    collapsed: view.collapsedColumns.includes(column.id),
    collapsedOutcomes: view.collapsedOutcomes,
    forbidden: drag.forbiddenColumns.has(column.id),
    onToggleCollapse: () => toggleColumn(column),
    onToggleOutcome: view.toggleOutcome,
    onAddTask: mayWriteCards ? () => setDialog({ kind: 'newTask', goalId: null }) : null,
    onEdit: () => setDialog({ kind: 'column', column }),
    onColdStorage: column.id === doneColumn?.id ? () => setDialog({ kind: 'coldStorage' }) : null,
    // Settled per column, out of the column's own answer and the board's: see
    // `hidesHold`. Passed in rather than read inside `Column` because the
    // search box and the status filter are the board's, and a column that
    // reached for them would be reaching past its own props for them.
    hidingHold: hidesHold(
      view.holdShown.includes(column.id),
      filters.status,
      parsed,
      filters.agent !== 'all',
    ),
    onToggleHold: () => view.toggleHold(column.id),
  })

  const lanesLayout: LanesLayout | null = inLanes
    ? {
        lanes,
        folded: view.foldedLanes,
        projectKey,
        onToggleFold: toggleLane,
        onAddTask: mayWriteCards
          ? (lane) => setDialog({ kind: 'newTask', goalId: lane.goal?.id ?? null })
          : null,
      }
    : null

  const closeDialog = () => setDialog(null)
  /** What every task dialog the board opens is handed, whichever card it is for. */
  const taskDialogProps = {
    projectKey,
    columns,
    templates,
    goals,
    announce,
    onDone: refresh,
    onClose: closeDialog,
  }

  return (
    <>
      {/* No standing paragraph of instructions. What it said, the board says
          better by being used: cards enter at the first column because that is
          the only one with a "+", and a card is dragged by dragging it. The
          keyboard's share of it is the part that is not self-evident, and that
          is spoken by SCREEN_READER_INSTRUCTIONS to the people it is for. */}
      <ErrorNotice error={moveTask.error} />
      <LiveRegion message={message} />

      <BoardToolbar
        board={board}
        members={members}
        goals={goals}
        meId={meId}
        tasks={tasks}
        filters={filters}
        onFiltersChange={setFilters}
        showCancelled={view.showCancelled}
        onShowCancelled={view.setShowCancelled}
        cancelledCount={cancelledCount}
        hidingCancelled={hidingCancelled}
        agentTally={agentTally}
        peopleTally={peopleTally}
        lanesOn={view.lanesOn}
        onLanesOn={view.setLanesOn}
        foldAll={inLanes ? { everyLaneFolded, onToggle: toggleEveryLane } : null}
        shaping={
          may('board')
            ? {
                templateCount: templates.length,
                onOpenTemplates: () => setDialog({ kind: 'templates' }),
                onAddColumn: () => setDialog({ kind: 'column', column: null }),
              }
            : null
        }
      />

      <NobodysCards
        people={filters.people}
        tasks={tasks}
        members={members}
        meId={meId}
        onEveryone={() => setFilters({ people: [] })}
      />

      <BoardAnnounce.Provider value={announce}>
        <CardActionsContext.Provider value={cardActions}>
          <DndContext
            sensors={drag.sensors}
            // Columns are the only drop targets and they never overlap, so
            // nearest centre is both the obvious answer and the one a keyboard
            // drag — which lands the card dead centre — can rely on.
            collisionDetection={closestCenter}
            accessibility={{
              announcements: drag.announcements,
              screenReaderInstructions: SCREEN_READER_INSTRUCTIONS,
            }}
            // The board scrolls sideways to reach a column that is off the
            // edge, and nothing else scrolls at all. Left to itself dnd-kit
            // picks the nearest scrollable ancestor, which is the card list you
            // are dragging out of: the column then scrolls its own cards away
            // under the pointer for as long as you hold one near its top or
            // bottom.
            autoScroll={{ canScroll: (element) => element === boardRef.current }}
            onDragStart={drag.onDragStart}
            onDragEnd={drag.onDragEnd}
            onDragCancel={drag.onDragCancel}
          >
            <BoardGrid
              boardRef={boardRef}
              columns={columns}
              byColumn={byColumn}
              columnProps={columnProps}
              lanes={lanesLayout}
            />

            {/* Outside the board, so the board's own overflow has nothing to
                say about where the card in the air is allowed to be drawn. */}
            <DragOverlay dropAnimation={null}>
              {drag.draggedTask ? <LiftedTaskCard task={drag.draggedTask} /> : null}
            </DragOverlay>
          </DndContext>
        </CardActionsContext.Provider>
      </BoardAnnounce.Provider>

      {drag.reopening ? (
        <ReopenDialog
          reopening={drag.reopening}
          onClose={drag.cancelReopening}
          onConfirm={drag.confirmReopening}
        />
      ) : null}

      {dialog?.kind === 'column' ? (
        <ColumnDialog
          projectKey={projectKey}
          column={dialog.column}
          canDelete={columns.length > board.min_columns}
          isLast={dialog.column !== null && dialog.column.id === doneColumn?.id}
          maxOutcomes={board.max_outcomes}
          announce={announce}
          onDone={refresh}
          onClose={closeDialog}
        />
      ) : null}

      {doneColumn && dialog?.kind === 'coldStorage' ? (
        <ColdStorageDialog
          projectKey={projectKey}
          column={doneColumn}
          // Every card in the column, not the ones the filters leave drawn:
          // storing takes the whole column, whatever the board is showing.
          cardCount={tasks.filter((task) => task.column_id === doneColumn.id).length}
          mayStore={mayWriteCards && may('files')}
          mayRestore={mayWriteCards}
          announce={announce}
          onDone={refresh}
          onOpenTask={(taskId) => setDialog({ kind: 'task', taskId })}
          onClose={closeDialog}
        />
      ) : null}

      {dialog?.kind === 'templates' ? (
        <TemplateDialog
          projectKey={projectKey}
          columns={columns}
          announce={announce}
          onDone={refresh}
          onClose={closeDialog}
        />
      ) : null}

      {/* One dialog at a time, never stacked: a sub-task's card needs every
          field a card needs, and a form drawn on top of the form it came from
          is two Save buttons with no way to tell which one is which. */}
      {firstColumn && dialog?.kind === 'newTask' ? (
        <TaskDialog
          {...taskDialogProps}
          taskId={null}
          firstColumn={firstColumn}
          defaultGoalId={dialog.goalId}
        />
      ) : null}

      {firstColumn && dialog?.kind === 'newSubtask' ? (
        <TaskDialog
          {...taskDialogProps}
          taskId={null}
          parentRef={dialog.parentRef}
          firstColumn={firstColumn}
        />
      ) : null}

      {firstColumn && dialog?.kind === 'task' ? (
        <TaskDialog
          {...taskDialogProps}
          taskId={dialog.taskId}
          firstColumn={firstColumn}
          onOpenTask={(taskId) => setDialog({ kind: 'task', taskId })}
          onSplit={(parentRef) => setDialog({ kind: 'newSubtask', parentRef })}
        />
      ) : null}
    </>
  )
}
