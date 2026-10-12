/**
 * Holding a board's socket open, and turning its nudges into refetches.
 *
 * The rules live in `api/realtime.ts`; this is the part that knows about
 * React and TanStack Query. It answers one question for the board —
 * `connected` — because that is what decides whether the old ten-second poll
 * needs to run.
 */

import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { backoffDelay, boardSocketUrl, isFinal, parseEvent } from '../api/realtime'

/** Collapse a burst into one refetch. */
const DEBOUNCE_MS = 250

export interface BoardSocketState {
  /** Whether the server has said `ready`. False means the board should poll. */
  connected: boolean
  /** Set when the socket was refused for good — the session is gone. */
  signedOut: boolean
}

export function useBoardSocket(projectKey: string): BoardSocketState {
  const queryClient = useQueryClient()
  const [connected, setConnected] = useState(false)
  const [signedOut, setSignedOut] = useState(false)

  // Read by the socket's callbacks, which outlive any one render.
  const missedWhileHidden = useRef(false)
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    // StrictMode mounts an effect twice in development. Without a generation
    // check the second mount's cleanup closes the first mount's socket and
    // the board is left permanently polling.
    let live = true
    let socket: WebSocket | null = null
    let retry: ReturnType<typeof setTimeout> | null = null
    let attempt = 0
    // Kept here rather than read from `signedOut`: the handlers below are made
    // once per effect, and the state they closed over would never turn true.
    let refused = false

    const refresh = () => {
      // Refetching a tab nobody is looking at is the waste
      // `refetchIntervalInBackground: false` was avoiding. Remember instead,
      // and settle up when the tab comes back.
      if (document.hidden) {
        missedWhileHidden.current = true
        return
      }
      if (debounce.current) clearTimeout(debounce.current)
      debounce.current = setTimeout(() => {
        void queryClient.invalidateQueries({ queryKey: ['tasks', projectKey] })
        // An open card is looking at the same change from the other side.
        void queryClient.invalidateQueries({ queryKey: ['task'] })
        // And so is its agent feed, whose own nudge may have been the one
        // the server coalesced away.
        void queryClient.invalidateQueries({ queryKey: ['agent-feed'] })
      }, DEBOUNCE_MS)
    }

    // A feed line concerns one card, and only an open one has a query to
    // refetch — TanStack ignores the key when nothing is subscribed to it.
    const refreshFeed = (taskId: string) => {
      if (document.hidden) {
        missedWhileHidden.current = true
        return
      }
      void queryClient.invalidateQueries({ queryKey: ['agent-feed', taskId] })
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
          setConnected(true)
          // Whatever happened while there was no socket, happened. One
          // unconditional refetch is the entire resync protocol.
          refresh()
        } else if (message.type === 'board.changed') {
          refresh()
        } else if (message.type === 'agent.feed') {
          refreshFeed(message.task_id)
        }
      }

      current.onclose = (event) => {
        // A socket that has already been replaced — closing while the tab
        // came back and opened a fresh one — has nothing left to say: its
        // retry would open a second socket beside the live one.
        if (socket !== current) return
        setConnected(false)
        if (!live) return
        if (isFinal(event.code)) {
          refused = true
          setSignedOut(true)
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
        refresh()
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
      socket?.close()
      setConnected(false)
    }
  }, [projectKey, queryClient])

  return { connected, signedOut }
}
