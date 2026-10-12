/**
 * The rules behind answering an agent from Cylist: what the box says above a
 * prompt, which answer a click or a typed reply sends, how each choice is
 * drawn, what a refused answer reads as, and what the cards and the task
 * look like the moment an answer lands.
 *
 * Shared by the board's popover (CYLIST-86) and the task's agent panel
 * (CYLIST-87) through `AgentPromptAnswer`, so the two can never disagree on
 * any of it.
 */

import {
  ApiError,
  type AgentPrompt,
  type AgentPromptAnswer,
  type AgentSessionRead,
  type Task,
  type TaskDetail,
} from '../api/client'
import { silentFor, withPromptAnswered } from '../routes/agentState'

/** "Question from the agent · 5m ago", "Permission asked · 1m ago". */
export function promptHeading(prompt: AgentPrompt, now: Date): string {
  const what = prompt.kind === 'question' ? 'Question from the agent' : 'Permission asked'
  const waited = silentFor(prompt.asked_at, now)
  return `${what} · ${waited === '0m' ? 'just now' : `${waited} ago`}`
}

/**
 * A typed reply as an answer, or null when there is nothing to send — an
 * empty box, or a prompt that does not take one (a permission is answered
 * with one of its three choices, and the server refuses anything else).
 */
export function replyAnswer(prompt: AgentPrompt, text: string): AgentPromptAnswer | null {
  if (!prompt.allows_reply) return null
  const trimmed = text.trim()
  return trimmed ? { text: trimmed } : null
}

export type ChoiceTone = 'go' | 'plain' | 'danger'

/**
 * How a choice is drawn. A permission's "Allow once" is the green the app
 * uses for yes and "Deny" the red it uses for no; every other choice — and
 * every choice a question offers, none of which is the safe one by default —
 * is plain.
 */
export function choiceTone(prompt: AgentPrompt, key: string): ChoiceTone {
  if (prompt.kind !== 'permission') return 'plain'
  if (key === 'allow_once') return 'go'
  if (key === 'deny') return 'danger'
  return 'plain'
}

/**
 * What a failed answer says. A conflict is the server explaining that the
 * prompt closed first — "Already answered by Aditi: Grey." — which is the
 * whole story, so it is said as it came. Anything else is a failure to send.
 */
export function answerFailure(error: unknown): string {
  if (error instanceof ApiError && error.code === 'conflict') return error.message
  const reason = error instanceof Error ? error.message : String(error)
  return `The answer did not reach the agent: ${reason}`
}

/** Whether a failure means the prompt is over, so the cached card is stale. */
export function promptClosedUnderUs(error: unknown): boolean {
  return error instanceof ApiError && error.code === 'conflict'
}

/**
 * One session as it reads once its prompt was answered: working again, with
 * no reason and no note, its clock restarted — what the server's `_resume`
 * does. A session showing some other prompt is left as it is.
 */
export function sessionWithPromptAnswered(
  session: AgentSessionRead,
  answered: AgentPrompt,
): AgentSessionRead {
  if (session.prompt?.id !== answered.id) return session
  const at = answered.answered_at ?? session.state_changed_at
  return {
    ...session,
    state: session.ended_at === null ? 'working' : session.state,
    reason: session.ended_at === null ? null : session.reason,
    note: session.ended_at === null ? null : session.note,
    state_changed_at: session.ended_at === null ? at : session.state_changed_at,
    prompt: answered,
  }
}

/** A task's detail once one of its prompts was answered. */
export function detailWithPromptAnswered(detail: TaskDetail, answered: AgentPrompt): TaskDetail {
  if (detail.id !== answered.task_id) return detail
  return withPromptAnswered(
    {
      ...detail,
      agent_sessions: detail.agent_sessions.map((session) =>
        sessionWithPromptAnswered(session, answered),
      ),
    },
    answered,
  )
}

/** A board's cards once one of its prompts was answered. */
export function tasksWithPromptAnswered(tasks: Task[], answered: AgentPrompt): Task[] {
  return tasks.map((task) =>
    task.id === answered.task_id ? withPromptAnswered(task, answered) : task,
  )
}
