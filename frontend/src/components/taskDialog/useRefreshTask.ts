import { useQueryClient } from '@tanstack/react-query'

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
  const queryClient = useQueryClient()

  return async function refreshTask(taskId: string) {
    await queryClient.invalidateQueries({ queryKey: ['task', taskId] })
    await queryClient.invalidateQueries({ queryKey: ['task-history', taskId] })
    await onDone()
  }
}
