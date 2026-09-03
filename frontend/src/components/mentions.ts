/**
 * Tagging a person with `@`, in a comment, a description or a stage label.
 *
 * A tag is the text somebody typed — `@Aditi K` — and it is recognised when
 * the text is read, against the people on the project. Nothing is written
 * into the text that a reader cannot see: the description you get back over
 * the API, in the CLI or through an agent is the description as it was
 * written, and `@Aditi K` says who is meant in every one of them. It is also
 * already the board's notation for the people a blocked card waits on, so a
 * tag looks like what it is before any of this code runs.
 *
 * The cost is that a tag follows the name rather than the person: rename
 * somebody and their old tags go back to reading as plain text. That is the
 * right way round here, because a name is already how a person is addressed
 * everywhere else in this product — the MCP tools resolve people by name, and
 * so does the board's `who:` search.
 *
 * Kept out of the components for the same reason as `boardSearch.ts`: none of
 * it touches React, and string matching is easiest to get right on its own.
 */

import type { Person } from '../api/client'

/**
 * What `@` may not be glued to the back of, and what a name may not be glued
 * to the front of.
 *
 * The point is that `deploy@example.com` is an address and not a tag on
 * somebody called Example: an `@` with a word character before it was not
 * typed to name anybody.
 */
const WORD = /[\p{L}\p{N}_]/u

/** How far back {@link mentionQuery} looks for the `@` it is completing. */
const LONGEST_QUERY = 80

/** A stretch of text, either plain or a person somebody tagged. */
export type MentionRun =
  { kind: 'text'; text: string } | { kind: 'mention'; text: string; person: Person }

/** An `@…` under the caret, waiting for a name to be chosen for it. */
export interface MentionDraft {
  /** Where the `@` is. */
  at: number
  /** What has been typed after it, up to the caret. */
  query: string
}

function isWord(char: string | undefined): boolean {
  return char !== undefined && WORD.test(char)
}

/**
 * Split text into its plain runs and its tags.
 *
 * Longest name first, so a directory holding both "Aditi" and "Aditi K" reads
 * `@Aditi K` as the second of them rather than as the first with a stray "K"
 * after it. A name only counts when the text neither runs into it nor out of
 * it: `@Aditi Kumar` is nobody, not "Aditi K" and "umar".
 */
export function splitMentions(text: string, members: readonly Person[]): MentionRun[] {
  const candidates = [...members].sort((a, b) => b.name.length - a.name.length)
  const runs: MentionRun[] = []
  let plain = ''

  for (let index = 0; index < text.length; index += 1) {
    const person =
      text[index] === '@' && !isWord(text[index - 1]) ? found(text, index + 1, candidates) : null

    if (!person) {
      plain += text[index]
      continue
    }

    if (plain) runs.push({ kind: 'text', text: plain })
    plain = ''
    runs.push({ kind: 'mention', text: `@${person.name}`, person })
    index += person.name.length
  }

  if (plain) runs.push({ kind: 'text', text: plain })
  return runs
}

/** The first candidate whose whole name sits at `from`, if any. */
function found(text: string, from: number, candidates: readonly Person[]): Person | null {
  for (const person of candidates) {
    const end = from + person.name.length
    if (text.slice(from, end).toLowerCase() !== person.name.toLowerCase()) continue
    if (isWord(text[end])) continue
    return person
  }
  return null
}

/**
 * The `@…` the caret is inside, if it is inside one.
 *
 * The query runs to the caret and may hold spaces, because names do: nothing
 * would ever complete "Aditi K" if a space ended the search. What stops it
 * running away is that the caller shows nobody once the query matches nobody,
 * which a space that no name has does immediately.
 */
export function mentionQuery(value: string, caret: number): MentionDraft | null {
  const floor = Math.max(0, caret - LONGEST_QUERY)

  for (let index = caret - 1; index >= floor; index -= 1) {
    const char = value[index]
    // A tag is one line. Past a newline is a different sentence, not a name
    // still being typed.
    if (char === '\n') return null
    if (char !== '@') continue
    if (isWord(value[index - 1])) return null
    return { at: index, query: value.slice(index + 1, caret) }
  }

  return null
}

/**
 * Who the query could still be naming: everyone whose name starts with it.
 *
 * An empty query — the caret just after a bare `@` — offers the lot, which is
 * what makes `@` on its own a way to see who is on the project.
 */
export function matchingMembers(query: string, members: readonly Person[]): Person[] {
  const wanted = query.toLowerCase()
  return members.filter((person) => person.name.toLowerCase().startsWith(wanted))
}

/**
 * Put a chosen name into the text in place of what was typed towards it.
 *
 * A trailing space, so the sentence carries on rather than running into the
 * name — and so a second `@` right afterwards is a second tag. Not a second
 * one, though: completing a tag in the middle of a sentence already has a
 * space after it, and two would be a gap the writer has to go back and close.
 */
export function applyMention(
  value: string,
  draft: MentionDraft,
  name: string,
): { value: string; caret: number } {
  const tail = value.slice(draft.at + 1 + draft.query.length)
  const head = `${value.slice(0, draft.at)}@${name}${tail.startsWith(' ') ? '' : ' '}`
  return { value: head + tail, caret: head.length }
}
