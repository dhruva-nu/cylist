/**
 * The Hooks page's words and its form, kept apart from the page so both can
 * be tested without a browser.
 *
 * A hook is read back to its admin as a sentence of parts — "Card moved ·
 * Hotfix cards · into In staging" — because that is how it was thought of
 * before it was written: which change, to which cards, where.
 */

import type {
  DeliveryState,
  Hook,
  HookDelivery,
  HookEvent,
  HookInput,
  TaskType,
} from '../api/client'

/** What the form holds. Strings throughout, '' meaning "no filter". */
export interface HookDraft {
  name: string
  url: string
  /**
   * "Any change" chosen outright. Kept apart from `verbs` so switching to it
   * and back does not lose what was ticked; the server only ever hears
   * `verbs`, empty for this.
   */
  anyChange: boolean
  verbs: string[]
  toColumnId: string
  fromColumnId: string
  templateId: string
  taskType: '' | TaskType
  secret: string
}

export const EMPTY_DRAFT: HookDraft = {
  name: '',
  url: '',
  anyChange: false,
  verbs: [],
  toColumnId: '',
  fromColumnId: '',
  templateId: '',
  taskType: '',
  secret: '',
}

export function draftOf(hook: Hook): HookDraft {
  return {
    name: hook.name,
    url: hook.url,
    anyChange: hook.verbs.length === 0,
    verbs: hook.verbs,
    toColumnId: hook.to_column_id ?? '',
    fromColumnId: hook.from_column_id ?? '',
    templateId: hook.template_id ?? '',
    taskType: hook.task_type ?? '',
    secret: '',
  }
}

/**
 * The form as the server reads it. Every filter is sent, null when empty, so
 * an edit that clears one clears it rather than leaving it alone.
 */
export function inputOf(draft: HookDraft, creating: boolean): HookInput {
  const input: HookInput = {
    name: draft.name.trim(),
    url: draft.url.trim(),
    verbs: draft.anyChange ? [] : draft.verbs,
    to_column_id: draft.toColumnId || null,
    from_column_id: draft.fromColumnId || null,
    template_id: draft.templateId || null,
    task_type: draft.taskType || null,
  }
  if (creating && draft.secret.trim() !== '') input.secret = draft.secret.trim()
  return input
}

/** Whether the server will take this as a hook's URL. */
export function isHookUrl(url: string): boolean {
  try {
    const parsed = new URL(url.trim())
    return (parsed.protocol === 'http:' || parsed.protocol === 'https:') && parsed.host !== ''
  } catch {
    return false
  }
}

/** Why the form cannot be saved yet, or null when it can. */
export function whyIncomplete(
  draft: HookDraft,
  hooks: Hook[],
  editing: Hook | null,
): string | null {
  const name = draft.name.trim()
  if (name === '') return 'Give the hook a name.'
  if (hooks.some((hook) => hook.name === name && hook.id !== editing?.id)) {
    return `This project already has a hook called ${name}.`
  }
  if (!isHookUrl(draft.url)) return 'The URL must start with http:// or https://.'
  if (!draft.anyChange && draft.verbs.length === 0) {
    return 'Tick at least one event, or choose Any change.'
  }
  if (draft.fromColumnId !== '' && !draft.anyChange && !draft.verbs.includes('task.moved')) {
    return '"Moved from" only matches moves — tick Moved to another column.'
  }
  return null
}

/** One of the picker's groups — "Card moves" — and the events in it. */
export interface EventGroup {
  id: string
  name: string
  events: HookEvent[]
}

/** The catalogue as the picker shows it: groups in the order the server listed them. */
export function groupEvents(events: HookEvent[]): EventGroup[] {
  const groups: EventGroup[] = []
  for (const event of events) {
    const last = groups[groups.length - 1]
    if (last?.id === event.category) last.events.push(event)
    else groups.push({ id: event.category, name: event.category_name, events: [event] })
  }
  return groups
}

/** How many of a group's events are ticked. */
export function pickedIn(group: EventGroup, verbs: string[]): number {
  return group.events.filter((event) => verbs.includes(event.verb)).length
}

/**
 * The ticked verbs with some switched on or off, kept in catalogue order so
 * the same set always reads — and saves — the same way.
 */
export function withVerbs(
  verbs: string[],
  changing: string[],
  on: boolean,
  events: HookEvent[],
): string[] {
  const wanted = new Set(verbs)
  for (const verb of changing) {
    if (on) wanted.add(verb)
    else wanted.delete(verb)
  }
  return events.map((event) => event.verb).filter((verb) => wanted.has(verb))
}

/**
 * Which change, in the page's words. A group ticked whole is named as the
 * group — "Card moves" — and the rest by their own labels; past two, the
 * line says how many more rather than running on.
 */
export function eventsPhrase(verbs: string[], events: HookEvent[]): string {
  if (verbs.length === 0) return 'Any change'
  const parts: string[] = []
  for (const group of groupEvents(events)) {
    const picked = group.events.filter((event) => verbs.includes(event.verb))
    if (picked.length === 0) continue
    if (picked.length === group.events.length && group.events.length > 1) parts.push(group.name)
    else parts.push(...picked.map((event) => event.label))
  }
  // A verb the catalogue no longer lists still fired once; name it as stored.
  const known = new Set(events.map((event) => event.verb))
  parts.push(...verbs.filter((verb) => !known.has(verb)))
  if (parts.length <= 2) return parts.join(' or ')
  return `${parts[0]}, ${parts[1]} and ${parts.length - 2} more`
}

const TYPE_WORDS: Record<TaskType, string> = { feature: 'Features', bug: 'Bugs', chore: 'Chores' }

/** The rule as a line of parts: which change · which cards · where. */
export function describeRule(hook: Hook, events: HookEvent[]): string {
  const parts = [eventsPhrase(hook.verbs, events)]
  if (hook.template_id) parts.push(`${hook.template_name ?? 'a deleted template'} cards`)
  if (hook.task_type) parts.push(TYPE_WORDS[hook.task_type])
  if (hook.from_column_id) parts.push(`from ${hook.from_column_name ?? 'a deleted column'}`)
  if (hook.to_column_id) {
    const movesOnly = hook.verbs.length === 1 && hook.verbs[0] === 'task.moved'
    parts.push(`${movesOnly ? 'into' : 'in'} ${hook.to_column_name ?? 'a deleted column'}`)
  }
  return parts.join(' · ')
}

/** Whether a filter of this hook names something since deleted — and so matches nothing. */
export function isDangling(hook: Hook): boolean {
  return (
    (hook.to_column_id !== null && hook.to_column_name === null) ||
    (hook.from_column_id !== null && hook.from_column_name === null) ||
    (hook.template_id !== null && hook.template_name === null)
  )
}

/**
 * When an attempt was made, to the second. A day alone — what the rest of the
 * app stamps things with — cannot tell a first attempt from the retry thirty
 * seconds later, which is the question a delivery log is read to answer.
 */
export function attemptTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

export const STATE_WORDS: Record<DeliveryState, string> = {
  pending: 'Queued',
  delivered: 'Delivered',
  failed: 'Failed',
}

/** How a delivery stands, in one line: what came back and what happens next. */
export function deliveryOutcome(delivery: HookDelivery, now: Date = new Date()): string {
  const last = delivery.attempts[delivery.attempts.length - 1]
  if (delivery.state === 'delivered' && last) {
    return `Delivered — ${last.status_code} in ${last.duration_ms} ms`
  }
  const why = delivery.last_error ?? 'no answer'
  if (delivery.state === 'failed') {
    const tries = delivery.attempt_count === 1 ? '1 attempt' : `${delivery.attempt_count} attempts`
    return `Failed after ${tries} — ${why}`
  }
  if (delivery.attempt_count === 0 || delivery.next_attempt_at === null) return 'Queued'
  const due = new Date(delivery.next_attempt_at)
  const wait = Math.max(0, Math.round((due.getTime() - now.getTime()) / 60000))
  const when = wait < 1 ? 'shortly' : wait < 60 ? `in ${wait} min` : `in ${Math.round(wait / 60)} h`
  return `Retrying ${when} — ${why}`
}
