import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useMatchRoute } from '@tanstack/react-router'
import { api } from '../../api/client'
import { queryKeys, useBoard, useTasks } from '../../api/queries'
import { localZone } from '../dates'

/** Whether the open-cards pane was last left folded, per browser. */
export const CARDS_FOLDED_KEY = 'cylist.today.cardsFolded'

/** The project the page is on, or null off one. */
export function useProjectKey(): string | null {
  const matchRoute = useMatchRoute()
  const match = matchRoute({ to: '/p/$projectKey', fuzzy: true })
  return match ? match.projectKey : null
}

export function usePlan(projectKey: string | null, day: string) {
  return useQuery({
    queryKey: queryKeys.todayPlan(projectKey ?? '', day),
    queryFn: ({ signal }) => api.getToday(projectKey ?? '', day, localZone(), signal),
    enabled: projectKey !== null,
    // Off by one person's session — the bootstrap login has no plan, and
    // asking again will not give it one.
    retry: false,
    // A plan from today on follows its cards, which change behind it — on
    // the board, on an open card, by an agent — so opening it always reads it
    // again rather than showing the copy from half a minute ago.
    refetchOnMount: 'always',
  })
}

/** The board and its cards, which the left pane and the count are read from. */
export function useBoardCards(projectKey: string | null) {
  const enabled = projectKey !== null
  // The board's own readers, so a column read further down on the board is
  // read as far here, and the two never hold different shapes.
  const tasks = useTasks(projectKey ?? '', { enabled })
  const board = useBoard(projectKey ?? '', { enabled })
  return { tasks: tasks.data?.tasks, columns: board.data?.columns, readAt: tasks.dataUpdatedAt }
}

/**
 * After any change to a plan: that plan, and the reports it shows up in.
 *
 * Every day of the project rather than only the one on screen, because a
 * wrap-up writes to two days at once and the count on the sidebar is today's.
 */
export function useRefreshPlans(projectKey: string) {
  const queryClient = useQueryClient()
  return () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.todayPlans(projectKey) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.dayReports(projectKey) }),
      // A to-do from the card ticks the card too, so the board and any open
      // card are read again.
      queryClient.invalidateQueries({ queryKey: queryKeys.tasks(projectKey) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.allTasks }),
      queryClient.invalidateQueries({ queryKey: queryKeys.allTaskHistory }),
    ])
}
