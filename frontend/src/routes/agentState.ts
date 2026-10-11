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

import type {
  AgentPresence,
  AgentPrompt,
  AgentSessionRead,
  AgentSessionReason,
} from '../api/client'

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
export function agentIndicator(presence: AgentPresence | null, now: Date): AgentIndicator | null {
  if (!presence) return null
  const many = presence.count > 1
  const agents = many ? `${presence.count} agents` : 'Agent'

  switch (presence.state) {
    case 'waiting': {
      // A question or a permission says what it is about, which is the one
      // thing a person deciding whether to stop what they are doing needs:
      // "Asks: keep the cookie name per environment?" rather than "needs you".
      const asking = isAnswerable(presence.prompt) ? promptLine(presence.prompt) : null
      if (asking) {
        return many
          ? { className: 'agentWaiting', label: `${agents} · one needs you`, detail: asking }
          : { className: 'agentWaiting', label: asking }
      }
      return {
        className: 'agentWaiting',
        label: many ? `${agents} · one needs you` : 'Agent needs you',
        detail: waitingDetail(presence.reason),
      }
    }
    case 'working': {
      // Just answered: the card says what you said — "You: Allow once" — for as
      // long as the server keeps the answer on it, which is until the session
      // next changes state. The agent's own action, once it has one, follows.
      const answered = presence.prompt?.status === 'answered' ? promptLine(presence.prompt) : null
      if (answered) {
        if (many) return { className: 'agentWorking', label: `${agents} working`, detail: answered }
        return presence.note
          ? { className: 'agentWorking', label: answered, detail: presence.note }
          : { className: 'agentWorking', label: answered }
      }
      // What it is doing, when it has said: "Running pytest test_pr_states.py"
      // tells a person more than "Agent working" does, and the dot and the
      // border already say it is an agent. With several on the card the count
      // is the headline and the deciding session's action the detail.
      if (presence.note) {
        return many
          ? { className: 'agentWorking', label: `${agents} working`, detail: presence.note }
          : { className: 'agentWorking', label: presence.note }
      }
      return { className: 'agentWorking', label: `${agents} working` }
    }
    case 'done':
      // A session that was cut off is finished, but not in the way finishing
      // means. It wears the dashed border the stale state used to — "the
      // outline of an agent rather than an agent" was always a better
      // description of a lost connection than of a slow one.
      if (presence.reason === 'connection_lost') {
        return {
          className: 'agentStale',
          label: many ? `${agents} disconnected` : 'Agent disconnected',
          detail: `last seen ${silentFor(presence.last_seen_at, now)} ago`,
        }
      }
      return { className: 'agentDone', label: many ? `${agents} finished` : 'Agent finished' }
  }
}

/**
 * The task's Now line for one session: what it is doing this moment, or null
 * once it has stopped doing anything.
 *
 * The session's own note when it sent one — the hook sums up every tool call
 * as it starts, and passes the harness's words on when it stops to ask — and
 * otherwise the plainest thing that is still true.
 */
export function nowLine(
  session: Pick<AgentSessionRead, 'state' | 'reason' | 'note'>,
): string | null {
  if (session.state === 'done') return null
  if (session.note) return session.note
  if (session.state === 'working') return 'Working'
  switch (session.reason) {
    case 'permission':
      return 'Waiting for permission'
    case 'question':
      return 'Waiting for an answer'
    case 'idle':
      return 'Idle'
    default:
      return 'Waiting for your reply'
  }
}

/**
 * The one line a prompt reads as on a card (CYLIST-85, CYLIST-89), or null
 * when there is nothing worth saying.
 *
 * Open, it says what is being asked — the question, or what the tool wants
 * to do. Answered, it says what the person said, as "You: …", which the card
 * keeps until the session next changes state. A prompt that closed without an
 * answer says why only when that is news to the person looking: answered in
 * the terminal, or nobody answered in time.
 */
export function promptLine(prompt: AgentPrompt | null): string | null {
  if (!prompt) return null
  switch (prompt.status) {
    case 'open':
      return prompt.kind === 'question' ? `Asks: ${prompt.text}` : `Permission: ${prompt.text}`
    case 'answered':
      return `You: ${prompt.answer ?? ''}`
    case 'expired':
      return 'Nobody answered in time'
    case 'withdrawn':
      return prompt.closed_reason === 'answered_in_terminal' ? 'Answered in the terminal' : null
  }
}

/**
 * Whether a person can still answer this from the board.
 *
 * With `now`, a prompt past its `expires_at` is not, even before the server
 * has said so: the server refuses an answer from that moment, and offering
 * buttons that can only fail is worse than offering none.
 */
export function isAnswerable(
  prompt: AgentPrompt | null | undefined,
  now?: Date,
): prompt is AgentPrompt {
  if (!prompt || prompt.status !== 'open') return false
  return now === undefined || new Date(prompt.expires_at).getTime() > now.getTime()
}

/**
 * A card as it reads the moment its prompt was answered, before the board has
 * refetched: working again, saying "You: …". Exactly what the server does to
 * the session — `_resume` clears the reason and the note and restarts the
 * clock — so the refetch that follows changes nothing a person can see.
 *
 * Only the prompt the card is showing is replaced; a card whose presence has
 * since moved on to another prompt, or to no agent at all, is left alone.
 */
export function withPromptAnswered<T extends { agent_session: AgentPresence | null }>(
  task: T,
  answered: AgentPrompt,
): T {
  const presence = task.agent_session
  if (!presence || presence.prompt?.id !== answered.id) return task
  // With several sessions on the card another may still be waiting, and which
  // one the card then shows is the server's to say: take the buttons away and
  // leave the rest to the refetch.
  if (presence.count > 1) return { ...task, agent_session: { ...presence, prompt: answered } }
  return {
    ...task,
    agent_session: {
      ...presence,
      state: 'working',
      reason: null,
      note: null,
      since: answered.answered_at ?? presence.since,
      prompt: answered,
    },
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
