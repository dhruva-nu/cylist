/**
 * Parsing and filtering for the board's search box.
 *
 * Free text matches every word against a card's title; `col:`, `who:`,
 * `goal:`, `blk:` and `hld:` narrow by column, assignee, goal, or status.
 * Kept out of `ProjectBoard.tsx` because none of it touches React — it is
 * plain string and array manipulation, easiest to get right (and to change)
 * on its own.
 */

import type { BoardColumn, Goal, Person, Task } from '../api/client'

const TAG_PATTERN = /^(col|who|goal|blk|hld):"?([^"]*)$/i

/** What `goal:` matches to mean "on no goal at all". */
export const NO_GOAL = 'none'

export interface ParsedSearch {
  freeText: string[]
  /** The partial or full column name after `col:`, if any. */
  column: string | null
  /** The partial or full assignee name after `who:`, if any. */
  assignee: string | null
  /** The partial or full goal name after `goal:`, if any. `none` finds the
   * cards on no goal — as well as any goal actually called that, so naming
   * one "None" costs you nothing but a wider answer. */
  goal: string | null
  blocked: boolean
  hold: boolean
}

/**
 * Splits on whitespace, except inside `"double quotes"` — so `col:"In
 * progress"` survives as one token instead of breaking at the space. Quotes
 * themselves are stripped; they exist only to mark the boundary.
 */
export function tokenize(query: string): string[] {
  const tokens: string[] = []
  let current = ''
  let inQuotes = false

  for (const char of query) {
    if (char === '"') {
      inQuotes = !inQuotes
      continue
    }
    if (!inQuotes && /\s/.test(char)) {
      if (current) tokens.push(current)
      current = ''
      continue
    }
    current += char
  }
  if (current) tokens.push(current)

  return tokens
}

/** Classifies each token from {@link tokenize} into a tag or a free-text word. */
export function parseQuery(tokens: string[]): ParsedSearch {
  const parsed: ParsedSearch = {
    freeText: [],
    column: null,
    assignee: null,
    goal: null,
    blocked: false,
    hold: false,
  }

  for (const token of tokens) {
    const lower = token.toLowerCase()
    if (lower.startsWith('col:')) {
      const value = token.slice('col:'.length).trim()
      if (value) parsed.column = value
    } else if (lower.startsWith('who:')) {
      const value = token.slice('who:'.length).trim()
      if (value) parsed.assignee = value
    } else if (lower.startsWith('goal:')) {
      const value = token.slice('goal:'.length).trim()
      if (value) parsed.goal = value
    } else if (lower.startsWith('blk:')) {
      parsed.blocked = true
    } else if (lower.startsWith('hld:')) {
      parsed.hold = true
    } else {
      parsed.freeText.push(token)
    }
  }

  return parsed
}

/** Every filter is ANDed together: each active one narrows the set further. */
export function filterTasks(
  tasks: Task[],
  parsed: ParsedSearch,
  columns: BoardColumn[],
  members: Person[],
): Task[] {
  const matchedColumnIds = parsed.column
    ? new Set(
        columns
          .filter((column) => column.name.toLowerCase().includes(parsed.column!.toLowerCase()))
          .map((column) => column.id),
      )
    : null
  const matchedAssignees = parsed.assignee
    ? new Set(
        members
          .filter((person) => person.name.toLowerCase().includes(parsed.assignee!.toLowerCase()))
          .map((person) => person.id),
      )
    : null
  const goal = parsed.goal?.toLowerCase() ?? null
  const freeText = parsed.freeText.map((term) => term.toLowerCase())

  return tasks.filter((task) => {
    // A card with no column is a sub-task, which is not on the board and so
    // cannot be in the column being searched for.
    if (matchedColumnIds && (task.column_id === null || !matchedColumnIds.has(task.column_id))) {
      return false
    }
    if (matchedAssignees && !matchedAssignees.has(task.assignee.id)) return false
    if (goal !== null && !matchesGoal(task, goal)) return false
    if (parsed.blocked && task.status !== 'blocked') return false
    if (parsed.hold && task.status !== 'hold') return false
    if (freeText.length) {
      const title = task.title.toLowerCase()
      if (!freeText.every((term) => title.includes(term))) return false
    }
    return true
  })
}

/**
 * Whether a card answers to a `goal:` term.
 *
 * A card with no goal answers to `none` — the one question the goal list
 * cannot be asked, because what it is asking for is the absence of an entry
 * in it. Everything else is a substring of the goal's name, matched the way
 * `col:` and `who:` are.
 */
function matchesGoal(task: Task, term: string): boolean {
  if (task.goal_name === null) return term === NO_GOAL
  return task.goal_name.toLowerCase().includes(term)
}

/** The token the caret is inside, assuming it sits at the end of the input. */
export function activeToken(query: string): string {
  if (query === '' || query.endsWith(' ')) return ''
  const lastSpace = query.lastIndexOf(' ')
  return lastSpace === -1 ? query : query.slice(lastSpace + 1)
}

export interface Suggestion {
  kind: 'column' | 'assignee' | 'goal'
  value: string
  /** A goal's own colour, so the list shows the rail it stands for. */
  colour?: string
}

/** Candidates for the token being typed, when it is a `col:`, `who:` or
 * `goal:` tag. */
export function suggestionsFor(
  token: string,
  columns: BoardColumn[],
  members: Person[],
  goals: Goal[] = [],
): Suggestion[] {
  const match = TAG_PATTERN.exec(token)
  if (!match) return []

  const [, tag, partial] = match
  const needle = (partial ?? '').toLowerCase()

  if (tag?.toLowerCase() === 'col') {
    return columns
      .filter((column) => column.name.toLowerCase().includes(needle))
      .map((column) => ({ kind: 'column', value: column.name }))
  }
  if (tag?.toLowerCase() === 'who') {
    return members
      .filter((person) => person.name.toLowerCase().includes(needle))
      .map((person) => ({ kind: 'assignee', value: person.name }))
  }
  if (tag?.toLowerCase() === 'goal') {
    const named: Suggestion[] = goals
      .filter((candidate) => candidate.name.toLowerCase().includes(needle))
      .map((candidate) => ({ kind: 'goal', value: candidate.name, colour: candidate.colour }))
    // Offered last rather than first: it is the rarer question, and a list
    // that opens on it would put a word nobody typed above the goals they did.
    return NO_GOAL.includes(needle) ? [...named, { kind: 'goal', value: NO_GOAL }] : named
  }
  return []
}

const TAGS: Record<Suggestion['kind'], string> = {
  column: 'col:',
  assignee: 'who:',
  goal: 'goal:',
}

/** Replaces the token being typed with the chosen suggestion, quoting a name
 * that contains a space, and leaves a trailing space to start the next token. */
export function applySuggestion(query: string, suggestion: Suggestion): string {
  const lastSpace = query.lastIndexOf(' ')
  const prefix = lastSpace === -1 ? '' : query.slice(0, lastSpace + 1)
  const tag = TAGS[suggestion.kind]
  const value = suggestion.value.includes(' ') ? `"${suggestion.value}"` : suggestion.value
  return `${prefix}${tag}${value} `
}
