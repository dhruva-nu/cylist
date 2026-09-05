/** Shared presentational primitives, styled from the design tokens. */

import { useCallback, useState, type ButtonHTMLAttributes, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import type { Person, PersonKind, TaskPriority, TaskType } from '../api/client'
import { splitMentions } from './mentions'
import styles from './ui.module.css'

type ButtonVariant = 'plain' | 'go' | 'ghost'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  small?: boolean
  danger?: boolean
}

export function Button({
  variant = 'plain',
  small = false,
  danger = false,
  className,
  type = 'button',
  ...rest
}: ButtonProps) {
  const classes = [
    styles.button,
    variant === 'go' && styles.go,
    variant === 'ghost' && styles.ghost,
    small && styles.small,
    danger && styles.danger,
    className,
  ]
    .filter(Boolean)
    .join(' ')

  return <button type={type} className={classes} {...rest} />
}

/** Initials from a name — "Aditi K" becomes AK. */
export function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => word[0]?.toUpperCase() ?? '')
    .join('')
}

/**
 * Which of the two fixed inks to write on a background, by its brightness.
 *
 * A person's or project's colour is stored per row and can be any hex at all,
 * so assuming white initials will do is how you get a 3:1 avatar. Anything
 * this cannot read — a keyword, a var() — falls back to the light ink, which
 * is what the identity palette is chosen to carry.
 */
export function readableInkOn(colour: string): string {
  const digits = /^#([\da-f]{3}|[\da-f]{6})$/i.exec(colour.trim())?.[1]
  if (digits === undefined) return 'var(--on-accent)'

  const full = digits.length === 3 ? digits.replace(/./g, (pair) => pair + pair) : digits
  const [r = 0, g = 0, b = 0] = [0, 2, 4].map((offset) => {
    const channel = parseInt(full.slice(offset, offset + 2), 16) / 255
    return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
  })
  const luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b

  // 0.19 is where white stops clearing 4.5:1; below it the light ink wins.
  return luminance > 0.19 ? 'var(--on-accent-dark)' : 'var(--on-accent)'
}

/** Where a tip is pointing, and what it has to say there. */
interface TipState {
  text: string
  note: string
  /** Viewport coordinates of the middle of the thing being described. */
  x: number
  top: number
  bottom: number
}

/**
 * A hover label for a control too small to carry its own text.
 *
 * `show` is handed the pointer event so it can measure the element under the
 * cursor; the caller supplies the words, because the element itself often has
 * none — that is the reason for the tip.
 */
function useTip() {
  const [tip, setTip] = useState<TipState | null>(null)

  const show = (event: { currentTarget: Element }, text: string, note: string) => {
    const box = event.currentTarget.getBoundingClientRect()
    setTip({ text, note, x: box.left + box.width / 2, top: box.top, bottom: box.bottom })
  }

  return { tip, show, hide: () => setTip(null) }
}

/**
 * The tip itself, drawn into `document.body`.
 *
 * A portal rather than a child of what it describes: board columns scroll and
 * clip, so a tip positioned inside one is cut off by the very overflow that
 * makes the board usable. Fixed coordinates put it back over the top of
 * everything, and it is nudged below its anchor when there is no room above.
 */
function Tip({ tip }: { tip: TipState | null }) {
  if (tip === null) return null

  const below = tip.top < 90
  const margin = 140

  return createPortal(
    <span
      role="tooltip"
      className={`${styles.tip} ${below ? styles.tipBelow : ''}`}
      style={{
        // Clamped to the viewport: half a full-width tip either side of the
        // anchor is what centring needs, and the segments at the ends of a
        // card near the edge of the board do not have it.
        left: Math.min(Math.max(tip.x, margin), Math.max(window.innerWidth - margin, margin)),
        top: below ? tip.bottom : tip.top,
      }}
    >
      <span className={styles.tipText}>{tip.text}</span>
      <span className={styles.tipNote}>{tip.note}</span>
    </span>,
    document.body,
  )
}

/**
 * The stage labels a task's sub-status is made of, and where it has got to.
 *
 * Nothing here is only a colour: the bar says position, the head line names
 * the stage you are on, and the hover tip carries whichever label you point
 * at in full. Labels can be a sentence long, so no part of the design asks
 * one to fit inside a fixed width.
 */
/**
 * Text with its `@` tags drawn as tags — see `mentions.ts` for what counts as
 * one. Used wherever somebody's prose is read back: a description, a comment,
 * the label on a stage.
 *
 * Whitespace is the author's. A description is written in paragraphs, and
 * reading it back as one run-on line would lose what they wrote.
 */
export function Tagged({ text, members }: { text: string; members: readonly Person[] }) {
  return (
    <span className={styles.tagged}>
      {splitMentions(text, members).map((run, index) =>
        run.kind === 'mention' ? (
          <span
            key={index}
            className={styles.mention}
            title={`${run.person.name} — ${run.person.role}`}
          >
            {run.text}
          </span>
        ) : (
          <span key={index}>{run.text}</span>
        ),
      )}
    </span>
  )
}

interface SubStatusProps {
  labels: string[]
  index: number
  /**
   * Called with the stage clicked or arrowed to. Omitted where the bar is
   * only reporting — the read-only detail view — which also drops the slider
   * role and the pointer cursor rather than offering a control that does
   * nothing.
   */
  onMove?: (index: number) => void
  /**
   * Let the current stage's label wrap onto as many lines as it needs. For
   * the dialog, which has the width for it; a board card clips to one line
   * instead, and leaves the rest to the tip.
   */
  wrap?: boolean
  /**
   * The project's people, for drawing an `@` tag in a stage label. Omitted
   * where they are not to hand, which only costs the label its highlight —
   * the words are the words either way.
   */
  members?: readonly Person[]
}

/**
 * A task's sub-status as a segmented slider: one segment per stage, filled up
 * to the stage it is on, with that stage drawn as the thumb.
 *
 * Interactive, the whole track is one slider — click a segment or arrow along
 * it to move the task to that stage, backwards as readily as forwards. The
 * segments themselves are not focus stops; the track is, and carries the
 * value, so tabbing through a board card is one stop rather than four.
 */
export function SubStatusBar({ labels, index, onMove, wrap = false, members }: SubStatusProps) {
  const tip = useTip()
  /**
   * The stage under the cursor, when it is ahead of the current one. Fills the
   * segments in between at half strength, so the bar shows where a click would
   * take the card before it takes it.
   */
  const [preview, setPreview] = useState<number | null>(null)
  const live = onMove !== undefined
  const current = labels[index] ?? ''
  /** There is no stage past the last one to be unfinished about, so reaching
   * it is done rather than merely arrived — see the thumb's own colour. */
  const done = index === labels.length - 1

  /** The tip for a stage: its label, and what pointing at it is offering. */
  const noteFor = (position: number) =>
    position === index
      ? `Stage ${position + 1} of ${labels.length} · ${done ? 'done' : 'you are here'}`
      : `Stage ${position + 1} of ${labels.length}${live ? ' · click to move here' : ''}`

  return (
    <div className={styles.subStatus}>
      <div className={styles.subStatusHead}>
        <span
          className={`${styles.subStatusNow} ${wrap ? styles.subStatusNowWrapped : ''}`}
          onPointerEnter={(event) => tip.show(event, current, noteFor(index))}
          onPointerLeave={tip.hide}
        >
          {members ? <Tagged text={current} members={members} /> : current}
        </span>
        <span className={styles.subStatusCount}>
          {index + 1}/{labels.length}
        </span>
      </div>

      <div
        className={`${styles.subStatusTrack} ${live ? styles.subStatusTrackLive : ''}`}
        // Read-only, the track is a picture of the list below it and has
        // nothing of its own to say. Live, it is the slider itself.
        role={live ? 'slider' : undefined}
        aria-hidden={live ? undefined : true}
        tabIndex={live ? 0 : undefined}
        aria-label={live ? 'Sub-status' : undefined}
        aria-valuemin={live ? 1 : undefined}
        aria-valuemax={live ? labels.length : undefined}
        aria-valuenow={live ? index + 1 : undefined}
        aria-valuetext={live ? `Stage ${index + 1} of ${labels.length}: ${current}` : undefined}
        onPointerLeave={() => {
          setPreview(null)
          tip.hide()
        }}
        onFocus={(event) => tip.show(event, current, noteFor(index))}
        onBlur={tip.hide}
        onKeyDown={(event) => {
          if (!onMove) return
          // Every key stops here, not just the ones that move the slider: the
          // board card around this is dnd-kit's drag handle and the button
          // that opens the dialog, so an Enter left to bubble would open a
          // task the reader was only stepping through.
          event.stopPropagation()
          const to = {
            ArrowLeft: index - 1,
            ArrowDown: index - 1,
            ArrowRight: index + 1,
            ArrowUp: index + 1,
            Home: 0,
            End: labels.length - 1,
          }[event.key]
          if (to === undefined) return
          event.preventDefault()
          if (to !== index && to >= 0 && to < labels.length) onMove(to)
        }}
      >
        {labels.map((label, position) => (
          <span
            key={position}
            className={[
              styles.subStatusSegment,
              (position < index || (position === index && done)) && styles.subStatusDone,
              position === index && styles.subStatusHere,
              preview !== null && position > index && position <= preview && styles.subStatusAhead,
            ]
              .filter(Boolean)
              .join(' ')}
            onPointerEnter={(event) => {
              setPreview(position)
              tip.show(event, label, noteFor(position))
            }}
            onClick={(event) => {
              if (!onMove) return
              // The card is both the drag handle and the button that opens the
              // dialog, so a click left to bubble would do one of those
              // instead of moving the stage.
              event.stopPropagation()
              if (position !== index) onMove(position)
            }}
            onPointerDown={(event) => event.stopPropagation()}
          />
        ))}
      </div>

      {/* The bar is a picture of the list; this is the list. Screen readers
          get every stage and which one is current, in order, without the
          segments having to be four more things to tab past. */}
      <ol className="visually-hidden">
        {labels.map((label, position) => (
          <li key={position}>
            {position === index ? `${label} — ${done ? 'done' : 'current stage'}` : label}
          </li>
        ))}
      </ol>

      <Tip tip={tip.tip} />
    </div>
  )
}

export function Avatar({
  name,
  colour,
  large = false,
}: {
  name: string
  colour: string
  large?: boolean
}) {
  return (
    <span
      className={`${styles.avatar} ${large ? styles.avatarLarge : ''}`}
      style={{ background: colour, color: readableInkOn(colour) }}
      title={name}
      aria-hidden="true"
    >
      {initials(name)}
    </span>
  )
}

/**
 * A task's type and priority, drawn rather than spelled.
 *
 * Both are closed sets of four or fewer values that appear on every card in a
 * column, where the words are the widest thing on the row and the least worth
 * reading twice. Inline paths rather than an icon package: eight glyphs do not
 * earn a dependency, and `currentColor` keeps each one the colour the chip
 * around it already carries.
 *
 * Every icon is decoration, `aria-hidden` and titleless: the word it replaces
 * is never dropped, only moved onto the chip around it, which carries it as a
 * tooltip and as text a screen reader still reads out.
 */
function Glyph({ children }: { children: ReactNode }) {
  return (
    <svg
      className={styles.glyph}
      viewBox="0 0 16 16"
      width="14"
      height="14"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  )
}

export function TypeIcon({ type }: { type: TaskType }) {
  if (type === 'bug') {
    return (
      <Glyph>
        {/* A shell with legs: the body reads at 14px even when the legs do not. */}
        <path d="M5 6.5a3 3 0 0 1 6 0v3a3 3 0 0 1-6 0Z" />
        <path d="M6 4.2 7 5.4M10 4.2 9 5.4M5 7.2H2.6M11 7.2h2.4M5 10.4H3M11 10.4h2" />
      </Glyph>
    )
  }
  if (type === 'chore') {
    return (
      <Glyph>
        {/* A spanner: work that has to happen, not work anyone asked for. */}
        <path d="M10.6 2.4a3.4 3.4 0 0 0-3.3 5.7L3 12.4l1.4 1.4 4.3-4.3a3.4 3.4 0 0 0 4.6-4.3l-2 2-1.7-1.7Z" />
      </Glyph>
    )
  }
  return (
    <Glyph>
      {/* A spark: the one of the three that is new work. */}
      <path d="M8 2.2 9.4 6.6 13.8 8 9.4 9.4 8 13.8 6.6 9.4 2.2 8l4.4-1.4Z" />
    </Glyph>
  )
}

export function PriorityIcon({ priority }: { priority: TaskPriority }) {
  // One shape rotated through four positions, so the four values read as one
  // scale: two chevrons up, one up, level, one down.
  if (priority === 'urgent') {
    return (
      <Glyph>
        <path d="M3.5 8.5 8 4l4.5 4.5M3.5 12 8 7.5l4.5 4.5" />
      </Glyph>
    )
  }
  if (priority === 'asap') {
    return (
      <Glyph>
        <path d="M3.5 10.2 8 5.8l4.5 4.4" />
      </Glyph>
    )
  }
  if (priority === 'week') {
    return (
      <Glyph>
        <path d="M3.5 6.4h9M3.5 9.6h9" />
      </Glyph>
    )
  }
  return (
    <Glyph>
      <path d="M3.5 5.8 8 10.2l4.5-4.4" />
    </Glyph>
  )
}

export function KindTag({ kind }: { kind: PersonKind }) {
  return <span className={`${styles.tag} ${styles[kind]}`}>{kind}</span>
}

export function Eyebrow({ children }: { children: ReactNode }) {
  return <span className={styles.eyebrow}>{children}</span>
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <div className={styles.empty}>{children}</div>
}

export function ErrorBanner({ children }: { children: ReactNode }) {
  return (
    <p className={styles.banner} role="alert">
      {children}
    </p>
  )
}

/**
 * Say something to a screen reader that the screen only shows.
 *
 * A card moving column, a secret revealed, an upload finishing: each is
 * obvious to anyone watching and silent to anyone not. Pair the returned
 * `message` with a :func:`LiveRegion`.
 */
export function useAnnouncer() {
  const [message, setMessage] = useState('')

  const announce = useCallback((text: string) => {
    // A live region whose text has not changed is not read again, so doing the
    // same thing twice would announce once. The trailing space makes the
    // string new without changing a word of what is spoken.
    setMessage((current) => (current === text ? `${text}\u00a0` : text))
  }, [])

  return { message, announce }
}

/** Where :func:`useAnnouncer`'s messages are spoken. Renders nothing visible. */
export function LiveRegion({ message }: { message: string }) {
  return (
    <p className="visually-hidden" role="status" aria-live="polite">
      {message}
    </p>
  )
}

/**
 * A task's Jira key or pull request, as short as it can be said.
 *
 * Both fields are free text on the way in, and people fill them either way:
 * `ATL-41` and `#212` typed by hand on one card, the whole URL pasted off the
 * browser bar on the next. The URL is the more useful of the two — it is the
 * only form that can be followed — and the less readable, being long enough to
 * push everything else off the row it sits on. So a URL shows as the identifier
 * it contains and carries the URL on the link; anything else shows as typed,
 * with nothing to click.
 *
 * `stopPropagation` on the pointer and key events is what makes this safe to
 * drop on a board card. That card is both the drag handle and the button that
 * opens the task, so an event left to bubble would open the dialog behind the
 * new tab, or pick the card up instead of following the link.
 */
export function TaskRef({ kind, value }: { kind: 'jira' | 'pr'; value: string }) {
  const { label, href } = kind === 'jira' ? jiraRef(value) : prRef(value)
  const icon = kind === 'jira' ? '\u2337' : '\u2387'

  if (!href) {
    return (
      <span>
        {icon} {label}
      </span>
    )
  }

  return (
    <a
      className={styles.taskRef}
      href={href}
      target="_blank"
      rel="noreferrer"
      // The icon is decoration and the label is an abbreviation, so neither
      // says on its own what the link goes to.
      aria-label={`${kind === 'jira' ? 'Jira' : 'Pull request'} ${label}`}
      title={href}
      onClick={(event) => event.stopPropagation()}
      onPointerDown={(event) => event.stopPropagation()}
      onKeyDown={(event) => event.stopPropagation()}
    >
      {icon} {label}
    </a>
  )
}

/** What :func:`TaskRef` renders: the text to show, and where it points. */
interface Ref {
  label: string
  /** Null when the stored value is not a URL — there is nothing to follow. */
  href: string | null
}

/**
 * A stored `https://…` value as a URL, or null.
 *
 * The scheme test is not only about `new URL` accepting the string: it is what
 * keeps a `javascript:` value someone typed into the Jira box out of an href.
 */
function asUrl(value: string): URL | null {
  if (!/^https?:\/\//i.test(value)) return null
  try {
    return new URL(value)
  } catch {
    return null
  }
}

/** The last path segment: the shortest thing left to show when nothing parses. */
function lastSegment(url: URL): string {
  return url.pathname.split('/').filter(Boolean).pop() ?? url.hostname
}

function jiraRef(value: string): Ref {
  const url = asUrl(value)
  if (!url) return { label: value, href: null }
  // The query string is searched as well as the path: `/browse/ATL-41` is the
  // link people copy, but a board hands out `?selectedIssue=ATL-41`.
  const key = /[A-Z][A-Z0-9]*-\d+/i.exec(`${url.pathname} ${url.search}`)
  return { label: key ? key[0].toUpperCase() : lastSegment(url), href: value }
}

function prRef(value: string): Ref {
  const url = asUrl(value)
  if (!url) return { label: value, href: null }
  // GitHub and Bitbucket say `pull`/`pull-requests`, GitLab `merge_requests`.
  const number = /\/(?:pull|pull-requests|merge_requests)\/(\d+)/i.exec(url.pathname)
  return { label: number ? `#${number[1]}` : lastSegment(url), href: value }
}

export const cardStyles = styles
