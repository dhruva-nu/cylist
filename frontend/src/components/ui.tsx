/** Shared presentational primitives, styled from the design tokens. */

import { useCallback, useState, type ButtonHTMLAttributes, type ReactNode } from 'react'
import type { PersonKind } from '../api/client'
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

/** The pale and deep ends of the sub-status gradient — light green to a deep,
 * confident green, standing in for "just started" to "nearly there". */
const SUB_STATUS_LIGHT = { r: 0xcf, g: 0xe9, b: 0xd8 }
const SUB_STATUS_DEEP = { r: 0x1d, g: 0x5c, b: 0x38 }

/**
 * A sub-status stage's fill colour, as a hex string `readableInkOn` can read.
 *
 * Interpolated across however many stages the task actually has, so the same
 * index means a different shade depending on the count: stage 1 of 2 sits
 * halfway down the gradient, stage 1 of 4 barely off the pale end. A single
 * stage is drawn at the deep end — nothing to gradient across, and the one
 * stage there is always "current".
 */
export function subStatusColor(index: number, count: number): string {
  const t = count <= 1 ? 1 : index / (count - 1)
  const mix = (from: number, to: number) => Math.round(from + (to - from) * t)
  const channel = (value: number) => value.toString(16).padStart(2, '0')
  return `#${channel(mix(SUB_STATUS_LIGHT.r, SUB_STATUS_DEEP.r))}${channel(
    mix(SUB_STATUS_LIGHT.g, SUB_STATUS_DEEP.g),
  )}${channel(mix(SUB_STATUS_LIGHT.b, SUB_STATUS_DEEP.b))}`
}

/**
 * A task's sub-status stages as a row of chips: stages up to and including
 * the current one filled in the green gradient, later ones left muted.
 *
 * Read-only — the board card wraps this in a button of its own so a click
 * advances the task; nothing here reacts to one.
 */
export function SubStatusChips({ labels, index }: { labels: string[]; index: number }) {
  return (
    <span className={styles.subStatusChips}>
      {labels.map((label, position) => {
        const filled = position <= index
        const colour = filled ? subStatusColor(position, labels.length) : undefined
        return (
          <span
            key={position}
            className={`${styles.subStatusChip} ${filled ? styles.subStatusChipFilled : ''}`}
            style={colour ? { background: colour, color: readableInkOn(colour) } : undefined}
          >
            {label}
          </span>
        )
      })}
    </span>
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
