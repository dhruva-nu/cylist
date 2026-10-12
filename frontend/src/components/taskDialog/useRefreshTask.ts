import { queryKeys, useInvalidate } from '../../api/queries'

/** One card's detail — its fields, sub-tasks and timeline — under the key every reader shares. */
export function taskKey(taskId: string | null) {
  return [...queryKeys.allTasks, taskId]
}

/**
 * The history under a card's timeline. A prefix of every page's key, so
 * invalidating it refreshes whichever page is showing.
 */
export function taskHistoryKey(taskId: string) {
  return ['task-history', taskId]
}

/**
 * Bring a card back up to date after something on it was changed in place —
 * a comment taken back, a sub-task ticked, a pull request moved along.
 *
 * Every one of those is itself a change to the card, so the history under the
 * timeline is now an entry out of date as well as the card. `onDone` is the
 * opener's own refresh: the board behind the dialog draws counts and marks
 * for all three.
 */
export function useRefreshTask(onDone: () => Promise<void>) {
  const invalidate = useInvalidate()

  return async function refreshTask(taskId: string) {
    await invalidate(taskKey(taskId), taskHistoryKey(taskId))
    await onDone()
  }
}
