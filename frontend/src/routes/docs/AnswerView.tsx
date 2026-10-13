/**
 * What the docs said to a question: the section that answers it, how sure
 * they are, and what else was weighed on the way.
 */

import type { DocAnswer, DocAnswerStatus, DocSectionFound } from '../../api/client'
import { Markdown } from '../../components/Markdown'
import { Button, ErrorBanner } from '../../components/ui'
import styles from './ProjectDocs.module.css'

const STATUS_LABEL: Record<DocAnswerStatus, string> = {
  ok: 'Answered',
  ambiguous: 'Answered — a close call',
  unverified: 'Best guess only',
  not_documented: 'Not in the docs',
  unavailable: 'Could not ask',
}

const STATUS_CLASS: Record<DocAnswerStatus, string | undefined> = {
  ok: styles.statusOk,
  ambiguous: styles.statusOk,
  unverified: styles.statusHold,
  not_documented: styles.statusHold,
  unavailable: styles.statusBlocked,
}

/** Below this, a section jev read and set aside is not worth a line beside an
 * answer: it was weighed for its title and found to be about something else. */
const WORTH_A_LOOK = 0.3

function percent(probability: number | null): string | null {
  return probability === null ? null : `${Math.round(probability * 100)}% relevant`
}

/** `Engineering / APIs / Webhooks` and a section, as the steps of a breadcrumb. */
function crumbsOf(path: string, section: string | null): string[] {
  return section ? [...path.split(' / '), section] : path.split(' / ')
}

/** Where a section is filed, drawn the way the reader's breadcrumb is. */
function Where({ path, section }: { path: string; section: string | null }) {
  return (
    <p className={styles.filed}>
      {crumbsOf(path, section).map((part, index) => (
        <span key={index}>
          {index ? <span aria-hidden="true"> › </span> : null}
          {part}
        </span>
      ))}
    </p>
  )
}

/** Why no answer was shown, in the words the status calls for. */
function missExplained(answer: DocAnswer): string {
  if (answer.status === 'unavailable') {
    return `The docs could not be asked just now: ${answer.reason ?? 'jev did not answer'}.`
  }
  if (answer.status === 'unverified') {
    return (
      'No section passed the relevance check, so nothing here is sure to answer it. ' +
      'An agent that works it out from the code writes it into the docs.'
    )
  }
  return (
    'Nothing in the docs answers this yet. An agent that works it out from the ' +
    'code writes it into the docs, so the next time it is asked it is here.'
  )
}

export function AnswerView({
  question,
  answer,
  pending,
  error,
  onOpen,
}: {
  question: string
  answer: DocAnswer | undefined
  pending: boolean
  error: Error | null
  onOpen: (docId: string) => void
}) {
  if (pending) {
    return (
      <article className={styles.reader} aria-busy="true">
        <AnswerHead question={question} />
        <p className={styles.quiet}>
          Finding the section that answers it — a few seconds, while jev reads the likely ones.
        </p>
      </article>
    )
  }
  if (error || !answer) {
    return (
      <article className={styles.reader}>
        <AnswerHead question={question} />
        <ErrorBanner>{error?.message ?? 'The docs could not be asked.'}</ErrorBanner>
      </article>
    )
  }

  const answered = answer.status === 'ok' || answer.status === 'ambiguous'
  // Beside an answer, only the near misses. Without one, every section that
  // was weighed — the best guess among them, whose text is not shown, since a
  // section that failed the check drawn as a page reads as though it answered.
  const weighed = answered
    ? answer.alternatives.filter((other) => (other.relevance ?? 0) >= WORTH_A_LOOK)
    : [...(answer.found ? [{ ...answer.found, score: 1 }] : []), ...answer.alternatives]

  return (
    <article className={styles.reader}>
      <AnswerHead
        question={question}
        status={answer.status}
        relevance={answered ? percent(answer.found?.relevance ?? null) : null}
      />

      {answer.found && answered ? <Found found={answer.found} onOpen={onOpen} /> : null}

      {!answered ? <p className={styles.miss}>{missExplained(answer)}</p> : null}

      {answer.also ? (
        <Found found={answer.also} onOpen={onOpen} heading="The other side of it" />
      ) : null}

      {weighed.length ? (
        <section className={styles.weighed}>
          <h3 className={styles.subhead}>{answered ? 'Also close' : 'Sections it weighed'}</h3>
          <ul>
            {weighed.map((other) => (
              <li key={`${other.doc_id}#${other.section ?? ''}`}>
                <button
                  type="button"
                  className={styles.docLink}
                  onClick={() => onOpen(other.doc_id)}
                >
                  {crumbsOf(other.path, other.section).join(' › ')}
                </button>
                {percent(other.relevance) ? (
                  <span className={styles.weighedScore}>{percent(other.relevance)}</span>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {answer.reason && answer.status !== 'unavailable' ? (
        <details className={styles.howRouted}>
          <summary>How it was routed</summary>
          <p>{answer.reason}</p>
        </details>
      ) : null}
    </article>
  )
}

/** The question asked, and — once there is an answer — how sure the docs are of it. */
function AnswerHead({
  question,
  status,
  relevance,
}: {
  question: string
  status?: DocAnswerStatus
  relevance?: string | null
}) {
  return (
    <header className={styles.readerHead}>
      <div className={styles.readerTitle}>
        <p className={styles.filed}>Asked the docs</p>
        <h2>{question}</h2>
        {status ? (
          <p className={styles.byline}>
            <span className={[styles.status, STATUS_CLASS[status]].filter(Boolean).join(' ')}>
              {STATUS_LABEL[status]}
            </span>
            {relevance ? <span>{relevance}</span> : null}
          </p>
        ) : null}
      </div>
    </header>
  )
}

function Found({
  found,
  onOpen,
  heading,
}: {
  found: DocSectionFound
  onOpen: (docId: string) => void
  heading?: string
}) {
  return (
    <section className={styles.found}>
      {heading ? <h3 className={styles.subhead}>{heading}</h3> : null}
      <div className={styles.foundHead}>
        <Where path={found.path} section={found.whole_doc ? null : found.section} />
        <Button small variant="ghost" onClick={() => onOpen(found.doc_id)}>
          Open doc
        </Button>
      </div>
      {found.text.trim() ? (
        <Markdown source={found.text} />
      ) : (
        <p className={styles.quiet}>This section is empty.</p>
      )}
    </section>
  )
}
