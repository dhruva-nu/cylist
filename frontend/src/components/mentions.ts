/**
 * Tagging in prose: a person with `@`, a file with `>`.
 *
 * A tag is the text somebody typed — `@Aditi K`, `>brief.pdf` — and it is
 * recognised when the text is read, against the people on the project and the
 * files in it. Nothing is written into the text that a reader cannot see: the
 * description you get back over the API, in the CLI or through an agent is the
 * description as it was written, and `@Aditi K` says who is meant in every one
 * of them, `>brief.pdf` what.
 *
 * The cost is that a tag follows the name rather than the thing: rename
 * somebody, or rename a file, and their old tags go back to reading as plain
 * text. That is the right way round here, because a name is already how both
 * are addressed everywhere else in this product — the MCP tools resolve people
 * and paths by name, and so does the board's `who:` search.
 *
 * Two sigils, one matcher, because a comment holds both and reading it back is
 * one pass over the text. What differs between them is only what a name is
 * likely to be remembered by — see {@link matchingFiles}.
 *
 * Kept out of the components for the same reason as `boardSearch.ts`: none of
 * it touches React, and string matching is easiest to get right on its own.
 */

import type { FiledItem, Person } from '../api/client'

/**
 * What a name may not be glued to the front or the back of.
 *
 * The point is that `deploy@example.com` is an address and not a tag on
 * somebody called Example: an `@` with a word character before it was not
 * typed to name anybody.
 */
const WORD = /[\p{L}\p{N}_]/u

/**
 * What a `>` may follow and still be a tag: nothing, a space, or something a
 * phrase is opened with.
 *
 * A whitelist where `@` gets away with a blacklist, because `>` is a character
 * people already write for other reasons — `a->b`, `() =>`, `x >= 3`, a quoted
 * line — and every one of those has a character before the `>` that no writer
 * tagging a file would have typed. Guessing wrong here turns a line of code in
 * a description into a link.
 */
const OPENS_A_FILE_TAG = /[\s([{"'`]/u

/** How far back {@link mentionQuery} looks for the sigil it is completing. */
const LONGEST_QUERY = 80

/** How many files one `>` offers at once — see {@link matchingFiles}. */
const MOST_FILES_OFFERED = 12

/** What starts a tag, and what kind of tag it starts. */
export const PERSON_SIGIL = '@'
export const FILE_SIGIL = '>'

export type Sigil = typeof PERSON_SIGIL | typeof FILE_SIGIL

/** A stretch of text: plain, a person tagged, or a file tagged. */
export type MentionRun =
  | { kind: 'text'; text: string }
  | { kind: 'mention'; text: string; person: Person }
  | { kind: 'file'; text: string; file: FiledItem }

/** A sigil under the caret, waiting for a name to be chosen for it. */
export interface MentionDraft {
  /** Which sigil was typed, and so what the list should offer. */
  sigil: Sigil
  /** Where the sigil is. */
  at: number
  /** What has been typed after it, up to the caret. */
  query: string
}

function isWord(char: string | undefined): boolean {
  return char !== undefined && WORD.test(char)
}

/** Whether a sigil at this point in the text could be starting a tag at all. */
function mayStart(sigil: Sigil, before: string | undefined): boolean {
  if (sigil === FILE_SIGIL) return before === undefined || OPENS_A_FILE_TAG.test(before)
  return !isWord(before)
}

/**
 * Split text into its plain runs and its tags.
 *
 * Longest name first, so a directory holding both "Aditi" and "Aditi K" reads
 * `@Aditi K` as the second of them rather than as the first with a stray "K"
 * after it, and a project holding both `brief.pdf` and `brief.pdf.sig` reads
 * each as itself. A name only counts when the text neither runs into it nor
 * out of it: `@Aditi Kumar` is nobody, not "Aditi K" and "umar".
 *
 * Two files may share a name in different folders, and a tag naming one of
 * them names the first — there is nothing in the text to tell them apart, and
 * refusing to recognise the tag at all would punish the writer for a
 * coincidence they cannot see. The picker shows each file's folder beside it,
 * which is where that is worth knowing.
 */
export function splitMentions(
  text: string,
  members: readonly Person[],
  files: readonly FiledItem[] = [],
): MentionRun[] {
  // Most prose holds no tag at all, and this is called for every comment on
  // a card on every keystroke in the composer below them. A sigil somewhere
  // in the text is what the rest of this is worth doing for.
  if (!text.includes(PERSON_SIGIL) && !text.includes(FILE_SIGIL)) {
    return text ? [{ kind: 'text', text }] : []
  }

  const people = byLongestName(members)
  const documents = byLongestName(files)
  const runs: MentionRun[] = []
  let plain = ''

  for (let index = 0; index < text.length; index += 1) {
    const tag = tagAt(text, index, people, documents)

    if (!tag) {
      plain += text[index]
      continue
    }

    if (plain) runs.push({ kind: 'text', text: plain })
    plain = ''
    runs.push(tag)
    index += tag.text.length - 1
  }

  if (plain) runs.push({ kind: 'text', text: plain })
  return runs
}

/** Candidates with the longest name first, so the fullest match wins. */
function byLongestName<T extends { name: string }>(named: readonly T[]): readonly T[] {
  return [...named].sort((a, b) => b.name.length - a.name.length)
}

/** The tag starting at `index`, if a whole name sits behind a sigil there. */
function tagAt(
  text: string,
  index: number,
  people: readonly Person[],
  files: readonly FiledItem[],
): MentionRun | null {
  const sigil = text[index]
  if (sigil !== PERSON_SIGIL && sigil !== FILE_SIGIL) return null
  if (!mayStart(sigil, text[index - 1])) return null

  if (sigil === PERSON_SIGIL) {
    const person = found(text, index + 1, people)
    return person && { kind: 'mention', text: `${sigil}${person.name}`, person }
  }

  const file = found(text, index + 1, files)
  return file && { kind: 'file', text: `${sigil}${file.name}`, file }
}

/** The first candidate whose whole name sits at `from`, if any. */
function found<T extends { name: string }>(
  text: string,
  from: number,
  candidates: readonly T[],
): T | null {
  for (const candidate of candidates) {
    const end = from + candidate.name.length
    if (text.slice(from, end).toLowerCase() !== candidate.name.toLowerCase()) continue
    if (isWord(text[end])) continue
    return candidate
  }
  return null
}

/**
 * The tag the caret is inside, if it is inside one.
 *
 * The query runs to the caret and may hold spaces, because names do: nothing
 * would ever complete "Aditi K" if a space ended the search. What stops it
 * running away is that the caller shows nobody once the query matches nobody,
 * which a space that no name has does immediately.
 *
 * Whichever sigil is nearest the caret is the one being completed, so a `>`
 * typed inside a sentence that already tagged somebody offers files. A sigil
 * that could not be starting a tag where it sits — an `@` in an address, a `>`
 * in `->` — stops the search rather than passing it back to whatever came
 * before, because the caret is inside that word, not inside a tag.
 */
export function mentionQuery(value: string, caret: number): MentionDraft | null {
  const floor = Math.max(0, caret - LONGEST_QUERY)

  for (let index = caret - 1; index >= floor; index -= 1) {
    const char = value[index]
    // A tag is one line. Past a newline is a different sentence, not a name
    // still being typed.
    if (char === '\n') return null
    if (char !== PERSON_SIGIL && char !== FILE_SIGIL) continue
    if (!mayStart(char, value[index - 1])) return null
    return { sigil: char, at: index, query: value.slice(index + 1, caret) }
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
 * Which files the query could still be naming.
 *
 * Anywhere in the name, not only the start, which is where this parts company
 * with {@link matchingMembers}: a person is remembered from the beginning of
 * their name, but a file is called `2026-01-atlas-brief.pdf` and remembered as
 * "brief". Names that *start* with the query come first all the same, because
 * somebody who typed the beginning of a name meant that name.
 *
 * Capped, because a project's files are unbounded where its people are not,
 * and a list longer than the box it drops out of is a list nobody reads to the
 * end of. Typing more narrows it; the files screen is for browsing.
 */
export function matchingFiles(query: string, files: readonly FiledItem[]): FiledItem[] {
  const wanted = query.toLowerCase()
  const matches = files.filter((file) => file.name.toLowerCase().includes(wanted))
  const starts = matches.filter((file) => file.name.toLowerCase().startsWith(wanted))
  const rest = matches.filter((file) => !file.name.toLowerCase().startsWith(wanted))
  return [...starts, ...rest].slice(0, MOST_FILES_OFFERED)
}

/**
 * Put a chosen name into the text in place of what was typed towards it.
 *
 * A trailing space, so the sentence carries on rather than running into the
 * name — and so a second sigil right afterwards is a second tag. Not a second
 * space, though: completing a tag in the middle of a sentence already has one
 * after it, and two would be a gap the writer has to go back and close.
 */
export function applyMention(
  value: string,
  draft: MentionDraft,
  name: string,
): { value: string; caret: number } {
  const tail = value.slice(draft.at + 1 + draft.query.length)
  const head = `${value.slice(0, draft.at)}${draft.sigil}${name}${tail.startsWith(' ') ? '' : ' '}`
  return { value: head + tail, caret: head.length }
}
