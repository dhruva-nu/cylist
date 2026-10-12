import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { queryKeys } from '../../api/queries'
import { useState } from 'react'
import { api, type TaskHistoryEntry } from '../../api/client'
import { Button, ErrorNotice } from '../ui'
import shared from './shared.module.css'
import styles from './History.module.css'

/**
 * What has been done to this card: every change, when, and who made it.
 *
 * The counterpart to the timeline above it, and deliberately not merged into
 * it. The timeline is what people *said*; this is what was *done* — and the
 * two are read for different reasons. Someone scrolling a card to catch up
 * wants the conversation; someone asking "why does this say Thursday now?"
 * wants the record, and interleaving the two buries each in the other.
 *
 * Closed to begin with, fetched only when it is opened, and then ten entries
 * at a time. A card worked on for a month carries a long record that nobody
 * opened the dialog to read: loading it on every open would slow the common
 * case to serve the rare one, and loading all of it at once would bury the
 * only part most readers want — the last thing that happened.
 *
 * The wording comes from the server rather than from a verb-to-sentence map
 * here, so the board, the CLI and an agent reading the API all tell the same
 * story. This side only decides what the record looks like.
 */
export function History({ taskId }: { taskId: string }) {
  const [open, setOpen] = useState(false)
  const [page, setPage] = useState(1)

  const history = useQuery({
    queryKey: [...queryKeys.taskHistory(taskId), page],
    queryFn: () => api.getTaskHistory(taskId, page),
    enabled: open,
    // The previous page stays on screen while the next one loads. A pager that
    // empties itself between clicks makes the dialog jump under the cursor.
    placeholderData: keepPreviousData,
  })

  const historyPage = history.data

  return (
    <details
      className={styles.history}
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary className={styles.historySummary}>
        <span className={styles.historyTitle}>History</span>
        <span className={styles.historyHint}>what changed, and who changed it</span>
      </summary>

      <ErrorNotice error={history.error} />

      <div className={styles.historyList}>
        {historyPage === undefined && history.isFetching ? (
          <span className={shared.empty}>Loading…</span>
        ) : null}
        {historyPage?.total === 0 ? (
          <span className={shared.empty}>Nothing has happened to this card yet.</span>
        ) : null}
        {historyPage?.entries.map((entry) => (
          <HistoryEntry key={entry.id} entry={entry} />
        ))}
      </div>

      {historyPage && historyPage.pages > 1 ? (
        <Pager
          page={historyPage.page}
          pages={historyPage.pages}
          total={historyPage.total}
          onGo={setPage}
        />
      ) : null}
    </details>
  )
}

/**
 * Which page of the history is showing, and how to reach the others.
 *
 * Every page is a numbered button rather than only Previous and Next: a record
 * is usually read for a particular moment — "what did it look like in March" —
 * and stepping there one page at a time means loading everything in between.
 * A history long enough for that to become a wall of numbers is one nobody
 * navigates by number anyway, so past nine pages the middle is elided.
 */
function Pager({
  page,
  pages,
  total,
  onGo,
}: {
  page: number
  pages: number
  total: number
  onGo: (page: number) => void
}) {
  return (
    <nav className={styles.pager} aria-label="History pages">
      <Button
        small
        variant="ghost"
        disabled={page === 1}
        aria-label="Previous page"
        onClick={() => onGo(page - 1)}
      >
        ‹
      </Button>

      {pageNumbers(page, pages).map((number, index) =>
        number === null ? (
          <span key={`gap-${index}`} className={styles.pagerGap} aria-hidden="true">
            …
          </span>
        ) : (
          <button
            key={number}
            type="button"
            className={number === page ? styles.pageOn : styles.page}
            aria-label={`Page ${number} of ${pages}`}
            aria-current={number === page ? 'page' : undefined}
            onClick={() => onGo(number)}
          >
            {number}
          </button>
        ),
      )}

      <Button
        small
        variant="ghost"
        disabled={page === pages}
        aria-label="Next page"
        onClick={() => onGo(page + 1)}
      >
        ›
      </Button>

      <span className={styles.pagerCount}>{total} entries</span>
    </nav>
  )
}

/** How many page buttons fit before the middle has to be elided. */
const PAGER_WIDTH = 9

/**
 * The page numbers to draw, with `null` standing for an elision.
 *
 * The first and last are always reachable — the beginning and the end of a
 * record are the two moments anybody jumps to — and the rest of the room goes
 * to the pages either side of where you are.
 */
function pageNumbers(page: number, pages: number): (number | null)[] {
  if (pages <= PAGER_WIDTH) return Array.from({ length: pages }, (_, index) => index + 1)

  const span = PAGER_WIDTH - 4 // first, last, and an elision at each end
  const middleStart = Math.min(Math.max(page - Math.floor(span / 2), 2), pages - span)
  const middle = Array.from({ length: span }, (_, index) => middleStart + index)

  return [
    1,
    ...(middleStart > 2 ? [null] : []),
    ...middle,
    ...(middleStart + span <= pages - 1 ? [null] : []),
    pages,
  ]
}

/** One thing that happened, and the fields it moved. */
function HistoryEntry({ entry }: { entry: TaskHistoryEntry }) {
  const when = new Date(entry.occurred_at).toLocaleString('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })

  return (
    <div className={styles.historyEntry}>
      <div className={styles.historyLine}>
        <span>{entry.summary}</span>
        <span className={styles.when}>{when}</span>
      </div>
      <div className={styles.historyWho}>
        {entry.actor_label}
        {/* Agents act through the same API as the browser, so the record is
            the only place that says a person did not do this. */}
        {entry.channel === 'api' ? <span className={styles.agent}>agent</span> : null}
      </div>
      {entry.changes.length ? (
        <dl className={styles.changes}>
          {entry.changes.map((change) => (
            <div key={change.field} className={styles.change}>
              <dt>{change.label}</dt>
              <dd>
                <span className={styles.was}>{asOneLine(change.from)}</span>
                <span aria-hidden="true"> → </span>
                <span className="visually-hidden"> became </span>
                <span className={styles.now}>{asOneLine(change.to)}</span>
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
    </div>
  )
}

/** How much of a changed value the record shows before it gets in the way. */
const LONGEST_VALUE_SHOWN = 90

/**
 * A field's value as one short line.
 *
 * Truncated, because a description is a paragraph and a history that prints
 * two of them per edit is a history you have to scroll past rather than read.
 * The full text is on the card itself, which is the thing this is a record of.
 */
function asOneLine(value: string | number | string[] | null): string {
  if (value === null || value === '') return '—'
  const text = Array.isArray(value) ? value.join(' · ') : String(value)
  if (text.length <= LONGEST_VALUE_SHOWN) return text
  return `${text.slice(0, LONGEST_VALUE_SHOWN - 1)}…`
}
