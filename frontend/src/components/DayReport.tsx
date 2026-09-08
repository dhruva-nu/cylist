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
 * It is a button in the sidebar and a dialog over the page — the shape it had
 * before it became a section that folded open, and the right one. A report is
 * a thing you ask for: you want a whole day at the width a day of work is
 * written at, you read it, you copy it, and you put it away. A panel a third
 * of that width had it competing with the page for room it did not need most
 * of the time, and a fold is a poor way to ask for anything — there is no
 * moment at which you have *requested* a report, only a section that happens
 * to be open.
 *
 * What the sidebar keeps is where you ask from. The old button hung off the
 * project hub alone, so reading yesterday from the board meant leaving the
 * board to do it; here it is beside every screen, and the report opens over
 * whichever one you are on.
 *
 * Every card the report names opens. A report that only tells you what
 * happened leaves you to go and find the card it happened to, which means a
 * search box and the reference typed out by hand — for the sake of a card the
 * report is already holding the reference of. So the head of a card's day is
 * a button, and it opens the same dialog the board opens, over the report.
 * What does not open is a card that is gone: a day can hold the last things
 * that ever happened to something since deleted, and those lines stay as text.
 */

import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useMatchRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { api, type DayReport, type TaskDay, type TaskHistoryEntry } from '../api/client'
import { localToday } from './dates'
import { Modal, ModalBody } from './Modal'
import { OpenCard } from './OpenCard'
import { SidebarAction } from './SidebarSection'
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

/**
 * The button in the sidebar, and everything it opens.
 *
 * Both dialogs are held here rather than one inside the other. Rendered as
 * siblings, a card comes out on top of the report simply by being second, and
 * closing it leaves the report exactly as it was — which is the point of
 * opening a card *from* a report rather than instead of one.
 */
export function DayReportPanel() {
  const matchRoute = useMatchRoute()
  const match = matchRoute({ to: '/p/$projectKey', fuzzy: true })
  const projectKey = match ? match.projectKey : null

  const [reporting, setReporting] = useState(false)
  /** The card being read over the report, by reference — which is what the
   * report holds and what `/tasks/{ref}` takes, so there is nothing to look up
   * before opening one. */
  const [reading, setReading] = useState<string | null>(null)

  return (
    /* No count on this one. Today's is worth carrying because it changes on
       its own and you want to be told; a report is as long as the day was,
       and a number saying so is a number nobody acts on. */
    <SidebarAction
      name="Day report"
      disabled={projectKey === null}
      title={projectKey === null ? 'Open a project to read a day of it' : undefined}
      onOpen={() => setReporting(true)}
    >
      {reporting && projectKey !== null ? (
        <ReportDialog
          projectKey={projectKey}
          onOpenCard={setReading}
          onClose={() => setReporting(false)}
        />
      ) : null}

      {reading !== null && projectKey !== null ? (
        <OpenCard
          projectKey={projectKey}
          reference={reading}
          onOpen={setReading}
          onClose={() => setReading(null)}
        />
      ) : null}
    </SidebarAction>
  )
}

function ReportDialog({
  projectKey,
  onOpenCard,
  onClose,
}: {
  projectKey: string
  onOpenCard: (reference: string) => void
  onClose: () => void
}) {
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
    <Modal
      title="Day report"
      onClose={onClose}
      footer={
        <>
          <span className={styles.zone}>Midnight to midnight, {shown?.timezone ?? zone}</span>
          <Button
            variant="go"
            disabled={!shown || shown.entry_count === 0}
            onClick={() => shown && void copy(shown.markdown)}
          >
            {copied ? 'Copied' : 'Copy as Markdown'}
          </Button>
          <Button onClick={onClose}>Close</Button>
        </>
      }
    >
      <ModalBody>
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
        {shown === undefined && report.isFetching ? (
          <EmptyState>Reading the day…</EmptyState>
        ) : null}
        {shown ? <Report report={shown} onOpen={onOpenCard} /> : null}
      </ModalBody>
    </Modal>
  )
}

/**
 * Whether there is still a card behind a line of the report.
 *
 * A card names the column it is in and a sub-task names its parent; something
 * with neither has been deleted, and the report is holding the last things
 * that ever happened to it. That one is text — a button that opened a 404 is
 * worse than no button.
 */
function stillThere(card: TaskDay): boolean {
  return card.column !== null || card.parent !== null
}

function Report({ report, onOpen }: { report: DayReport; onOpen: (reference: string) => void }) {
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
                  {stillThere(card) ? (
                    <button
                      type="button"
                      className={styles.open}
                      title={`Open ${card.reference}`}
                      onClick={() => onOpen(card.reference)}
                    >
                      <b>{card.reference}</b> {card.title}
                    </button>
                  ) : (
                    <>
                      <b>{card.reference}</b> {card.title}
                    </>
                  )}
                </li>
              ))}
          </ul>
        </section>
      ) : null}

      {report.tasks.length ? (
        <section>
          <h3 className={styles.section}>Cards</h3>
          {report.tasks.map((card) => (
            <Card key={card.reference} card={card} onOpen={onOpen} />
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

function Card({ card, onOpen }: { card: TaskDay; onOpen: (reference: string) => void }) {
  const head = (
    <>
      <span className={styles.reference}>{card.reference}</span>
      <span className={styles.title}>{card.title}</span>
      {/* Where this piece of work stands: a card names its column, a sub-task
          names the card it belongs to, and something with neither has been
          deleted. Read straight off the column, an absent one would make
          every sub-task in the report look deleted. */}
      <span className={card.finished ? styles.done : styles.where}>
        {card.column ?? (card.parent ? `of ${card.parent}` : 'deleted')}
      </span>
    </>
  )

  return (
    <div className={styles.card}>
      {/* The head opens the card and the lines beneath it stay text. The head
          is what names the card — reference, title and where it is — so it is
          the part of the block that is *about* the card rather than about a
          thing that happened to it, and it is one target rather than a row of
          them. */}
      {stillThere(card) ? (
        <button
          type="button"
          className={`${styles.cardHead} ${styles.cardOpen}`}
          title={`Open ${card.reference}`}
          onClick={() => onOpen(card.reference)}
        >
          {head}
        </button>
      ) : (
        <div className={styles.cardHead}>{head}</div>
      )}
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
