/**
 * Reading a date the API sent, and saying how near it is.
 *
 * Shared rather than kept beside the card that first needed it: a card's due
 * date and a goal's target date are the same question asked of two different
 * things, and two answers to "is this late" that could drift apart is exactly
 * the bug this file exists to make impossible.
 */

/**
 * `YYYY-MM-DD` as a date in the reader's own timezone.
 *
 * `new Date(iso)` would read it as UTC midnight, which shows as the previous
 * day anywhere west of Greenwich — and a due date that is off by one is worse
 * than no due date at all.
 */
export function localDate(iso: string): Date {
  const [year = 1970, month = 1, day = 1] = iso.split('-').map(Number)
  return new Date(year, month - 1, day)
}

export function formatDue(iso: string): string {
  return localDate(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}

/** The same date said in full, for the tooltip on a mark that abbreviates it. */
export function formatDueLong(iso: string): string {
  return localDate(iso).toLocaleDateString('en-GB', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  })
}

/**
 * How near a due date is, in the five steps a card draws differently.
 *
 * This was a boolean — overdue or not — and a boolean is the wrong shape for
 * the question. Every date that had not yet passed looked identical, so a card
 * wanted this afternoon sat in the column looking exactly like one wanted next
 * month, and the only moment the card ever changed was the moment it was too
 * late to matter. Five steps put the colour where the urgency is.
 *
 * `late` carries its own day count because "how far past" is the part anybody
 * acts on: a card one day over is a card to finish, and a card three weeks
 * over is a card to have a conversation about.
 */
export type DueBucket =
  | { kind: 'late'; days: number }
  | { kind: 'today' }
  | { kind: 'tomorrow' }
  | { kind: 'soon' }
  | { kind: 'later' }
  | { kind: 'none' }

/**
 * Whole days from today until a date, negative once it has gone by.
 *
 * Its own function because the card asks the same question twice now — which
 * step of the ramp a date is on, and whether it is near enough to earn a tab —
 * and two midnights worked out in two places is precisely the drift this file
 * exists to make impossible.
 */
export function daysUntilDue(iso: string): number {
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  // Rounded, not truncated: the two midnights are an hour apart rather than a
  // whole number of days across a daylight-saving change, and a date that came
  // out at 6.96 days would otherwise be filed a step nearer than it is.
  return Math.round((localDate(iso).getTime() - today.getTime()) / 86_400_000)
}

/** A card with no date is never late: there is no day it was wanted by. */
export function dueBucket(iso: string | null): DueBucket {
  if (!iso) return { kind: 'none' }

  const days = daysUntilDue(iso)

  if (days < 0) return { kind: 'late', days: -days }
  if (days === 0) return { kind: 'today' }
  if (days === 1) return { kind: 'tomorrow' }
  if (days <= 7) return { kind: 'soon' }
  return { kind: 'later' }
}
