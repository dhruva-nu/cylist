/**
 * How a line of an agent's feed reads: the word in front of it, and its time.
 *
 * Kept out of the component for the reason `agentState.ts` is — the words are
 * rules, and a feed that labelled a shell command READ, or an error as plain
 * output, would be wrong in a way nobody goes looking for.
 */

import type { AgentFeedKind } from '../api/client'

/** The label in front of a line, as the feed's column shows it. */
export function feedLabel(kind: AgentFeedKind): string {
  switch (kind) {
    case 'said':
      return 'Said'
    case 'read':
      return 'Read'
    case 'edit':
      return 'Edit'
    case 'run':
      return 'Ran'
    case 'output':
      return 'Got'
    case 'error':
      return 'Failed'
    case 'tool':
      return 'Tool'
  }
}

/** Whether a line is code-shaped — a path, a command, its output — and set in mono. */
export function isCodeLike(kind: AgentFeedKind): boolean {
  return kind !== 'said'
}

/** `14:31`, in the reader's own time zone. A feed is read the same day it is written. */
export function feedClock(at: string): string {
  const when = new Date(at)
  if (Number.isNaN(when.getTime())) return ''
  return when.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
}
