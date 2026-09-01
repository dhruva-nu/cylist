/**
 * Parsing and filtering for the board's search box.
 *
 * Free text matches every word against a card's title; `col:`, `who:`,
 * `blk:` and `hld:` narrow by column, assignee, or status. Kept out of
 * `ProjectBoard.tsx` because none of it touches React — it is plain string
 * and array manipulation, easiest to get right (and to change) on its own.
 */

import type { BoardColumn, Person, Task } from '../api/client'

const TAG_PATTERN = /^(col|who|blk|hld):"?([^"]*)$/i

export interface ParsedSearch {
  freeText: string[]
  /** The partial or full column name after `col:`, if any. */
  column: string | null
  /** The partial or full assignee name after `who:`, if any. */
  assignee: string | null
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
  const freeText = parsed.freeText.map((term) => term.toLowerCase())

  return tasks.filter((task) => {
    if (matchedColumnIds && !matchedColumnIds.has(task.column_id)) return false
    if (matchedAssignees && !matchedAssignees.has(task.assignee.id)) return false
    if (parsed.blocked && task.status !== 'blocked') return false
    if (parsed.hold && task.status !== 'hold') return false
    if (freeText.length) {
      const title = task.title.toLowerCase()
      if (!freeText.every((term) => title.includes(term))) return false
    }
    return true
  })
}

/** The token the caret is inside, assuming it sits at the end of the input. */
export function activeToken(query: string): string {
  if (query === '' || query.endsWith(' ')) return ''
  const lastSpace = query.lastIndexOf(' ')
  return lastSpace === -1 ? query : query.slice(lastSpace + 1)
}

export interface Suggestion {
  kind: 'column' | 'assignee'
  value: string
}

/** Candidates for the token being typed, when it is a `col:` or `who:` tag. */
export function suggestionsFor(
  token: string,
  columns: BoardColumn[],
  members: Person[],
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
  return []
}

/** Replaces the token being typed with the chosen suggestion, quoting a name
 * that contains a space, and leaves a trailing space to start the next token. */
export function applySuggestion(query: string, suggestion: Suggestion): string {
  const lastSpace = query.lastIndexOf(' ')
  const prefix = lastSpace === -1 ? '' : query.slice(0, lastSpace + 1)
  const tag = suggestion.kind === 'column' ? 'col:' : 'who:'
  const value = suggestion.value.includes(' ') ? `"${suggestion.value}"` : suggestion.value
  return `${prefix}${tag}${value} `
}
