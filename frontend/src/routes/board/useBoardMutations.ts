/**
 * Everything the board writes: a card moved, a stage slid along, a card handed
 * to someone, the columns put in a new order — and the refresh that follows
 * each of them.
 *
 * Each write updates the cache before the request goes out. A card that snaps
 * back is how you find out the move failed — waiting for a round trip to see a
 * card move makes the board feel broken even when it is working.
 */

import { useMutation, useQueryClient, type QueryKey } from '@tanstack/react-query'
import {
  api,
  type Board,
  type BoardColumn,
  type Person,
  type Task,
  type TodayPlan,
} from '../../api/client'
import { queryKeys, refreshesItself, useInvalidate, type BoardCards } from '../../api/queries'
import { sortKeyAt } from './sortKey'

/** A card dropped somewhere: where it lands, and what that changes about it. */
export interface Move {
  taskId: string
  /** `ATL-41` — what an error about this card will name it by. */
  taskRef: string
  /** Null when the card was dropped in the column it was already in. */
  columnId: string | null
  /**
   * How many cards from the top of the column it lands — what the server is
   * asked for. Not the card's `position`, which is a sort key: `sortKeyAt`
   * works out the one the board draws it with meanwhile.
   */
  position: number
  /**
   * The section of a divided column the card lands on, by name, and where the
   * board draws it while the request is in the air. Null for a drop on a
   * column that is not divided — every column but the board's last, and most
   * of those.
   */
  outcome: string | null
  outcomeIndex: number | null
}

/**
 * A card as it will look once a move has landed, so the board can draw it
 * there before the server has answered.
 */
function withMoveApplied(task: Task, move: Move, cards: Task[]): Task {
  const { columnId, position, outcome, outcomeIndex } = move
  if (!columnId) return task
  return {
    ...task,
    column_id: columnId,
    position: sortKeyAt(cards, columnId, position, task.id),
    outcome,
    outcome_index: outcomeIndex,
  }
}

/** What an optimistic write remembers, so a refusal can put the cache back. */
interface Snapshot<T> {
  previous: T | undefined
}

/**
 * `members` is the project's list as it currently stands, which is what a card
 * handed to someone borrows its new name and colour from while the request is
 * in the air. `live` is whether the board's socket is up, which decides who
 * refetches the cards.
 */
export function useBoardMutations(
  projectKey: string,
  members: Person[] | undefined,
  live: boolean,
) {
  const queryClient = useQueryClient()
  const invalidate = useInvalidate()
  const tasksKey = queryKeys.tasks(projectKey)
  const boardKey = queryKeys.board(projectKey)

  /** Everything a write on the board can leave out of date, wherever it is read. */
  async function refresh() {
    await invalidate(
      boardKey,
      tasksKey,
      queryKeys.coldStorage(projectKey),
      queryKeys.allTasks, // every open card's timeline
      queryKeys.templates(projectKey),
      queryKeys.goals(projectKey),
      queryKeys.allGoals,
    )
  }

  /**
   * What a change to one card from the board can leave out of date — less
   * than `refresh`, because it follows every drag.
   *
   * While the socket is up the cards, and any card open, come back on their
   * own: the server tells every board about the change, this one included,
   * and reading them here too would read them twice. The socket says nothing
   * of the card's goal, whose progress counts it, or of a day it is planned
   * on, whose to-do a stage ticks — so those are read here, when the card has
   * either. After a refusal it is `refresh` instead: the cards were put back
   * from a snapshot, and the server, having changed nothing, sends no word.
   */
  async function refreshCard(taskId: string) {
    const keys: QueryKey[] = live ? [] : [tasksKey, queryKeys.allTasks]
    const card = queryClient.getQueryData<BoardCards>(tasksKey)?.tasks.find((t) => t.id === taskId)
    if (card?.goal_id) keys.push(queryKeys.goals(projectKey), queryKeys.allGoals)
    const planned = queryClient
      .getQueriesData<TodayPlan>({ queryKey: queryKeys.todayPlans(projectKey) })
      .some(([, plan]) => plan?.picks.some((pick) => pick.task.id === taskId))
    if (planned) keys.push(queryKeys.todayPlans(projectKey), queryKeys.dayReports(projectKey))
    await invalidate(...keys)
  }

  /**
   * Redraw the cards as `change` says they will be, and remember how they were.
   *
   * In-flight fetches are cancelled first: one landing after this would put
   * the old cards back over the new ones until the refresh after the write.
   */
  async function changeCardsOptimistically(
    change: (task: Task) => Task,
  ): Promise<Snapshot<BoardCards>> {
    await queryClient.cancelQueries({ queryKey: tasksKey })
    const previous = queryClient.getQueryData<BoardCards>(tasksKey)
    queryClient.setQueryData<BoardCards>(tasksKey, (current) =>
      current ? { ...current, tasks: current.tasks.map(change) } : current,
    )
    return { previous }
  }

  function restoreCards(snapshot: Snapshot<BoardCards> | undefined) {
    queryClient.setQueryData(tasksKey, snapshot?.previous)
  }

  /** Where a dropped card lands: a column, and the section of it where it is divided. */
  const moveTask = useMutation<Task | null, Error, Move, Snapshot<BoardCards>>({
    mutationFn: async ({ taskId, columnId, position, outcome }) =>
      columnId ? await api.moveTask(taskId, columnId, position, outcome) : null,
    onMutate: (move) => {
      const cards = queryClient.getQueryData<BoardCards>(tasksKey)?.tasks ?? []
      return changeCardsOptimistically((task) =>
        task.id === move.taskId ? withMoveApplied(task, move, cards) : task,
      )
    },
    onError: (_error, _move, snapshot) => restoreCards(snapshot),
    onSettled: (_moved, error, move) => (error ? refresh() : refreshCard(move.taskId)),
    meta: refreshesItself,
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
    Snapshot<BoardCards>
  >({
    mutationFn: ({ taskId, index }) => api.setSubStatus(taskId, index),
    onMutate: ({ taskId, index }) =>
      changeCardsOptimistically((task) =>
        task.id === taskId ? { ...task, sub_status_index: index } : task,
      ),
    onError: (_error, _change, snapshot) => restoreCards(snapshot),
    onSettled: (_task, error, { taskId }) => (error ? refresh() : refreshCard(taskId)),
    meta: refreshesItself,
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
    Snapshot<BoardCards>
  >({
    mutationFn: ({ taskRef, assigneeId }) => api.updateTask(taskRef, { assignee_id: assigneeId }),
    onMutate: ({ taskId, assigneeId }) => {
      const assignee = (members ?? []).find((person) => person.id === assigneeId)
      return changeCardsOptimistically((task) =>
        task.id === taskId && assignee ? { ...task, assignee } : task,
      )
    },
    onError: (_error, _change, snapshot) => restoreCards(snapshot),
    onSettled: (_task, error, { taskId }) => (error ? refresh() : refreshCard(taskId)),
    meta: refreshesItself,
  })

  const reorderColumns = useMutation<Board, Error, string[], Snapshot<Board>>({
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
    onError: (_error, _columnIds, snapshot) => {
      queryClient.setQueryData(boardKey, snapshot?.previous)
    },
    // The templates' stages are listed in the board's order.
    onSettled: () => invalidate(boardKey, queryKeys.templates(projectKey)),
    meta: refreshesItself,
  })

  return { refresh, moveTask, moveSubStatus, setAssignee, reorderColumns }
}
