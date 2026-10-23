/**
 * Holding a project's socket open, and turning its nudges into refetches.
 *
 * The rules live in `api/realtime.ts` and `api/liveRefresh.ts`; this is the part
 * that knows about React and TanStack Query. It answers one question for the
 * board — `connected` — because that is what decides whether the old
 * ten-second poll needs to run.
 */

import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useSyncExternalStore } from 'react'
import { joinChanged, keysToRefresh } from '../api/liveRefresh'
import { queryKeys } from '../api/queries'
import { backoffDelay, boardSocketUrl, isFinal, parseEvent, type ChangedRef } from '../api/realtime'

/** Collapse a burst into one refetch. */
const DEBOUNCE_MS = 250

/**
 * The boards whose socket is up right now, by project key.
 *
 * Read by the window-focus rule in `main.tsx`: a board with a live socket is
 * told about every change to its cards, and settles up for the ones it missed
 * while hidden (`missedWhileHidden`), so reading all of them again because the
 * tab came back into view is a second copy of what the socket already does.
 */
const liveBoards = new Set<string>()

/** Whether a board socket already keeps this query's data current. */
export function keptCurrentBySocket(queryKey: readonly unknown[]): boolean {
  return (
    queryKey[0] === queryKeys.allBoardCards[0] &&
    typeof queryKey[1] === 'string' &&
    liveBoards.has(queryKey[1])
  )
}

export interface BoardSocketState {
  /** Whether the server has said `ready`. False means the board should poll. */
  connected: boolean
  /** Set when the socket was refused for good — the session is gone. */
  signedOut: boolean
}

const DOWN: BoardSocketState = { connected: false, signedOut: false }

/**
 * Each project's socket state, for whoever is drawing that project.
 *
 * The socket is held by `useProjectSocket`, once, from the app's shell; the
 * board reads whether it is up from here. Kept outside React so the one that
 * holds the socket and the ones that only want to know do not have to share a
 * parent.
 */
const states = new Map<string, BoardSocketState>()
const listeners = new Set<() => void>()

function setState(projectKey: string, next: BoardSocketState): void {
  const current = states.get(projectKey) ?? DOWN
  if (current.connected === next.connected && current.signedOut === next.signedOut) return
  states.set(projectKey, next)
  listeners.forEach((listener) => listener())
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** Whether this project's socket is up, and whether it was refused for good. */
export function useBoardSocket(projectKey: string): BoardSocketState {
  return useSyncExternalStore(subscribe, () => states.get(projectKey) ?? DOWN)
}

/**
 * Hold the open project's socket, and turn what it says into refetches.
 *
 * Mounted once, by the shell, whichever page of the project is showing — it
 * used to be the board's, so a goal's page, the hooks or Today only heard of
 * changes when somebody went back to the board. `null` is a page that is not
 * on a project, which has no socket.
 */
export function useProjectSocket(projectKey: string | null): void {
  const queryClient = useQueryClient()

  // Read by the socket's callbacks, which outlive any one render.
  const missedWhileHidden = useRef(false)
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null)
  // What has been named since the last refetch, so a burst of changes is one
  // refetch of exactly the lists they touched. `null` is "anything".
  const pending = useRef<ChangedRef[] | null | undefined>(undefined)

  useEffect(() => {
    if (projectKey === null) return
    // StrictMode mounts an effect twice in development. Without a generation
    // check the second mount's cleanup closes the first mount's socket and
    // the board is left permanently polling.
    let live = true
    let socket: WebSocket | null = null
    let retry: ReturnType<typeof setTimeout> | null = null
    let attempt = 0
    // Kept here rather than read from the store: the handlers below are made
    // once per effect, and what they closed over would never turn true.
    let refused = false

    const settle = () => {
      const named = pending.current
      pending.current = undefined
      for (const queryKey of keysToRefresh(projectKey, named === undefined ? null : named)) {
        void queryClient.invalidateQueries({ queryKey })
      }
    }

    const refresh = (changed: ChangedRef[] | null) => {
      pending.current = joinChanged(pending.current, changed)
      // Refetching a tab nobody is looking at is the waste
      // `refetchIntervalInBackground: false` was avoiding. Remember instead,
      // and settle up when the tab comes back.
      if (document.hidden) {
        missedWhileHidden.current = true
        return
      }
      if (debounce.current) clearTimeout(debounce.current)
      debounce.current = setTimeout(settle, DEBOUNCE_MS)
    }

    // A feed line concerns one card, and only an open one has a query to
    // refetch — TanStack ignores the key when nothing is subscribed to it.
    const refreshFeed = (taskId: string) => {
      if (document.hidden) {
        missedWhileHidden.current = true
        return
      }
      void queryClient.invalidateQueries({ queryKey: queryKeys.agentFeed(taskId) })
    }

    const open = () => {
      if (!live) return
      const current = new WebSocket(boardSocketUrl(projectKey, window.location))
      socket = current

      current.onmessage = (event) => {
        const message = parseEvent(event.data)
        if (!message) return
        if (message.type === 'ready') {
          // Only here, not on `open`: a socket that is accepted and then
          // immediately refused would otherwise reset the backoff and spin.
          attempt = 0
          liveBoards.add(projectKey)
          setState(projectKey, { connected: true, signedOut: false })
          // Whatever happened while there was no socket, happened. One
          // unconditional refetch is the entire resync protocol.
          refresh(null)
        } else if (message.type === 'board.changed') {
          refresh(message.changed)
        } else if (message.type === 'agent.feed') {
          refreshFeed(message.task_id)
        }
      }

      current.onclose = (event) => {
        // A socket that has already been replaced — closing while the tab
        // came back and opened a fresh one — has nothing left to say: its
        // retry would open a second socket beside the live one.
        if (socket !== current) return
        setState(projectKey, DOWN)
        // An unmounted project's socket says nothing for the set either: the
        // cleanup below already took it out, and under StrictMode this close
        // can land after the second mount's socket has put it back.
        if (!live) return
        liveBoards.delete(projectKey)
        if (isFinal(event.code)) {
          refused = true
          setState(projectKey, { connected: false, signedOut: true })
          return
        }
        retry = setTimeout(open, backoffDelay(attempt))
        attempt += 1
      }
    }

    const onVisible = () => {
      if (document.hidden) return
      if (missedWhileHidden.current) {
        missedWhileHidden.current = false
        settle()
      }
      // Frozen tabs get their sockets closed out from under them, and a
      // person who has just come back should not wait out a backoff.
      if (live && !refused && (socket === null || socket.readyState > WebSocket.OPEN)) {
        if (retry) clearTimeout(retry)
        attempt = 0
        open()
      }
    }

    document.addEventListener('visibilitychange', onVisible)
    open()

    return () => {
      live = false
      document.removeEventListener('visibilitychange', onVisible)
      if (retry) clearTimeout(retry)
      if (debounce.current) clearTimeout(debounce.current)
      pending.current = undefined
      socket?.close()
      liveBoards.delete(projectKey)
      setState(projectKey, DOWN)
    }
  }, [projectKey, queryClient])
}
