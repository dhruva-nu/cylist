/**
 * Whose cards the board shows (CYLIST-99): a row of faces at the start of the
 * toolbar, the way Jira does it. Press a face to add that person, press it
 * again to take them off; with nobody pressed it is everybody's board.
 *
 * The board opens on you — see `peopleFromSearch` — because the question most
 * people bring to a board is "what is on me", and a board of everyone's cards
 * makes them find themselves in it every time.
 *
 * The row starts folded: just whoever is picked, small and stacked, and a grey
 * chevron. Who the board is narrowed to is worth seeing at all times; the
 * faces of everybody it could be narrowed to are not. The board stays
 * narrowed whether the row is open or not.
 */

import { useEffect, useId, useMemo, useRef, useState } from 'react'
import type { Person, Task } from '../../api/client'
import { Avatar } from '../../components/primitives/Avatar'
import { cardsOwned, facesInOrder } from './boardPeople'
import { FoldIcon, PeopleIcon, TickIcon } from './icons'
import { usePopover } from './usePopover'
import styles from './BoardToolbar.module.css'

/** How many faces sit on the toolbar before the rest go behind "+N". */
const FACES_SHOWN = 5

/** How many of the picked the folded row draws; its label names them all. */
const FACES_STACKED = 3

export function PeopleFilter({
  members,
  meId,
  tasks,
  people,
  onPeople,
  tally,
}: {
  members: Person[]
  meId: string | null
  /** Every card on the board, unfiltered — what the faces are ordered by. */
  tasks: Task[]
  /** Who is picked, by id. Empty is everyone. */
  people: string[]
  onPeople: (people: string[]) => void
  /** How many cards each person owns on the board as it is narrowed, before
   * this filter has its say — see `narrowBoard`. */
  tally: Map<string, number>
}) {
  const faces = useMemo(
    () => facesInOrder(members, cardsOwned(tasks), meId),
    [members, tasks, meId],
  )
  const [expanded, setExpanded] = useState(false)
  // Opening swaps the summary button for the row and folding swaps it back,
  // so the button pressed is gone; focus goes to the one that took its place.
  const summary = useRef<HTMLButtonElement>(null)
  const fold = useRef<HTMLButtonElement>(null)
  const moveFocus = useRef(false)
  useEffect(() => {
    if (!moveFocus.current) return
    moveFocus.current = false
    ;(expanded ? fold : summary).current?.focus()
  }, [expanded])
  if (!faces.length) return null

  const setOpen = (open: boolean) => {
    moveFocus.current = true
    setExpanded(open)
  }

  if (!expanded) {
    const picked = faces.filter((person) => people.includes(person.id))
    const label = `${showing(picked, people, meId)} Open to change whose.`
    return (
      <button
        ref={summary}
        type="button"
        className={styles.peopleSummary}
        aria-expanded={false}
        aria-label={label}
        title={label}
        onClick={() => setOpen(true)}
      >
        {/* Somebody picked who has since left the project has no face to
            stack, but the board is still narrowed to them: the people icon
            would claim it was everyone's. */}
        {people.length ? (
          <span className={styles.summaryFaces}>
            {picked.slice(0, FACES_STACKED).map((person) => (
              <Avatar key={person.id} name={person.name} colour={person.colour} />
            ))}
          </span>
        ) : (
          <PeopleIcon />
        )}
        <FoldIcon />
      </button>
    )
  }

  const shown = faces.slice(0, FACES_SHOWN)
  const behind = faces.slice(FACES_SHOWN)
  const toggle = (id: string) =>
    onPeople(people.includes(id) ? people.filter((picked) => picked !== id) : [...people, id])

  return (
    <div className={styles.people} role="group" aria-label="Whose cards">
      <button
        ref={fold}
        type="button"
        className={styles.foldButton}
        aria-expanded={true}
        aria-label="Fold the people back up"
        title="Fold the people back up"
        onClick={() => setOpen(false)}
      >
        <FoldIcon back />
      </button>
      <div className={styles.faces}>
        {shown.map((person) => {
          const picked = people.includes(person.id)
          const name = person.id === meId ? `${person.name} (you)` : person.name
          return (
            <button
              key={person.id}
              type="button"
              aria-pressed={picked}
              aria-label={name}
              title={name}
              className={faceClass(picked, people.length > 0)}
              onClick={() => toggle(person.id)}
            >
              <Avatar name={person.name} colour={person.colour} />
            </button>
          )
        })}
        {behind.length ? (
          <MorePeople
            count={behind.length}
            // Ringed while it is holding somebody picked, who would otherwise
            // be narrowing the board from out of sight.
            holdsPick={behind.some((person) => people.includes(person.id))}
            faces={faces}
            meId={meId}
            people={people}
            onPeople={onPeople}
            toggle={toggle}
            tally={tally}
          />
        ) : null}
      </div>
      {people.length ? (
        <button
          type="button"
          className={styles.popLink}
          onClick={() => onPeople([])}
          title="Show everybody's cards"
        >
          Everyone
        </button>
      ) : null}
    </div>
  )
}

/** A face's look: ringed when picked, dimmed when somebody else is. */
function faceClass(picked: boolean, anyPicked: boolean): string {
  return [styles.face, picked ? styles.facePicked : '', anyPicked && !picked ? styles.faceDim : '']
    .filter(Boolean)
    .join(' ')
}

/**
 * "+N": everybody on the project, in a list with a find box, how many cards
 * each owns and a tick for whoever is picked — and the two picks people make
 * most, everyone and just themselves, at the top.
 */
function MorePeople({
  count,
  holdsPick,
  faces,
  meId,
  people,
  onPeople,
  toggle,
  tally,
}: {
  count: number
  holdsPick: boolean
  faces: Person[]
  meId: string | null
  people: string[]
  onPeople: (people: string[]) => void
  toggle: (id: string) => void
  tally: Map<string, number>
}) {
  const { open, setOpen, root, button } = usePopover()
  const [find, setFind] = useState('')
  const listLabel = useId()
  const term = find.trim().toLowerCase()
  const found = term ? faces.filter((person) => person.name.toLowerCase().includes(term)) : faces
  /** You, if you are on this project — "Just me" is about nothing otherwise. */
  const me = faces.find((person) => person.id === meId)?.id

  return (
    <div className={styles.popRoot} ref={root}>
      <button
        ref={button}
        type="button"
        className={faceClass(holdsPick, people.length > 0 && !open)}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-label={`${count} more people`}
        title={open ? 'Hide the list' : 'Everybody on this project'}
        onClick={() => {
          setOpen(!open)
          setFind('')
        }}
      >
        <span className={styles.moreFaces}>+{count}</span>
      </button>
      {open ? (
        <div
          className={`${styles.popover} ${styles.peoplePopover}`}
          role="dialog"
          aria-label="Whose cards"
        >
          <div className={styles.popHead}>
            <span className={styles.popTitle}>Whose cards</span>
            <span>
              <button
                type="button"
                className={styles.popLink}
                aria-pressed={!people.length}
                onClick={() => onPeople([])}
              >
                Everyone
              </button>
              {me !== undefined ? (
                <button
                  type="button"
                  className={styles.popLink}
                  aria-pressed={people.length === 1 && people[0] === me}
                  onClick={() => onPeople([me])}
                >
                  Just me
                </button>
              ) : null}
            </span>
          </div>
          <input
            className={styles.popFind}
            type="search"
            value={find}
            onChange={(event) => setFind(event.target.value)}
            placeholder="Find a person"
            aria-label="Find a person"
            // The list is opened to look somebody up; the box is where that starts.
            autoFocus
          />
          <div className={styles.optionList} role="group" aria-labelledby={listLabel}>
            <span id={listLabel} className="visually-hidden">
              People
            </span>
            {found.map((person) => {
              const picked = people.includes(person.id)
              const owned = tally.get(person.id) ?? 0
              return (
                <button
                  key={person.id}
                  type="button"
                  className={styles.option}
                  aria-pressed={picked}
                  onClick={() => toggle(person.id)}
                >
                  <Avatar name={person.name} colour={person.colour} small />
                  <span className={styles.optionName}>
                    {person.name}
                    {person.id === meId ? <span className={styles.optionHint}> (you)</span> : null}
                  </span>
                  <span
                    className={styles.optionCount}
                    title={`${owned} ${owned === 1 ? 'card' : 'cards'}`}
                  >
                    {owned}
                  </span>
                  <span className={styles.optionTick}>{picked ? <TickIcon /> : null}</span>
                </button>
              )
            })}
            {found.length ? null : <p className={styles.popEmpty}>Nobody called “{find}”.</p>}
          </div>
        </div>
      ) : null}
    </div>
  )
}

/**
 * "Nothing on Aditi on this board", over the columns: what an empty board
 * says when the people picked own no card on it at all — so it reads as
 * nothing being theirs rather than the board having failed to load.
 *
 * Not drawn when the board is empty for another reason, such as the search
 * box, which says what it is narrowing to in its own words.
 */
export function NobodysCards({
  people,
  tasks,
  members,
  meId,
  onEveryone,
}: {
  people: string[]
  tasks: Task[]
  members: Person[]
  meId: string | null
  onEveryone: () => void
}) {
  if (!people.length) return null
  const owned = cardsOwned(tasks)
  if (people.some((id) => owned.has(id))) return null

  const known = new Map(members.map((person) => [person.id, person.name]))
  const names = people.map((id) =>
    id === meId ? 'you' : (known.get(id) ?? 'somebody no longer on this project'),
  )
  return (
    <p className={styles.nobodys} role="status">
      Nothing on {joinOr(names)} on this board.{' '}
      <button type="button" className={styles.popLink} onClick={onEveryone}>
        Show everyone
      </button>
    </p>
  )
}

/** "Aditi", "Aditi or Ravi", "Aditi, Ravi or you". */
function joinOr(names: string[]): string {
  return joinWith(names, 'or')
}

/** What the folded row is narrowing the board to, in words, for its label. */
function showing(picked: Person[], people: string[], meId: string | null): string {
  if (!people.length) return "Showing everyone's cards."
  if (!picked.length) return 'Showing the cards of somebody no longer on this project.'
  if (picked.length === 1 && picked[0]?.id === meId) return 'Showing your cards.'
  return `Showing ${joinWith(
    picked.map((person) => (person.id === meId ? 'you' : person.name)),
    'and',
  )}'s cards.`
}

function joinWith(names: string[], last: string): string {
  return names.length < 2
    ? (names[0] ?? '')
    : `${names.slice(0, -1).join(', ')} ${last} ${names.at(-1)}`
}
