/**
 * The box you type a tag in — `@` for a person, `>` for a file. `Tagged`, in
 * `ui.tsx`, is how one reads back; `mentions.ts` is what both of them agree a
 * tag is.
 *
 * The suggestion list is the board search box's, deliberately: this project
 * already has one way of offering names to a half-typed word, and a second
 * one that behaved differently would be a thing to learn rather than a thing
 * to recognise. Files are offered by that same list for the same reason —
 * whichever sigil is under the caret only changes what the rows say.
 */

import { useRef, useState, type KeyboardEvent } from 'react'

import type { FiledItem, Person } from '../api/client'
import { FILE_SIGIL, applyMention, matchingFiles, matchingMembers, mentionQuery } from './mentions'
import styles from './Mentions.module.css'
import { Avatar } from './ui'

/** What one row of the list offers, whichever kind of tag is being written. */
interface Suggestion {
  /** React's key, and what the tag will carry as its name. */
  id: string
  name: string
  /** Drawn at the left: an avatar for a person, a glyph for a file. */
  mark: React.ReactNode
  /** The quiet line at the right — a person's role, a file's folder. */
  note: string
}

/** Where a file sits, for the row's right-hand side. */
function whereItSits(file: FiledItem): string {
  if (file.kind === 'link') return file.folder_path ? `Link · ${file.folder_path}` : 'Link'
  return file.folder_path || 'Top level'
}

interface MentionBoxProps {
  value: string
  onChange: (value: string) => void
  members: Person[]
  /**
   * The project's files, for the `>` tag. Omitted where they are not to hand,
   * which leaves `>` an ordinary character rather than offering an empty list.
   */
  files?: readonly FiledItem[] | undefined
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
 * An input or textarea where `@` offers the people on the project and `>` its
 * files.
 *
 * Arrow keys move through the list, enter or tab accepts, escape dismisses it
 * without disturbing what was typed — so `@` can also just be an `@`, and `>`
 * a greater-than sign. The list closes on its own as soon as what has been
 * typed matches nothing, which is what keeps a name with a space in it
 * ("Aditi K", "Q3 report.pdf") completable without the menu hanging around
 * over the rest of the sentence.
 */
export function MentionBox({
  value,
  onChange,
  members,
  files,
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
  const matches: Suggestion[] = !draft
    ? []
    : draft.sigil === FILE_SIGIL
      ? matchingFiles(draft.query, files ?? []).map((file) => ({
          id: file.id,
          name: file.name,
          mark: <FileGlyph link={file.kind === 'link'} />,
          note: whereItSits(file),
        }))
      : matchingMembers(draft.query, members).map((person) => ({
          id: person.id,
          name: person.name,
          mark: <Avatar name={person.name} colour={person.colour} />,
          note: person.role,
        }))

  function pick(suggestion: Suggestion) {
    if (!draft) return
    const next = applyMention(value, draft, suggestion.name)
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
          {matches.map((suggestion, index) => (
            <li key={suggestion.id} role="presentation">
              <button
                type="button"
                role="option"
                aria-selected={index === highlighted}
                className={index === highlighted ? styles.suggestionActive : ''}
                // mousedown, not click: click lands after the field's blur has
                // already closed the list, so the pick never happens.
                onMouseDown={(event) => {
                  event.preventDefault()
                  pick(suggestion)
                }}
              >
                {suggestion.mark}
                <span className={styles.suggestionName}>{suggestion.name}</span>
                <span className={styles.suggestionRole}>{suggestion.note}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}

/**
 * The mark on a file's row: a sheet of paper, or an arrow out for a link.
 *
 * An avatar's size and shape, so the rows of one list line up whether they are
 * offering people or files.
 */
function FileGlyph({ link }: { link: boolean }) {
  return (
    <span className={styles.glyph} aria-hidden="true">
      {link ? (
        '↗'
      ) : (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
          <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
          <path d="M14 3v5h5" />
        </svg>
      )}
    </span>
  )
}
