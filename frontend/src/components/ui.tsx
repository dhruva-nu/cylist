/** Shared presentational primitives, styled from the design tokens. */

import { useCallback, useState, type ButtonHTMLAttributes, type ReactNode } from 'react'
import type { PersonKind, TaskPriority, TaskType } from '../api/client'
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
