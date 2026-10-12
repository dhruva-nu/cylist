import type { ReactNode } from 'react'
import type { PrState, TaskPriority, TaskStatus, TaskType } from '../../api/client'
import styles from './ui.module.css'

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
function Glyph({
  children,
  size = 14,
  weight = 1.6,
  filled = false,
}: {
  children: ReactNode
  /** Edge of the square box. 13 in a due chip, 14 on a card, 15 in a type tile. */
  size?: number
  /**
   * Stroke width. 1.6 is the drawing weight of the set; the priority chevrons
   * take 1.8 because they are two or three hairlines rather than a shape, and
   * at 1.6 a stack of them turned into a grey smudge at card size.
   */
  weight?: number
  /**
   * Painted solid rather than drawn in outline. Only the feature spark: it is
   * the one glyph in the set that is a shape rather than a diagram, and hollow
   * it read as a fifth kind of outline instead of as the thing that is new.
   */
  filled?: boolean
}) {
  return (
    <svg
      className={styles.glyph}
      viewBox="0 0 16 16"
      width={size}
      height={size}
      fill={filled ? 'currentColor' : 'none'}
      stroke={filled ? undefined : 'currentColor'}
      strokeWidth={filled ? undefined : weight}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  )
}

export function TypeIcon({ type, size = 14 }: { type: TaskType; size?: number }) {
  if (type === 'bug') {
    return (
      <Glyph size={size}>
        {/* A shell with legs: the body reads at 14px even when the legs do not. */}
        <path d="M5 6.5a3 3 0 0 1 6 0v3a3 3 0 0 1-6 0Z" />
        <path d="M6 4.2 7 5.4M10 4.2 9 5.4M5 7.2H2.6M11 7.2h2.4M5 10.4H3M11 10.4h2" />
      </Glyph>
    )
  }
  if (type === 'chore') {
    return (
      <Glyph size={size}>
        {/* A spanner: work that has to happen, not work anyone asked for. */}
        <path d="M10.6 2.4a3.4 3.4 0 0 0-3.3 5.7L3 12.4l1.4 1.4 4.3-4.3a3.4 3.4 0 0 0 4.6-4.3l-2 2-1.7-1.7Z" />
      </Glyph>
    )
  }
  return (
    <Glyph size={size} filled>
      {/* A spark: the one of the three that is new work. */}
      <path d="M8 2.2 9.4 6.6 13.8 8 9.4 9.4 8 13.8 6.6 9.4 2.2 8l4.4-1.4Z" />
    </Glyph>
  )
}

/**
 * A task's status, for the tile at the head of a card that is not simply
 * running. The word itself is never dropped — it stays on the pill beside it —
 * so this is the same kind of shorthand the type tile is.
 *
 * Nothing is drawn for `active`: a card that is just getting on with it wears
 * its type there instead, which is the more useful of the two facts.
 */
export function StatusIcon({ status, size = 14 }: { status: TaskStatus; size?: number }) {
  if (status === 'hold') {
    return (
      <Glyph size={size} weight={1.7}>
        {/* Two bars: paused, and able to start again. */}
        <path d="M6.2 4.4v7.2M9.8 4.4v7.2" />
      </Glyph>
    )
  }
  if (status === 'blocked') {
    return (
      <Glyph size={size} weight={1.7}>
        {/* Barred, not crossed out: something is in the way of this, and the
            thing in the way is somebody else's. */}
        <circle cx="8" cy="8" r="5.4" />
        <path d="M4.2 4.2 11.8 11.8" />
      </Glyph>
    )
  }
  return (
    <Glyph size={size} weight={1.7}>
      {/* A cross: not going to happen. */}
      <path d="M4.6 4.6 11.4 11.4M11.4 4.6 4.6 11.4" />
    </Glyph>
  )
}

export function PriorityIcon({ priority, size = 14 }: { priority: TaskPriority; size?: number }) {
  // One shape rotated through four positions, so the four values read as one
  // scale: two chevrons up, one up, level, one down.
  if (priority === 'p0') {
    return (
      <Glyph size={size} weight={1.8}>
        <path d="M3.5 8.5 8 4l4.5 4.5M3.5 12 8 7.5l4.5 4.5" />
      </Glyph>
    )
  }
  if (priority === 'p1') {
    return (
      <Glyph size={size} weight={1.8}>
        <path d="M3.5 10.2 8 5.8l4.5 4.4" />
      </Glyph>
    )
  }
  if (priority === 'p2') {
    return (
      <Glyph size={size} weight={1.8}>
        <path d="M3.5 6.4h9M3.5 9.6h9" />
      </Glyph>
    )
  }
  return (
    <Glyph size={size} weight={1.8}>
      <path d="M3.5 5.8 8 10.2l4.5-4.4" />
    </Glyph>
  )
}

/**
 * The rest of the card's shorthand, drawn in the same hand as the type and
 * priority marks above.
 *
 * These five were typed characters until now — `✎ ☑ ⌷ ⎇ ⚠` picked out of the
 * font because they were to hand. A dingbat is at the mercy of whatever font
 * has it: the four rendered at four different weights and three different
 * heights on the same row, and `⌷` and `⎇` are missing often enough to show
 * as a box. Drawn, they line up with the glyphs already on the card and sit on
 * the baseline the row gives them.
 */

/** How much has been said about this card. */
export function CommentIcon({ size = 14 }: { size?: number }) {
  return (
    <Glyph size={size}>
      <path d="M13.2 9.6a1.9 1.9 0 0 1-1.9 1.9H5.7L3 13.9V4.7a1.9 1.9 0 0 1 1.9-1.9h6.4a1.9 1.9 0 0 1 1.9 1.9Z" />
    </Glyph>
  )
}

/** Jira's four-diamond mark, near enough to be recognised at 14px. */
export function JiraIcon({ size = 14 }: { size?: number }) {
  return (
    <Glyph size={size}>
      <path d="M8 2.4 13.6 8 8 13.6 2.4 8Z" />
      <path d="M8 5.6 10.4 8 8 10.4 5.6 8Z" />
    </Glyph>
  )
}

/**
 * A pull request, drawn by how far it got (CYLIST-76).
 *
 * Three drawings rather than one in three colours. The mark is 14px on a card
 * that may be tinted amber or red, and a reader who cannot tell the green from
 * the violet would otherwise be reading a row of identical glyphs — so the
 * shape says it too, and the weight on top of that: merged is the only one
 * with its nodes filled in.
 *
 * All three keep the branch-and-trunk silhouette every forge draws a pull
 * request as, because on a board the mark alone has to say *what kind of fact
 * this is* before it says anything about its state. A bare tick in the state's
 * place would have been read as a finished sub-task.
 */
export function PrIcon({ state, size = 14 }: { state: PrState; size?: number }) {
  if (state === 'approved') {
    return (
      <Glyph size={size}>
        {/* The branch ends in the tick rather than carrying one as a badge: a
            badge at this size is four pixels across and reads as a smudge. */}
        <circle cx="4.7" cy="4" r="1.7" />
        <circle cx="4.7" cy="12" r="1.7" />
        <path d="M4.7 5.7v4.6M11.3 9.4V8.4a2.1 2.1 0 0 0-2.1-2.1H6.4" />
        <path d="M9 11.9 10.7 13.6 13.6 10.7" />
      </Glyph>
    )
  }

  if (state === 'merged') {
    return (
      <Glyph size={size}>
        {/* The branch meets the trunk and the trunk carries on past the join,
            which is what "it is in" looks like drawn. The nodes are painted
            solid — `fill` and `stroke` per circle, because a Glyph is one or
            the other — so the mark is heavier as well as a different shape. */}
        <circle cx="4.7" cy="12" r="1.7" fill="currentColor" stroke="none" />
        <circle cx="11.3" cy="4" r="1.7" fill="currentColor" stroke="none" />
        <path d="M4.7 3v7.3" />
        <path d="M11.3 5.7v0.6a2.1 2.1 0 0 1-2.1 2.1H4.7" />
      </Glyph>
    )
  }

  // Raised: the mark as it was drawn before there were states, because this is
  // the state every reference the board already had is now in.
  return (
    <Glyph size={size}>
      <circle cx="4.7" cy="4" r="1.7" />
      <circle cx="4.7" cy="12" r="1.7" />
      <circle cx="11.3" cy="12" r="1.7" />
      <path d="M4.7 5.7v4.6M11.3 10.3V8.4a2.1 2.1 0 0 0-2.1-2.1H6.4" />
    </Glyph>
  )
}

/** A date that is simply a date. */
export function CalendarIcon({ size = 14 }: { size?: number }) {
  return (
    <Glyph size={size}>
      <rect x="2.6" y="3.9" width="10.8" height="9.5" rx="1.9" />
      <path d="M2.6 7h10.8M5.6 2.5v2.6M10.4 2.5v2.6" />
    </Glyph>
  )
}

/** A date that has come round: the calendar, with the day running out of it. */
export function ClockIcon({ size = 14 }: { size?: number }) {
  return (
    <Glyph size={size}>
      <circle cx="8" cy="8" r="5.4" />
      <path d="M8 4.9v3.3l2.2 1.3" />
    </Glyph>
  )
}

/** A date that has gone. */
export function AlertIcon({ size = 14 }: { size?: number }) {
  return (
    <Glyph size={size}>
      <path d="M8 2.9 14.2 13.4H1.8Z" />
      <path d="M8 6.8v2.6" />
      <circle cx="8" cy="11.4" r="0.75" fill="currentColor" stroke="none" />
    </Glyph>
  )
}

/**
 * An envelope, for an address.
 *
 * The last of the typed characters to go — `✉` was a dingbat sat next to an
 * email address, and like the four before it, it arrived at whatever weight
 * and height the reader's font happened to draw it at. Drawn, it is the same
 * hand as everything else on the row.
 */
export function MailIcon({ size = 14 }: { size?: number }) {
  return (
    <Glyph size={size}>
      <rect x="2.4" y="3.6" width="11.2" height="8.8" rx="1.6" />
      <path d="m2.8 4.6 5.2 3.8 5.2-3.8" />
    </Glyph>
  )
}
