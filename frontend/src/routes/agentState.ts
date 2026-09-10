/**
 * What a card says about the agent on it.
 *
 * The server has already reduced a card's sessions to one `AgentPresence` —
 * whatever needs a human first, then whatever is alive, then what is over.
 * This side only decides how that reads: which class the card wears, and the
 * one line under the title that says why.
 *
 * Kept out of the component for the reason `today.ts` is. A border that says
 * "working" on a card whose agent died an hour ago, or that says nothing on a
 * card with a question waiting, is a wrong answer nobody goes looking for, and
 * the rules are easier to get right — and to test — as plain functions.
 *
 * `now` is the caller's to supply: the board re-renders on a timer while any
 * card has a live session, and "silent for 12m" has to move with the clock
 * rather than with the last fetch.
 */

import type { AgentPresence, AgentSessionReason } from '../api/client'

export type AgentClassName = 'agentWorking' | 'agentWaiting' | 'agentDone' | 'agentStale'

export interface AgentIndicator {
  className: AgentClassName
  /** The line under the title. */
  label: string
  /** A second, quieter clause: what it is waiting for. */
  detail?: string
}

/** How long a session has been quiet, said the way a person would. */
export function silentFor(lastSeenAt: string, now: Date): string {
  const minutes = Math.max(0, Math.floor((now.getTime() - new Date(lastSeenAt).getTime()) / 60_000))
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h`
  return `${Math.floor(hours / 24)}d`
}

/** What a waiting agent is waiting for, in the words the card uses. */
export function waitingDetail(reason: AgentSessionReason | null): string {
  switch (reason) {
    case 'permission':
      return 'permission'
    case 'idle':
      return 'idle'
    case 'question':
      return 'a question'
    case 'turn_ended':
    default:
      return 'waiting for your reply'
  }
}

/**
 * The card's indicator, or null for the ordinary card with no agent on it.
 *
 * The count is said only when it is more than one — "Agent working" is what
 * one session reads as, and "1 agent working" makes every card sound like a
 * head-count.
 */
export function agentIndicator(p: AgentPresence | null, now: Date): AgentIndicator | null {
  if (!p) return null
  const many = p.count > 1
  const agents = many ? `${p.count} agents` : 'Agent'

  switch (p.state) {
    case 'waiting':
      return {
        className: 'agentWaiting',
        label: many ? `${agents} · one needs you` : 'Agent needs you',
        detail: waitingDetail(p.reason),
      }
    case 'working':
      return { className: 'agentWorking', label: `${agents} working` }
    case 'done':
      // A session that was cut off is finished, but not in the way finishing
      // means. It wears the dashed border the stale state used to — "the
      // outline of an agent rather than an agent" was always a better
      // description of a lost connection than of a slow one.
      if (p.reason === 'connection_lost') {
        return {
          className: 'agentStale',
          label: many ? `${agents} disconnected` : 'Agent disconnected',
          detail: `last seen ${silentFor(p.last_seen_at, now)} ago`,
        }
      }
      return { className: 'agentDone', label: many ? `${agents} finished` : 'Agent finished' }
  }
}

/**
 * Whether the board should keep asking the server about this list of cards.
 *
 * The fallback for a board with no socket — see `useBoardSocket`. True while
 * any card has a session still open: working can turn into waiting at any
 * moment, and waiting into working the moment somebody types. A card whose
 * only sessions are done changes when a person acts, and a person acting is a
 * mutation, which already refetches everything.
 */
export function hasLiveAgent(tasks: readonly { agent_session: AgentPresence | null }[]): boolean {
  return tasks.some((task) => task.agent_session !== null && task.agent_session.state !== 'done')
}
