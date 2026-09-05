/**
 * The month a goals calendar draws, worked out apart from the drawing of it.
 *
 * A month is a grid of six-and-a-bit weeks whose edges belong to the months
 * either side, and getting that wrong is the sort of thing that shows up once
 * a year — a leap day, a month that starts on a Sunday — long after anybody is
 * looking. So it is a function of a year and a month with no React in it, and
 * there are tests.
 */

import type { Goal } from '../api/client'
import { localDate } from '../components/dates'

/** One cell. `iso` is the key everything else is filed under. */
export interface CalendarDay {
  /** `YYYY-MM-DD` in the reader's own timezone — see `localDate`. */
  iso: string
  day: number
  /** False for the days either side that fill the first and last rows. */
  inMonth: boolean
  isToday: boolean
}

/** A date as the API writes one, read back in the reader's timezone. */
export function isoOf(date: Date): string {
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

/** Monday first: the app says its dates in en-GB, and so does its week. */
export const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'] as const

/**
 * The weeks of one month, each a full seven days.
 *
 * Whole rows rather than blanks at the ends: a goal targeted at the 1st is
 * read alongside the last days of the month before it, and a cell that is
 * empty because the month has not started yet is still a day somebody can look
 * at. Five rows for most months, six when a long month starts late.
 */
export function monthWeeks(year: number, month: number, today = new Date()): CalendarDay[][] {
  const first = new Date(year, month, 1)
  // getDay() is Sunday-first; this is the number of days back to the Monday
  // on or before the 1st.
  const lead = (first.getDay() + 6) % 7
  const days = new Date(year, month + 1, 0).getDate()
  const rows = Math.ceil((lead + days) / 7)
  const todayIso = isoOf(today)

  return Array.from({ length: rows }, (_, row) =>
    Array.from({ length: 7 }, (_, column) => {
      // Day-of-month arithmetic rather than adding milliseconds: `new Date`
      // rolls over the ends of months and years for us, and does it across a
      // daylight-saving change without landing on 23:00 the day before.
      const date = new Date(year, month, 1 - lead + row * 7 + column)
      const iso = isoOf(date)
      return {
        iso,
        day: date.getDate(),
        inMonth: date.getMonth() === first.getMonth(),
        isToday: iso === todayIso,
      }
    }),
  )
}

/** The month a heading says, in words. */
export function monthName(year: number, month: number): string {
  return new Date(year, month, 1).toLocaleDateString('en-GB', { month: 'long', year: 'numeric' })
}

/** The same day said in full, for a cell that shows only its number. */
export function dayName(iso: string): string {
  return localDate(iso).toLocaleDateString('en-GB', {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  })
}

/**
 * The goals of each day, and the ones with no day at all.
 *
 * Undated goals are handed back rather than dropped: a calendar that silently
 * hides a third of the list is a calendar that gets somebody in trouble, so
 * the page draws them in a strip of their own beneath the grid.
 */
export function goalsByDate(goals: readonly Goal[]): {
  dated: Map<string, Goal[]>
  undated: Goal[]
} {
  const dated = new Map<string, Goal[]>()
  const undated: Goal[] = []

  for (const goal of goals) {
    if (goal.target_date === null) {
      undated.push(goal)
      continue
    }
    const on = dated.get(goal.target_date)
    if (on) on.push(goal)
    else dated.set(goal.target_date, [goal])
  }

  return { dated, undated }
}

/**
 * Which month to open on.
 *
 * This month, unless nothing is targeted anywhere near it: a project whose
 * goals are all in March is a project whose calendar should open in March
 * rather than on an empty September the reader has to page out of. The nearest
 * target either side of today wins, and a project with no dates at all opens
 * where the reader already is.
 */
export function openingMonth(goals: readonly Goal[], today = new Date()): Date {
  const here = new Date(today.getFullYear(), today.getMonth(), 1)
  const dates = goals
    .map((goal) => goal.target_date)
    .filter((date): date is string => date !== null)
    .map(localDate)
  if (dates.length === 0) return here

  const inThisMonth = dates.some(
    (date) => date.getFullYear() === today.getFullYear() && date.getMonth() === today.getMonth(),
  )
  if (inThisMonth) return here

  const nearest = dates.reduce((best, date) =>
    Math.abs(date.getTime() - today.getTime()) < Math.abs(best.getTime() - today.getTime())
      ? date
      : best,
  )
  return new Date(nearest.getFullYear(), nearest.getMonth(), 1)
}
