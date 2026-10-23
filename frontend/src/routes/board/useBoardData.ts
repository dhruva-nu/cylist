/** Everything the board reads from the server, kept fresh by its socket. */

import { useQuery, useQueryClient } from '@tanstack/react-query'
import type { Task } from '../../api/client'
import {
  fetchBoardCards,
  queryKeys,
  useBoard,
  useGoals,
  useMembers,
  useTemplates,
  type BoardCards,
} from '../../api/queries'
import { hasLiveAgent } from '../agentState'
import { useBoardSocket } from '../useBoardSocket'

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

export function useBoardData(projectKey: string) {
  const board = useBoard(projectKey)
  // The board is told about changes rather than asking for them: one socket
  // per open board, and a refetch when it rings. See `useBoardSocket`.
  const socket = useBoardSocket(projectKey)
  // Read here rather than through `useTasks`, because only the board polls:
  // the same key, with the fallback interval no other reader of it wants.
  const queryClient = useQueryClient()
  const tasks = useQuery({
    queryKey: queryKeys.tasks(projectKey),
    queryFn: ({ signal }) =>
      fetchBoardCards(
        projectKey,
        queryClient.getQueryData<BoardCards>(queryKeys.tasks(projectKey)),
        signal,
      ),
    refetchInterval: (query) =>
      fallbackPollInterval(socket.connected, query.state.data?.tasks ?? []),
    refetchIntervalInBackground: false,
  })
  // Needed for `who:` search matches and its autocomplete, and for the `@`
  // tags in a stage label — not for the assignee on a card, which every card
  // already carries.
  const members = useMembers(projectKey)
  // Needed twice over: the New task form offers them, and the board reads the
  // columns each one allows to know where a card being dragged may be dropped.
  const templates = useTemplates(projectKey)
  // Needed for the `goal:` tag and its autocomplete, for the picker on the
  // card form, and for the lanes — which draw a lane per open goal whether or
  // not anything has been written under it yet, since an empty lane is where
  // the first card on a goal gets dropped.
  const goals = useGoals(projectKey)

  return { board, tasks, members, templates, goals, live: socket.connected }
}
