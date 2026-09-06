/**
 * The month grid a goals calendar is drawn on.
 *
 * The arithmetic is the part that fails quietly and rarely: a month that
 * starts on a Sunday, a leap February, a goal filed one cell out. All of it
 * happens here, away from the rendering, so it can be checked without a
 * browser.
 */

import { describe, expect, it } from 'vitest'

import type { Goal, Person } from '../api/client'
import { WEEKDAYS, goalsByDate, isoOf, monthWeeks, openingMonth } from './goalCalendar'

const ADITI: Person = {
  id: 'person-1',
  name: 'Aditi K',
  kind: 'team',
  role: 'Backend engineer',
  responsibilities: '',
  email: null,
  colour: '#1D7D46',
  archived_at: null,
  created_at: '2026-01-01T00:00:00Z',
  is_me: false,
}

function goal(reference: string, target: string | null): Goal {
  return {
    id: reference,
    project_id: 'project-1',
    reference,
    number: 1,
    name: reference,
    description: '',
    colour: '#3B6FC2',
    status: 'open',
    target_date: target,
    achieved_at: null,
    owner: ADITI,
    progress: { total: 0, done: 0, cancelled: 0, open: 0, blocked: 0, on_hold: 0 },
    created_at: '2026-01-01T00:00:00Z',
  }
}

describe('monthWeeks', () => {
  it('starts every row on a Monday and fills it', () => {
    const weeks = monthWeeks(2026, 8, new Date(2026, 8, 6))
    for (const week of weeks) expect(week).toHaveLength(7)
    // 1 September 2026 is a Tuesday, so the first row opens on 31 August.
    expect(weeks[0]?.[0]?.iso).toBe('2026-08-31')
    expect(WEEKDAYS[0]).toBe('Mon')
  })

  it('marks the days either side of the month as outside it', () => {
    const weeks = monthWeeks(2026, 8, new Date(2026, 8, 6)).flat()
    expect(weeks[0]).toMatchObject({ iso: '2026-08-31', day: 31, inMonth: false })
    expect(weeks[1]).toMatchObject({ iso: '2026-09-01', day: 1, inMonth: true })
    expect(weeks.filter((day) => day.inMonth)).toHaveLength(30)
    expect(weeks.at(-1)?.inMonth).toBe(false)
  })

  it('takes a sixth row only when the month needs one', () => {
    // February 2026 is 28 days from a Sunday: seven rows' worth of nothing
    // would be five rows, and it is.
    expect(monthWeeks(2026, 1, new Date(2026, 1, 1))).toHaveLength(5)
    // August 2026 starts on a Saturday and runs 31 days — six rows.
    expect(monthWeeks(2026, 7, new Date(2026, 7, 1))).toHaveLength(6)
  })

  it('counts a leap February in full', () => {
    const days = monthWeeks(2028, 1, new Date(2028, 1, 1))
      .flat()
      .filter((day) => day.inMonth)
    expect(days).toHaveLength(29)
    expect(days.at(-1)?.iso).toBe('2028-02-29')
  })

  it('marks today, and only today', () => {
    const today = monthWeeks(2026, 8, new Date(2026, 8, 6, 23, 30))
      .flat()
      .filter((day) => day.isToday)
    expect(today.map((day) => day.iso)).toEqual(['2026-09-06'])
  })

  it('writes the date in the reader’s own timezone, not UTC', () => {
    // The bug this guards: a date turned into an ISO string through UTC shows
    // as the day before anywhere west of Greenwich.
    expect(isoOf(new Date(2026, 0, 1, 0, 30))).toBe('2026-01-01')
    expect(isoOf(new Date(2026, 11, 31, 23, 30))).toBe('2026-12-31')
  })
})

describe('goalsByDate', () => {
  it('files goals under their target and keeps the undated ones', () => {
    const { dated, undated } = goalsByDate([
      goal('G1', '2026-09-30'),
      goal('G2', '2026-09-30'),
      goal('G3', null),
    ])

    expect(dated.get('2026-09-30')?.map((one) => one.reference)).toEqual(['G1', 'G2'])
    expect(undated.map((one) => one.reference)).toEqual(['G3'])
  })
})

describe('openingMonth', () => {
  const today = new Date(2026, 8, 6)

  it('opens on this month when anything is targeted in it', () => {
    expect(openingMonth([goal('G1', '2026-09-30')], today)).toEqual(new Date(2026, 8, 1))
  })

  it('opens on this month when nothing is targeted at all', () => {
    expect(openingMonth([goal('G1', null)], today)).toEqual(new Date(2026, 8, 1))
  })

  it('otherwise opens on the month of the nearest target', () => {
    expect(openingMonth([goal('G1', '2027-03-04'), goal('G2', '2026-11-20')], today)).toEqual(
      new Date(2026, 10, 1),
    )
    // Behind counts as near as ahead: a goal that slipped last month is the
    // one somebody came to look at.
    expect(openingMonth([goal('G1', '2026-08-10'), goal('G2', '2026-12-01')], today)).toEqual(
      new Date(2026, 7, 1),
    )
  })
})
