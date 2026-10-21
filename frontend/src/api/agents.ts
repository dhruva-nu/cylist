/** The agents working on a card: their sessions, what they ask, and what they do. */

import { jsonBody, request } from './http'
import { MAX_PAGE, fetchPage } from './paging'

export type AgentSessionState = 'working' | 'waiting' | 'done'
export type AgentSessionReason =
  | 'turn_ended'
  | 'permission'
  | 'idle'
  | 'question'
  | 'moved'
  | 'session_ended'
  /** The socket went away without a goodbye: it dropped, or it went quiet
   * past the idle window. What a card draws as the dashed outline. */
  | 'connection_lost'

/** One harness session — a Claude Code conversation — on one card. */
export interface AgentSessionRead {
  id: string
  task_id: string
  /** The name of the token the hook reports with. */
  actor_label: string
  /** The session's display name, if known. Usually the card's reference. */
  client_name: string | null
  state: AgentSessionState
  reason: AgentSessionReason | null
  /** What the session is doing right now — "Running pytest test_pr_states.py" —
   * from its latest tool call, or what it is waiting on. Null when it has not said. */
  note: string | null
  started_at: string
  /** When `state` last changed. */
  state_changed_at: string
  /** When the session last reported in, over its socket or the HTTP route. */
  last_seen_at: string
  ended_at: string | null
  dismissed_at: string | null
  /** What the session is waiting on a person for while it is; otherwise the
   * last prompt it closed since entering its current state, so a card can say
   * "You: Allow once". Null when neither. */
  prompt: AgentPrompt | null
}

/** What an agent is waiting on a person for (CYLIST-85, CYLIST-89). */
export type AgentPromptKind = 'question' | 'permission'

/**
 * Where a prompt has got to. Only `open` is waiting on anyone: `answered` was
 * answered on the board, `expired` ran out of time, `withdrawn` stopped
 * waiting first — see `closed_reason`.
 */
export type AgentPromptStatus = 'open' | 'answered' | 'expired' | 'withdrawn'

/** Why a withdrawn prompt was withdrawn. */
export type AgentPromptClosedReason =
  'answered_in_terminal' | 'interrupted' | 'abandoned' | 'session_ended'

/** One choice a prompt offers. */
export interface AgentPromptOption {
  /** What an answer names to pick this: `allow_once`, or `1` for a question's first. */
  key: string
  /** What the button says. */
  label: string
}

/** The keys a permission's options always have, in the order they are offered. */
export type PermissionAnswerKey = 'allow_once' | 'allow_session' | 'deny'

/**
 * Something an agent session is waiting on a person for, or was: a question
 * it asked with `ask_human`, or a permission Claude Code wants before a tool
 * runs. Mirrors `AgentPromptRead` in `app/schemas/agent_sessions.py`.
 */
export interface AgentPrompt {
  id: string
  agent_session_id: string
  task_id: string
  kind: AgentPromptKind
  /** The question, or for a permission what the tool wants to do. */
  text: string
  /** For a permission, the tool's own name: `Bash`. */
  tool: string | null
  /** The choices, in order. A permission's are always the three `PermissionAnswerKey`s. */
  options: AgentPromptOption[]
  /** Whether a typed reply is accepted as well as a choice — a question's are. */
  allows_reply: boolean
  /**
   * Whose agent is asking, as a person id (CYLIST-141). A permission may be
   * answered by this person alone — the tool runs on their machine; a
   * question by anyone who may edit the card. Null for a session nobody owns.
   */
  owner_id: string | null
  /** Whose agent is asking, by name. */
  owner: string | null
  status: AgentPromptStatus
  /** What was answered: the chosen option's label, or the reply typed. */
  answer: string | null
  /** The chosen option's key; null for a typed reply. */
  answer_key: string | null
  /** Who answered, by name. */
  answered_by: string | null
  /**
   * Who answered, as a person id — compared with `api.me()`'s to say "You: …"
   * rather than the name. Null when unanswered, or answered with a token that
   * is nobody's.
   */
  answered_by_id: string | null
  answered_at: string | null
  closed_reason: AgentPromptClosedReason | null
  asked_at: string
  /** After this nobody may answer it. */
  expires_at: string
  closed_at: string | null
}

/** A person's answer: one of the options by key, or — to a question — a typed reply. */
export type AgentPromptAnswer = { key: string } | { text: string }

export type AgentPresenceState = 'working' | 'waiting' | 'done'

/** What one line of an agent's feed is. */
export type AgentFeedKind = 'said' | 'read' | 'edit' | 'run' | 'tool' | 'output' | 'error'

/**
 * One line of what an agent read, edited, ran or said on a card (CYLIST-88).
 * Written by the agent's presence daemon from its transcript, redacted on the
 * agent's machine; each session keeps its newest 200.
 */
export interface AgentFeedEntry {
  id: string
  agent_session_id: string
  /** The session's display name, if known. */
  client_name: string | null
  /** When the agent did it, by its own clock. */
  at: string
  kind: AgentFeedKind
  /** The tool's own name — `Bash`, `Edit` — for every kind but `said`. */
  tool: string | null
  text: string
}

/**
 * What a card says about the agents on it, reduced to the one state its border
 * shows. `waiting` wins over `working` wins over `done`.
 *
 * There was a fourth, `stale`, for a working session gone quiet past a
 * threshold — back when nothing ended the row of a process that had died. The
 * server ends them now, so a dropped agent arrives here as `done` with a
 * `reason` of `connection_lost` rather than as a guess about the clock.
 */
export interface AgentPresence {
  state: AgentPresenceState
  /** How many sessions this summarises: the open ones while any is open, the
   * finished-but-undismissed ones once none are. */
  count: number
  /** The deciding session's reason. */
  reason: AgentSessionReason | null
  /** The deciding session's name. */
  client_name: string | null
  /** What the deciding session is doing right now — its current tool call. */
  note: string | null
  /** When the deciding session entered its state. */
  since: string
  /** When the deciding session last reported in. */
  last_seen_at: string
  /** The deciding session's open question or permission, answerable from the
   * card — or, once answered, the answer, until the session next changes state. */
  prompt: AgentPrompt | null
}

export const agentsApi = {
  /** What the agents on a card have been doing, newest first. */
  getAgentFeed: async (taskRef: string, limit = MAX_PAGE) =>
    (await fetchPage<AgentFeedEntry>(`/tasks/${taskRef}/agent-feed`, null, limit)).items,
  /** What the agents on a card have asked, open or not, newest first. */
  listAgentPrompts: async (taskRef: string, limit = 20) =>
    (await fetchPage<AgentPrompt>(`/tasks/${taskRef}/agent-prompts`, null, limit)).items,
  /**
   * Answer what an agent on a card is waiting on. The first answer wins: one
   * already answered, expired or withdrawn comes back as a `conflict` error
   * saying which. The agent is told at once and the card goes back to working.
   */
  answerAgentPrompt: (taskRef: string, promptId: string, answer: AgentPromptAnswer) =>
    request<AgentPrompt>(`/tasks/${taskRef}/agent-prompts/${promptId}/answer`, {
      method: 'POST',
      body: jsonBody(answer),
    }),
  /** Clear the finished sessions off a card. The ones still running stay. */
  dismissAgentSessions: (taskRef: string) =>
    request<void>(`/tasks/${taskRef}/agent-sessions/dismiss`, { method: 'POST' }),
}
