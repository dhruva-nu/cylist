/**
 * The board's search box: free text plus `col:`, `who:`, `goal:`, `blk:`,
 * `hld:` and `cnl:` tags (see `boardSearch.ts`). Typing `col:`, `who:` or
 * `goal:` opens a suggestion list of the matching columns, people or goals —
 * arrow keys to move through it, enter or a click to accept, escape to dismiss
 * it without losing the token being typed.
 *
 * A query long enough to be worth tagging is long enough to be a nuisance to
 * select and delete, so once there is anything in the box an × sits at its
 * right to empty it in one go. It appears only when there is something to
 * clear — an × over an empty box is a control that does nothing — and hands
 * focus back to the input, because clearing the search is almost always the
 * start of the next one rather than the end of searching.
 */

import { useRef, useState, type KeyboardEvent } from 'react'
import type { BoardColumn, Goal, Person } from '../../api/client'
import { activeToken, applySuggestion, suggestionsFor, type Suggestion } from '../boardSearch'
import styles from './SearchBar.module.css'

/** The tags the search box understands, as offered under it — see `boardSearch.ts`. */
const SEARCH_TAGS = [
  { token: 'col:', says: 'in a column' },
  { token: 'who:', says: 'owned by someone' },
  { token: 'goal:', says: 'on a goal' },
  { token: 'blk:', says: 'blocked' },
  { token: 'hld:', says: 'on hold' },
  { token: 'cnl:', says: 'cancelled' },
] as const

const SUGGESTION_KINDS: Record<Suggestion['kind'], string> = {
  column: 'Column',
  assignee: 'Assignee',
  goal: 'Goal',
}

export function SearchBar({
  query,
  onChange,
  columns,
  members,
  goals,
}: {
  query: string
  onChange: (query: string) => void
  columns: BoardColumn[]
  members: Person[]
  goals: Goal[]
}) {
  const [highlighted, setHighlighted] = useState(0)
  const [dismissed, setDismissed] = useState(false)
  const [focused, setFocused] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const suggestions = dismissed ? [] : suggestionsFor(activeToken(query), columns, members, goals)

  /** A new query, typed or cleared: the list opens afresh, from its first row. */
  function replaceQuery(next: string) {
    onChange(next)
    setDismissed(false)
    setHighlighted(0)
  }

  function pick(suggestion: Suggestion) {
    onChange(applySuggestion(query, suggestion))
    setHighlighted(0)
  }

  /** The suggestion list's keys: up and down through it, enter to take one, escape to shut it. */
  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (!suggestions.length) return
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setHighlighted((current) => (current + 1) % suggestions.length)
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setHighlighted((current) => (current - 1 + suggestions.length) % suggestions.length)
    } else if (event.key === 'Enter') {
      const choice = suggestions[highlighted]
      if (choice) {
        event.preventDefault()
        pick(choice)
      }
    } else if (event.key === 'Escape') {
      setDismissed(true)
    }
  }

  function clear() {
    // The list is keyed off the token being typed, so emptying the box closes
    // it anyway; the reset is for the next query, which would otherwise open
    // its list still dismissed and with a stale row highlighted.
    replaceQuery('')
    input.current?.focus()
  }

  return (
    <div className={styles.search}>
      <input
        ref={input}
        type="text"
        className={query ? styles.clearable : undefined}
        value={query}
        onChange={(event) => replaceQuery(event.target.value)}
        onKeyDown={onKeyDown}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        placeholder="Search cards"
        aria-label="Search tasks"
        aria-autocomplete="list"
        aria-expanded={suggestions.length > 0}
      />
      {query ? (
        <button
          type="button"
          className={styles.clearSearch}
          aria-label="Clear the search"
          // Both, and each for its own reason: mousedown to swallow the focus
          // the pointer would otherwise take off the input, click to do the
          // work — a keyboard press on the button raises click and no
          // mousedown at all, and clearing on mousedown alone leaves the ×
          // dead to everyone tabbing to it.
          onMouseDown={(event) => event.preventDefault()}
          onClick={clear}
        >
          ×
        </button>
      ) : null}
      {/* The tags, offered while the box is empty and waiting. They used to
          be the placeholder, which made the box a line of syntax to read
          past every visit; here they are a list to pick from the once you
          want one, and gone the moment you type. */}
      {focused && !query && !dismissed ? (
        <ul className={styles.suggestions} aria-label="Search tags">
          {SEARCH_TAGS.map((tag) => (
            <li key={tag.token}>
              <button
                type="button"
                tabIndex={-1}
                onMouseDown={(event) => {
                  event.preventDefault()
                  onChange(tag.token)
                }}
              >
                <code className={styles.tagToken}>{tag.token}</code>
                <span className={styles.tagSays}>{tag.says}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {suggestions.length ? (
        <ul className={styles.suggestions} role="listbox">
          {suggestions.map((suggestion, index) => (
            <li key={`${suggestion.kind}:${suggestion.value}`} role="presentation">
              <button
                type="button"
                role="option"
                aria-selected={index === highlighted}
                className={index === highlighted ? styles.suggestionActive : ''}
                // mousedown, not click: click fires after the input's blur, by
                // which point the list has already unmounted for having lost
                // focus, and the pick never happens.
                onMouseDown={(event) => {
                  event.preventDefault()
                  pick(suggestion)
                }}
              >
                <span className={styles.suggestionKind}>{SUGGESTION_KINDS[suggestion.kind]}</span>
                {/* The goal's own colour beside its name, because that is what
                    the tag is really selecting: the rail on the cards it is
                    about to leave on the board. */}
                {suggestion.colour ? (
                  <span
                    className={styles.suggestionDot}
                    style={{ background: suggestion.colour }}
                    aria-hidden="true"
                  />
                ) : null}
                {suggestion.value}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}
