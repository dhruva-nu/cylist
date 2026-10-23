/**
 * One goal: what it is, how far along it is, and every card written under it.
 *
 * The page is deliberately not a small board. A goal's cards are listed under
 * the column each is in, in the board's own left-to-right order, because the
 * question this page answers is "what is left" rather than "what is where" —
 * and a second thing that looks like a board is a second thing to drag cards
 * around on, with two answers to where they went. Nothing here moves: the way
 * to move a card is the board, and "On the board" in the actions goes straight
 * there with this goal already in the search box.
 *
 * The cards take the page and the goal itself takes a column down the side.
 * That is the right way round: the description, the progress bar and the
 * counts are read once on arrival and then referred to, while the list is what
 * somebody came to work through — and a summary across the top pushed the
 * first card below the fold on every visit.
 */

import { useInfiniteQuery, useMutation } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from '@tanstack/react-router'
import { useState } from 'react'
import { api, type GoalDetail, type Task } from '../../api/client'
import { queryKeys, useBoard, useInvalidate, useMembers, useTemplates } from '../../api/queries'
import { GoalStatusPill } from '../../components/GoalMarks'
import { GoalDialog } from '../../components/GoalDialog'
import { goalClosure, type GoalClosure } from '../../components/goalClose'
import { useProjectFiles } from '../../components/projectFiles'
import { PageHead } from '../../components/Shell'
import { TaskDialog } from '../../components/TaskDialog'
import {
  Button,
  EmptyState,
  ErrorBanner,
  ErrorNotice,
  Eyebrow,
  LiveRegion,
  useAnnouncer,
} from '../../components/ui'
import { EVERYONE } from '../board/boardPeople'
import { usePermissions } from '../usePermissions'
import { cardMatches } from './cardMatch'
import { GoalAside, REFUSAL_ID } from './GoalAside'
import { COLD_STORAGE, Filters, GoalCardRow, placeOf } from './GoalCards'
import styles from './GoalPage.module.css'
import { LinkCardsDialog } from './LinkCardsDialog'
import { handlesOwnErrors } from '../../api/mutationErrors'

/** One page of a goal's cards — the first carrying the goal itself. */
interface GoalCardsPage {
  detail: GoalDetail | null
  items: Task[]
  next: string | null
}

export function GoalPage() {
  const { projectKey, goalRef } = useParams({ from: '/p/$projectKey/goals/$goalRef' })
  const may = usePermissions(projectKey)
  const invalidate = useInvalidate()
  const navigate = useNavigate()
  const { message, announce } = useAnnouncer()
  const [editing, setEditing] = useState(false)
  const [linking, setLinking] = useState(false)
  const [openTaskId, setOpenTaskId] = useState<string | null>(null)
  /** What the list is narrowed to: a word, and a column. Neither is remembered
      between visits — a filter you cannot see the state of is a page that
      looks broken when you come back to it, and both are visible here. */
  const [searchText, setSearchText] = useState('')
  const [onlyColumn, setOnlyColumn] = useState<string | null>(null)

  // The goal and the first page of its cards, then further pages of cards on
  // request: a goal can be an epic of hundreds, and its page reads like the
  // board, the top first.
  const goal = useInfiniteQuery({
    queryKey: queryKeys.goal(goalRef),
    queryFn: async ({ pageParam, signal }): Promise<GoalCardsPage> => {
      if (pageParam === null) {
        const detail = await api.getGoal(goalRef, signal)
        return { detail, items: detail.tasks, next: detail.tasks_next }
      }
      const page = await api.listGoalTasks(goalRef, pageParam, signal)
      return { detail: null, ...page }
    },
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next,
  })
  const board = useBoard(projectKey)
  /** For the `>` tags in the goal's description. */
  const files = useProjectFiles(projectKey)
  const members = useMembers(projectKey)
  const templates = useTemplates(projectKey)

  async function refresh() {
    await invalidate(
      queryKeys.allGoals,
      queryKeys.goals(projectKey),
      queryKeys.tasks(projectKey),
      queryKeys.allTasks,
    )
  }

  const unlink = useMutation({
    meta: handlesOwnErrors,
    mutationFn: (task: Task) => api.updateTask(task.reference, { goal_id: null }),
    onSuccess: async (task) => {
      await refresh()
      announce(`${task.reference} taken off this goal. It is still on the board.`)
    },
  })

  /**
   * Closing the goal, and opening it again.
   *
   * Only the status is sent: a PATCH is a partial edit, and a form's worth of
   * fields posted back alongside it would make a button that closes a goal
   * also a button that saves whatever the goal looked like when the page
   * loaded — which is how a rename made in another tab gets undone by someone
   * ticking a goal off.
   */
  const close = useMutation({
    meta: handlesOwnErrors,
    mutationFn: async (closure: GoalClosure) => {
      await api.updateGoal(goalRef, { status: closure.next })
      return closure
    },
    onSuccess: async (closure) => {
      await refresh()
      announce(closure.announcement)
    },
  })

  if (goal.isPending || board.isPending) return <EmptyState>Loading the goal…</EmptyState>
  if (goal.error) return <ErrorBanner>{goal.error.message}</ErrorBanner>
  if (board.error) return <ErrorBanner>{board.error.message}</ErrorBanner>

  const [first] = goal.data.pages
  if (!first?.detail) return <EmptyState>Loading the goal…</EmptyState>
  const goalDetail = {
    ...first.detail,
    tasks: goal.data.pages.flatMap((page) => page.items),
  }
  const columns = board.data.columns
  const memberList = members.data?.members ?? []
  const closure = goalClosure(goalDetail)

  const searchWord = searchText.trim().toLowerCase()
  // The word narrows what the column chips count, and the column narrows what
  // the list shows. In that order: a chip that says 3 and then shows nothing
  // because a word was typed is a chip that lies.
  const searchMatched = goalDetail.tasks.filter((task) => cardMatches(task, searchWord))
  const shownTasks = searchMatched.filter(
    (task) => onlyColumn === null || placeOf(task) === onlyColumn,
  )
  // Cold storage last, as it is on the board: its cards are off every column
  // but still the goal's, and still counted in its progress.
  const columnGroups = [
    ...columns.map((column) => ({ id: column.id, name: column.name })),
    { id: COLD_STORAGE, name: 'Cold storage' },
  ]
    .map((place) => ({
      ...place,
      tasks: shownTasks.filter((task) => placeOf(task) === place.id),
    }))
    .filter((group) => group.tasks.length > 0)

  return (
    <>
      <PageHead
        eyebrow={
          <Eyebrow>
            <span
              className={styles.rail}
              style={{ background: goalDetail.colour }}
              aria-hidden="true"
            />
            {goalDetail.reference}
            <GoalStatusPill status={goalDetail.status} className={styles.pill} />
          </Eyebrow>
        }
        title={goalDetail.name}
        actions={
          <>
            {/* Straight to the board with the goal already in the search box,
                which is the one thing this page cannot do: move cards. Every
                owner's cards, not the board's usual opening on yours: this
                page is about the goal, and so is the link. */}
            <Link
              to="/p/$projectKey/board"
              params={{ projectKey }}
              search={{ q: `goal:"${goalDetail.name}"`, who: EVERYONE }}
              className={styles.boardLink}
            >
              On the board
            </Link>
            <Button onClick={() => setLinking(true)}>+ Link cards</Button>
            {/* Not drawn for a role that is not allowed goals, as on the goals
                page: a button that always refuses is worse than one that is
                not there. */}
            {may('goals') ? (
              <CloseGoalButton
                closure={closure}
                pending={close.isPending}
                onClose={() => close.mutate(closure)}
              />
            ) : null}
            <Button variant="go" onClick={() => setEditing(true)}>
              Edit
            </Button>
          </>
        }
      />

      <LiveRegion message={message} />
      <ErrorNotice error={unlink.error} />
      {/* The button holds itself shut over open cards, so this is for what the
          page could not know: a card finished in another tab, or a role the
          server turned down. Either way the server's own sentence is the one
          worth showing — it names the cards. */}
      <ErrorNotice error={close.error} />

      {/* The goal down one side, its cards down the other. The aside comes
          first in the source and is placed on the right by the grid, so that
          on a narrow window — where there is only one column — what the goal
          is still arrives before the list of what is on it. */}
      <div className={styles.detail}>
        <GoalAside
          goal={goalDetail}
          refusal={may('goals') ? closure.refusal : null}
          members={memberList}
          files={files}
        />

        <div className={styles.list}>
          {goalDetail.tasks.length === 0 ? (
            <EmptyState>
              No cards on this goal yet. Link the ones already on the board, or pick this goal when
              you write a new card.
            </EmptyState>
          ) : (
            <Filters
              columns={columns}
              tasks={searchMatched}
              searchText={searchText}
              onSearchText={setSearchText}
              onlyColumn={onlyColumn}
              onColumn={setOnlyColumn}
              total={goalDetail.tasks.length}
              showing={shownTasks.length}
            />
          )}

          {goalDetail.tasks.length > 0 && shownTasks.length === 0 ? (
            <EmptyState>
              No card on this goal matches that.{' '}
              <button
                type="button"
                className={styles.clear}
                onClick={() => {
                  setSearchText('')
                  setOnlyColumn(null)
                }}
              >
                Clear the filters
              </button>
            </EmptyState>
          ) : null}

          {columnGroups.map(({ id, name, tasks }) => (
            <section key={id} className={styles.group}>
              <h2>
                {name}
                <span className={styles.count}>{tasks.length}</span>
              </h2>
              <ul className={styles.cards}>
                {tasks.map((task) => (
                  <GoalCardRow
                    key={task.id}
                    task={task}
                    unlinking={unlink.isPending}
                    onOpen={() => setOpenTaskId(task.id)}
                    onUnlink={() => unlink.mutate(task)}
                  />
                ))}
              </ul>
            </section>
          ))}

          {goal.hasNextPage ? (
            <div className={styles.more}>
              <Button
                small
                disabled={goal.isFetchingNextPage}
                onClick={() => void goal.fetchNextPage()}
              >
                {goal.isFetchingNextPage
                  ? 'Loading…'
                  : `Show more cards · ${goalDetail.progress.total - goalDetail.tasks.length} not shown`}
              </Button>
            </div>
          ) : null}
        </div>
      </div>

      {editing ? (
        <GoalDialog
          projectKey={projectKey}
          goal={goalDetail}
          members={memberList}
          announce={announce}
          onDone={refresh}
          onClose={() => setEditing(false)}
          onDeleted={() => {
            // Deliberately not awaited: the dialog has already closed, and the
            // navigation is the last thing this page does before it unmounts.
            void navigate({ to: '/p/$projectKey/goals', params: { projectKey } })
          }}
        />
      ) : null}

      {linking ? (
        <LinkCardsDialog
          projectKey={projectKey}
          goalId={goalDetail.id}
          goalName={goalDetail.name}
          columns={columns}
          announce={announce}
          onDone={refresh}
          onClose={() => setLinking(false)}
        />
      ) : null}

      {openTaskId && columns[0] ? (
        <TaskDialog
          projectKey={projectKey}
          taskId={openTaskId}
          columns={columns}
          firstColumn={columns[0]}
          templates={templates.data ?? []}
          goals={[goalDetail]}
          announce={announce}
          onOpenTask={setOpenTaskId}
          onDone={refresh}
          onClose={() => setOpenTaskId(null)}
        />
      ) : null}
    </>
  )
}

/**
 * The one button that ends a goal, and the one that starts it again.
 *
 * It says what it will do rather than naming a status — "Mark achieved", not
 * "Achieved" — because it is a verb being pressed, not a value being set; the
 * dropdown on the form is where a status is set, and this is the shortcut past
 * it for the one change anybody makes twice a year per goal.
 *
 * Over open cards it is held shut rather than left to be refused by the
 * server, and the sentence saying which cards is in the aside beside the
 * counts it is about — the button points at it, so a reader on a screen reader
 * is told why the moment they reach the button rather than after pressing it.
 */
function CloseGoalButton({
  closure,
  pending,
  onClose,
}: {
  closure: GoalClosure
  pending: boolean
  onClose: () => void
}) {
  const held = closure.refusal !== null

  return (
    <Button
      disabled={pending}
      // aria-disabled rather than disabled while it is held, as on
      // `AddColumnButton`: this is a button whose job at that moment is to say
      // why it will not close the goal, and a disabled button cannot be focused
      // to read it. Greying itself is the Button's own — see `[aria-disabled]`.
      aria-disabled={held}
      title={closure.refusal ?? closure.description}
      aria-describedby={held ? REFUSAL_ID : undefined}
      onClick={() => {
        if (!held) onClose()
      }}
    >
      {pending ? 'Saving…' : closure.label}
    </Button>
  )
}
