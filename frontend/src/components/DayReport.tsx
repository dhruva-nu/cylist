/**
 * What you did on a project today, and any other day.
 *
 * Every line here is written by the server — see
 * `app/services/activity.describe` — so this side only decides what a day
 * looks like: the cards you touched, in the order you touched them, what
 * happened to each, and what happened away from the board. The wording is
 * deliberately not reconstructed from verbs here, for the same reason a card's
 * history does not: two clients wording the same event differently is how a
 * record stops being one.
 *
 * The day is cut in this browser's own zone, which is sent with the request.
 * Left to itself the server cuts in UTC, and an evening's work in Kolkata
 * would show up in tomorrow's report.
 *
 * It used to be a dialog, opened from a button beside the project's title.
 * That was the wrong shape twice over. A report is something you read *while*
 * looking at the work it is about — and a dialog is the one place you cannot
 * be looking at anything else — and it was reachable from the hub alone, so
 * reading yesterday from the board meant leaving the board. In the sidebar it
 * is beside whatever screen you are on, and stepping back through a week no
 * longer covers the thing you are stepping back through.
 */

import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useMatchRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { api, type DayReport, type TaskDay, type TaskHistoryEntry } from '../api/client'
import { localToday } from './dates'
import { SidebarSection } from './SidebarSection'
import { Button, EmptyState, ErrorBanner } from './ui'
import styles from './DayReport.module.css'

/** The zone this browser is in, as an IANA name the server can cut a day by. */
function localZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
}

/**
 * The day `by` days from this one.
 *
 * Stepped through UTC on purpose: `new Date('2026-09-02')` is midnight UTC, so
 * doing the arithmetic there cannot land on the previous day for a reader west
 * of it. The result is a plain date either way — no time of day survives.
 */
function shiftDay(day: string, by: number): string {
  const [year = 0, month = 1, date = 1] = day.split('-').map(Number)
  return new Date(Date.UTC(year, month - 1, date + by)).toISOString().slice(0, 10)
}

export function DayReportPanel({ folded, onToggle }: { folded: boolean; onToggle: () => void }) {
  const matchRoute = useMatchRoute()
  const match = matchRoute({ to: '/p/$projectKey', fuzzy: true })

  return (
    <SidebarSection name="Day report" folded={folded} onToggle={onToggle}>
      {match ? (
        <Day projectKey={match.projectKey} />
      ) : (
        <p className={styles.aside}>Open a project to read its day.</p>
      )}
    </SidebarSection>
  )
}

function Day({ projectKey }: { projectKey: string }) {
  const [day, setDay] = useState(localToday)
  const [copied, setCopied] = useState(false)
  const zone = localZone()
  const today = localToday()

  const report = useQuery({
    queryKey: ['day-report', projectKey, day, zone],
    queryFn: () => api.getDayReport(projectKey, day, zone),
    // The day on screen stays while the next one loads. A report that empties
    // itself between clicks makes stepping through a week unreadable.
    placeholderData: keepPreviousData,
  })

  function go(to: string) {
    setDay(to)
    setCopied(false)
  }

  async function copy(markdown: string) {
    try {
      await navigator.clipboard.writeText(markdown)
      setCopied(true)
    } catch {
      // Denied clipboard permission, or an insecure origin. Saying so beats a
      // button that looks as though it worked.
      setCopied(false)
      window.prompt('Copy the report from here:', markdown)
    }
  }

  const shown = report.data

  return (
    <div className={styles.panel}>
      <div className={styles.picker}>
        <Button
          small
          variant="ghost"
          aria-label="The day before"
          onClick={() => go(shiftDay(day, -1))}
        >
          ‹
        </Button>
        <label className={styles.dayField}>
          <span className="visually-hidden">Which day to report on</span>
          <input
            type="date"
            value={day}
            max={today}
            onChange={(event) => go(event.currentTarget.value || today)}
          />
        </label>
        <Button
          small
          variant="ghost"
          aria-label="The day after"
          // Tomorrow cannot hold anything yet, so there is nothing to go to.
          disabled={day >= today}
          onClick={() => go(shiftDay(day, 1))}
        >
          ›
        </Button>
        <Button small variant="ghost" disabled={day === today} onClick={() => go(today)}>
          Today
        </Button>
      </div>

      {report.error ? <ErrorBanner>{report.error.message}</ErrorBanner> : null}
      {shown === undefined && report.isFetching ? <EmptyState>Reading the day…</EmptyState> : null}
      {shown ? <Report report={shown} /> : null}

      {/* Under the report rather than over it: the window it covered and the
          button that copies it are both things you want once you have read
          it, and a footer of chrome above a report you have not read yet is
          a footer in the way. */}
      {shown ? (
        <div className={styles.foot}>
          <Button
            small
            variant="go"
            disabled={shown.entry_count === 0}
            onClick={() => void copy(shown.markdown)}
          >
            {copied ? 'Copied' : 'Copy as Markdown'}
          </Button>
          <span className={styles.zone}>Midnight to midnight, {shown.timezone}</span>
        </div>
      ) : null}
    </div>
  )
}

function Report({ report }: { report: DayReport }) {
  return (
    <div className={styles.report}>
      <p className={styles.headline}>{report.headline}</p>

      {report.finished.length ? (
        <section>
          <h3 className={styles.section}>Finished</h3>
          <ul className={styles.finished}>
            {report.tasks
              .filter((card) => card.finished)
              .map((card) => (
                <li key={card.reference}>
                  <b>{card.reference}</b> {card.title}
                </li>
              ))}
          </ul>
        </section>
      ) : null}

      {report.tasks.length ? (
        <section>
          <h3 className={styles.section}>Cards</h3>
          {report.tasks.map((card) => (
            <Card key={card.reference} card={card} />
          ))}
        </section>
      ) : null}

      {report.elsewhere.length ? (
        <section>
          <h3 className={styles.section}>Elsewhere on the project</h3>
          <div className={styles.entries}>
            {report.elsewhere.map((entry) => (
              <Entry key={entry.id} entry={entry} />
            ))}
          </div>
        </section>
      ) : null}
    </div>
  )
}

function Card({ card }: { card: TaskDay }) {
  return (
    <div className={styles.card}>
      <div className={styles.cardHead}>
        <span className={styles.reference}>{card.reference}</span>
        <span className={styles.title}>{card.title}</span>
        {/* Where this piece of work stands: a card names its column, a sub-task
            names the card it belongs to, and something with neither has been
            deleted. Read straight off the column, an absent one would make
            every sub-task in the report look deleted. */}
        <span className={card.finished ? styles.done : styles.where}>
          {card.column ?? (card.parent ? `of ${card.parent}` : 'deleted')}
        </span>
      </div>
      <div className={styles.entries}>
        {card.entries.map((entry) => (
          <Entry key={entry.id} entry={entry} />
        ))}
      </div>
    </div>
  )
}

/** One thing that happened: when, what, and who if it was not a person. */
function Entry({ entry }: { entry: TaskHistoryEntry }) {
  const at = new Date(entry.occurred_at).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  })

  return (
    <p className={styles.entry}>
      <time className={styles.at} dateTime={entry.occurred_at}>
        {at}
      </time>
      <span className={styles.what}>{entry.summary}</span>
      {/* Agents act through the same API as the browser, so this is the only
          thing on the line that says the day's work was not all yours. */}
      {entry.channel === 'api' ? <span className={styles.agent}>{entry.actor_label}</span> : null}
    </p>
  )
}
