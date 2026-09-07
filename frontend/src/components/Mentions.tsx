/**
 * The box you type an `@` tag in. `Tagged`, in `ui.tsx`, is how one reads
 * back; `mentions.ts` is what both of them agree a tag is.
 *
 * The suggestion list is the board search box's, deliberately: this project
 * already has one way of offering names to a half-typed word, and a second
 * one that behaved differently would be a thing to learn rather than a thing
 * to recognise.
 */

import { useRef, useState, type KeyboardEvent } from 'react'

import type { Person } from '../api/client'
import { applyMention, matchingMembers, mentionQuery } from './mentions'
import styles from './Mentions.module.css'
import { Avatar } from './ui'

interface MentionBoxProps {
  value: string
  onChange: (value: string) => void
  members: Person[]
  /** A textarea rather than a one-line input. */
  multiline?: boolean
  rows?: number
  placeholder?: string
  maxLength?: number
  'aria-label'?: string
  /**
   * What enter does when no suggestion is showing — submit the line, in a box
   * that is a composer rather than a field. While the list is open enter picks
   * a name instead, which is why this is offered here rather than left to the
   * caller's own `onKeyDown`: the box is the only thing that knows.
   */
  onEnter?: (() => void) | undefined
  /**
   * For the wrapper, not the field. The suggestion list is positioned against
   * the wrapper, so the wrapper has to be the element the surrounding layout
   * sizes — a row that stretched the input inside a wrapper that had not
   * grown would leave the list the width of nothing.
   */
  className?: string | undefined
}

/**
 * An input or textarea where `@` offers the people on the project.
 *
 * Arrow keys move through the list, enter or tab accepts, escape dismisses it
 * without disturbing what was typed — so `@` can also just be an `@`. The list
 * closes on its own as soon as what has been typed matches nobody, which is
 * what keeps a name with a space in it ("Aditi K") completable without the
 * menu hanging around over the rest of the sentence.
 */
export function MentionBox({
  value,
  onChange,
  members,
  multiline = false,
  rows,
  placeholder,
  maxLength,
  'aria-label': label,
  onEnter,
  className,
}: MentionBoxProps) {
  const field = useRef<HTMLInputElement | HTMLTextAreaElement>(null)
  const [caret, setCaret] = useState(0)
  const [highlighted, setHighlighted] = useState(0)
  const [dismissed, setDismissed] = useState(false)

  const draft = dismissed ? null : mentionQuery(value, caret)
  const matches = draft ? matchingMembers(draft.query, members) : []

  function pick(person: Person) {
    if (!draft) return
    const next = applyMention(value, draft, person.name)
    onChange(next.value)
    setHighlighted(0)
    // The caret belongs after the name just inserted, not at the end of the
    // text — a tag added halfway through a sentence must leave you halfway
    // through it. React has not written `value` back yet, so this waits.
    requestAnimationFrame(() => {
      field.current?.setSelectionRange(next.caret, next.caret)
      setCaret(next.caret)
    })
  }

  /** Where the caret is after whatever just happened to the field. */
  function track(target: HTMLInputElement | HTMLTextAreaElement) {
    setCaret(target.selectionStart ?? target.value.length)
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) {
    if (!matches.length) {
      if (event.key === 'Enter' && onEnter) {
        event.preventDefault()
        onEnter()
      }
      return
    }
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setHighlighted((current) => (current + 1) % matches.length)
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setHighlighted((current) => (current - 1 + matches.length) % matches.length)
    } else if (event.key === 'Enter' || event.key === 'Tab') {
      const choice = matches[highlighted]
      if (choice) {
        event.preventDefault()
        pick(choice)
      }
    } else if (event.key === 'Escape') {
      // Stopped here rather than allowed to close the dialog: dismissing the
      // list is what escape means while the list is open.
      event.preventDefault()
      event.stopPropagation()
      setDismissed(true)
    }
  }

  const shared = {
    value,
    placeholder,
    maxLength,
    'aria-label': label,
    'aria-autocomplete': 'list' as const,
    'aria-expanded': matches.length > 0,
    onChange: (event: { target: HTMLInputElement | HTMLTextAreaElement }) => {
      onChange(event.target.value)
      setDismissed(false)
      setHighlighted(0)
      track(event.target)
    },
    onKeyDown,
    // The caret moves for reasons no keystroke reports: a click into the
    // middle of a sentence, a drag, an arrow key. Both cover it.
    onKeyUp: (event: { currentTarget: HTMLInputElement | HTMLTextAreaElement }) =>
      track(event.currentTarget),
    onClick: (event: { currentTarget: HTMLInputElement | HTMLTextAreaElement }) =>
      track(event.currentTarget),
    onBlur: () => setDismissed(true),
    onFocus: () => setDismissed(false),
  }

  return (
    <div className={className ? `${styles.box} ${className}` : styles.box}>
      {multiline ? (
        <textarea ref={field as React.RefObject<HTMLTextAreaElement>} rows={rows} {...shared} />
      ) : (
        <input ref={field as React.RefObject<HTMLInputElement>} type="text" {...shared} />
      )}

      {matches.length ? (
        <ul className={styles.suggestions} role="listbox">
          {matches.map((person, index) => (
            <li key={person.id} role="presentation">
              <button
                type="button"
                role="option"
                aria-selected={index === highlighted}
                className={index === highlighted ? styles.suggestionActive : ''}
                // mousedown, not click: click lands after the field's blur has
                // already closed the list, so the pick never happens.
                onMouseDown={(event) => {
                  event.preventDefault()
                  pick(person)
                }}
              >
                <Avatar name={person.name} colour={person.colour} />
                {person.name}
                <span className={styles.suggestionRole}>{person.role}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}
