/**
 * The board's live connection: a socket that only ever rings a doorbell.
 *
 * The server sends `{type: 'board.changed'}` when something on this project
 * committed, and the answer is to refetch over HTTP like any other time. The
 * message deliberately carries no board data — see the backend's
 * `app/realtime/hub.py` for why, but the short version is that a card is
 * assembled from six queries in one place, and a push that rebuilt one would
 * be a second place.
 *
 * Kept out of React for the reason `agentState.ts` is: reconnect timing and
 * URL building are rules, and rules are easier to get right — and to test —
 * as plain functions. `useBoardSocket` is the twenty lines that know about
 * hooks.
 *
 * WebSocket routes are not in the OpenAPI document, so nothing generates
 * these types. They mirror `app/realtime/routes.py` by hand; change them
 * together.
 */

/** What the server may send down a board socket. */
export type BoardEvent =
  | { type: 'ready' }
  | { type: 'board.changed'; at: string }
  | { type: 'error'; code: string; message: string }

/**
 * Close codes the server uses, mirroring `app/realtime/auth.py`.
 *
 * Only `UNAUTHORIZED` changes what the client does: no amount of retrying
 * will find a credential, so it stops and says so. Everything else is worth
 * another attempt.
 */
export const CLOSE = {
  UNAUTHORIZED: 4401,
  FORBIDDEN: 4403,
  NOT_FOUND: 4404,
} as const

/** Where a project's board socket lives, from wherever the page was served. */
export function boardSocketUrl(projectKey: string, location: Location): string {
  const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${scheme}//${location.host}/api/v1/projects/${encodeURIComponent(projectKey)}/board/ws`
}

const FIRST_DELAY = 500
const MAX_DELAY = 15_000
const JITTER = 0.2

/**
 * How long to wait before attempt `n`, counting from zero.
 *
 * Exponential to a fifteen-second ceiling, then jittered, so that a backend
 * coming back up is not met by every open tab at the same instant.
 */
export function backoffDelay(attempt: number, random: () => number = Math.random): number {
  const flat = Math.min(FIRST_DELAY * 2 ** Math.max(0, attempt), MAX_DELAY)
  const spread = flat * JITTER
  return Math.round(flat - spread + random() * spread * 2)
}

/** Whether a close code means "stop trying" rather than "try again". */
export function isFinal(code: number): boolean {
  return code === CLOSE.UNAUTHORIZED
}

/** A message off the wire, or null if it is not one we understand. */
export function parseEvent(data: unknown): BoardEvent | null {
  if (typeof data !== 'string') return null
  let parsed: unknown
  try {
    parsed = JSON.parse(data)
  } catch {
    return null
  }
  if (typeof parsed !== 'object' || parsed === null) return null
  const type = (parsed as { type?: unknown }).type
  if (type === 'ready') return { type: 'ready' }
  if (type === 'board.changed') {
    const at = (parsed as { at?: unknown }).at
    return { type: 'board.changed', at: typeof at === 'string' ? at : '' }
  }
  if (type === 'error') {
    const { code, message } = parsed as { code?: unknown; message?: unknown }
    return {
      type: 'error',
      code: typeof code === 'string' ? code : 'unknown',
      message: typeof message === 'string' ? message : '',
    }
  }
  return null
}
