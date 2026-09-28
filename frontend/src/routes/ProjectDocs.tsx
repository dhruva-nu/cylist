/**
 * A project's docs — markdown, filed as section → topic → doc.
 *
 * Two panes, because the two things done here are finding a doc and reading
 * it, and a reader who has to go back to a list between every doc is a reader
 * who stops at the first one. The tree down the left is both sections, always
 * — an empty section is still a heading somebody can add a topic under — and
 * the pane beside it is the doc, drawn as a page, or the editor.
 *
 * The open doc is in the address (`?doc=`), so a doc can be linked to from a
 * card or a chat and open on the doc rather than on the tree it is somewhere
 * in. What is *being edited* is not: a link somebody pastes should open the
 * doc, not their half-finished edit of it.
 *
 * Above the tree, the docs can be asked a question — the same `ask_docs` the
 * agents use before they read the code. jev-docs routes it to the one section
 * that answers it and reads that section back against the question; the pane
 * shows the section, how sure it is, and the doc it came from. The question
 * is in the address too (`?ask=`), so an answer can be passed on as a link.
 *
 * The sections are fixed and the topics are the project's own, one level deep.
 * Nothing here can nest a topic in a topic, because nothing on the server can.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams, useSearch } from '@tanstack/react-router'
import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import {
  api,
  type Doc,
  type DocAnswer,
  type DocAnswerStatus,
  type DocSection,
  type DocSectionFound,
  type DocTopicWithDocs,
  type DocTree,
} from '../api/client'
import { formatStamp } from '../components/format'
import { Markdown } from '../components/Markdown'
import { PageHead } from '../components/Shell'
import { Avatar, Button, EmptyState, ErrorBanner, LiveRegion, useAnnouncer } from '../components/ui'
import styles from './ProjectDocs.module.css'
import { usePermissions } from './usePermissions'

type Mode = { kind: 'read' } | { kind: 'edit'; doc: Doc } | { kind: 'new'; topic: DocTopicWithDocs }

export function ProjectDocs() {
  const { projectKey } = useParams({ from: '/p/$projectKey/docs' })
  const { doc: openId, ask } = useSearch({ from: '/p/$projectKey/docs' })
  const navigate = useNavigate({ from: '/p/$projectKey/docs' })
  const may = usePermissions(projectKey)
  const queryClient = useQueryClient()
  const { message, announce } = useAnnouncer()
  const [mode, setMode] = useState<Mode>({ kind: 'read' })

  const tree = useQuery({
    queryKey: ['docs', projectKey],
    queryFn: () => api.getDocTree(projectKey),
  })

  const open = useQuery({
    queryKey: ['doc', openId],
    queryFn: () => api.getDoc(openId ?? ''),
    enabled: Boolean(openId),
  })

  // Each question is about five jev requests, so an answer is kept rather than
  // asked again whenever the window regains focus; an edit to the docs is what
  // makes it stale, and `refresh` says so.
  const answer = useQuery({
    queryKey: ['doc-answer', projectKey, ask],
    queryFn: () => api.askDocs(projectKey, ask ?? ''),
    enabled: Boolean(ask),
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
    retry: false,
  })

  // Opening a different doc, or asking something, leaves whatever was being
  // edited or written.
  useEffect(() => setMode({ kind: 'read' }), [openId, ask])

  /** Open a doc from the tree: the question, if there was one, is done with. */
  function show(docId: string | null) {
    void navigate({ search: docId ? { doc: docId } : {} })
  }

  /** Open a doc from an answer, keeping the question to come back to. */
  function showFromAnswer(docId: string) {
    void navigate({ search: ask ? { doc: docId, ask } : { doc: docId } })
  }

  function askTheDocs(question: string) {
    void navigate({ search: { ask: question } })
  }

  async function refresh(docId?: string) {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['docs', projectKey] }),
      queryClient.invalidateQueries({ queryKey: ['project-summary', projectKey] }),
      docId ? queryClient.invalidateQueries({ queryKey: ['doc', docId] }) : null,
      queryClient.invalidateQueries({ queryKey: ['doc-answer', projectKey] }),
    ])
  }

  if (tree.isPending) return <EmptyState>Loading docs…</EmptyState>
  if (tree.error) return <ErrorBanner>{tree.error.message}</ErrorBanner>

  const writable = may('docs')
  // The section the answer pointed into, when the open doc is the one it named.
  const answeredIn = [answer.data?.found, answer.data?.also].find(
    (found) => found && found.doc_id === openId,
  )

  return (
    <>
      <PageHead title="Docs">
        What this project knows about itself, in markdown — most of it written by the agents that
        work on it. Filed under Product or Engineering, then by topic.
      </PageHead>

      <LiveRegion message={message} />

      <div className={styles.layout}>
        <DocNav
          projectKey={projectKey}
          tree={tree.data}
          openId={openId ?? null}
          writable={writable}
          ask={ask ?? ''}
          asking={answer.isFetching}
          onAsk={askTheDocs}
          onOpen={show}
          onNew={(topic) => setMode({ kind: 'new', topic })}
          onChanged={() => refresh()}
          announce={announce}
        />

        <section className={styles.pane} aria-label="Doc">
          {mode.kind === 'new' ? (
            <DocEditor
              heading={`New doc in ${mode.topic.name}`}
              initial={{ title: '', body: '' }}
              onCancel={() => setMode({ kind: 'read' })}
              onSave={async ({ title, body }) => {
                const created = await api.createDoc(mode.topic.id, title, body)
                await refresh()
                announce(`Filed “${created.title}” under ${mode.topic.name}.`)
                setMode({ kind: 'read' })
                show(created.id)
              }}
            />
          ) : mode.kind === 'edit' ? (
            <DocEditor
              heading={`Editing ${mode.doc.title}`}
              initial={{ title: mode.doc.title, body: mode.doc.body }}
              onCancel={() => setMode({ kind: 'read' })}
              onSave={async ({ title, body }) => {
                await api.updateDoc(mode.doc.id, { title, body })
                await refresh(mode.doc.id)
                announce(`Saved “${title}”.`)
                setMode({ kind: 'read' })
              }}
            />
          ) : !openId && ask ? (
            <AnswerView
              key={ask}
              question={ask}
              answer={answer.data}
              pending={answer.isPending}
              error={answer.error}
              onOpen={showFromAnswer}
            />
          ) : !openId ? (
            <Overview tree={tree.data} />
          ) : open.isPending ? (
            <EmptyState>Loading…</EmptyState>
          ) : open.error ? (
            <ErrorBanner>{open.error.message}</ErrorBanner>
          ) : (
            <DocReader
              // A fresh reader per doc, so the next one opens at its top rather
              // than scrolled as far down as the last one was read.
              key={open.data.id}
              doc={open.data}
              tree={tree.data}
              writable={writable}
              focusSection={answeredIn?.whole_doc ? null : (answeredIn?.section ?? null)}
              onBack={ask ? () => void navigate({ search: { ask } }) : undefined}
              onEdit={() => setMode({ kind: 'edit', doc: open.data })}
              onChanged={() => refresh(open.data.id)}
              onDeleted={async () => {
                await refresh()
                announce(`Deleted “${open.data.title}”.`)
                show(null)
              }}
              announce={announce}
            />
          )}
        </section>
      </div>
    </>
  )
}

// --- The tree ------------------------------------------------------------------

function DocNav({
  projectKey,
  tree,
  openId,
  writable,
  ask,
  asking,
  onAsk,
  onOpen,
  onNew,
  onChanged,
  announce,
}: {
  projectKey: string
  tree: DocTree
  openId: string | null
  writable: boolean
  ask: string
  asking: boolean
  onAsk: (question: string) => void
  onOpen: (docId: string) => void
  onNew: (topic: DocTopicWithDocs) => void
  onChanged: () => Promise<void>
  announce: (message: string) => void
}) {
  return (
    <nav className={styles.nav} aria-label="Docs by section and topic">
      {tree.doc_count > 0 ? <AskBox asked={ask} asking={asking} onAsk={onAsk} /> : null}
      {tree.sections.map((part) => (
        <div key={part.section} className={styles.section}>
          <h2 className={styles.sectionHead}>{part.label}</h2>
          {part.topics.length === 0 ? (
            <p className={styles.quiet}>No topics yet.</p>
          ) : (
            <ul className={styles.topics}>
              {part.topics.map((topic, index) => (
                <TopicItem
                  key={topic.id}
                  projectKey={projectKey}
                  section={part.section}
                  siblings={part.topics}
                  index={index}
                  topic={topic}
                  openId={openId}
                  writable={writable}
                  onOpen={onOpen}
                  onNew={onNew}
                  onChanged={onChanged}
                  announce={announce}
                />
              ))}
            </ul>
          )}
          {writable ? (
            <AddTopic
              projectKey={projectKey}
              section={part.section}
              label={part.label}
              onAdded={async (name) => {
                await onChanged()
                announce(`Added the topic ${name} under ${part.label}.`)
              }}
            />
          ) : null}
        </div>
      ))}
    </nav>
  )
}

function TopicItem({
  projectKey,
  section,
  siblings,
  index,
  topic,
  openId,
  writable,
  onOpen,
  onNew,
  onChanged,
  announce,
}: {
  projectKey: string
  section: DocSection
  siblings: DocTopicWithDocs[]
  index: number
  topic: DocTopicWithDocs
  openId: string | null
  writable: boolean
  onOpen: (docId: string) => void
  onNew: (topic: DocTopicWithDocs) => void
  onChanged: () => Promise<void>
  announce: (message: string) => void
}) {
  const [renaming, setRenaming] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function shift(step: -1 | 1) {
    const order = siblings.map((sibling) => sibling.id)
    order.splice(index, 1)
    order.splice(index + step, 0, topic.id)
    try {
      await api.reorderDocTopics(projectKey, section, order)
      await onChanged()
      announce(`Moved ${topic.name} ${step < 0 ? 'up' : 'down'}.`)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not move the topic.')
    }
  }

  async function remove() {
    if (!window.confirm(`Delete the topic “${topic.name}”?`)) return
    try {
      await api.deleteDocTopic(topic.id)
      await onChanged()
      announce(`Deleted the topic ${topic.name}.`)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not delete the topic.')
    }
  }

  return (
    <li className={styles.topic}>
      {renaming ? (
        <NameForm
          label={`Rename ${topic.name}`}
          initial={topic.name}
          submitLabel="Rename"
          onCancel={() => setRenaming(false)}
          onSubmit={async (name) => {
            await api.renameDocTopic(topic.id, name)
            await onChanged()
            announce(`Renamed ${topic.name} to ${name}.`)
            setRenaming(false)
          }}
        />
      ) : (
        <div className={styles.topicHead}>
          <span className={styles.topicName}>{topic.name}</span>
          <span className={styles.count} aria-label={`${topic.doc_count} docs`}>
            {topic.doc_count}
          </span>
          {writable ? (
            <span className={styles.topicTools}>
              <IconButton label={`New doc in ${topic.name}`} onClick={() => onNew(topic)}>
                +
              </IconButton>
              <IconButton
                label={`Move ${topic.name} up`}
                disabled={index === 0}
                onClick={() => void shift(-1)}
              >
                ↑
              </IconButton>
              <IconButton
                label={`Move ${topic.name} down`}
                disabled={index === siblings.length - 1}
                onClick={() => void shift(1)}
              >
                ↓
              </IconButton>
              <IconButton label={`Rename ${topic.name}`} onClick={() => setRenaming(true)}>
                ✎
              </IconButton>
              {/* Only offered while empty: the server refuses to delete a topic
                  with docs in it, and a button that always refuses is worse
                  than one that is not there. */}
              {topic.doc_count === 0 ? (
                <IconButton label={`Delete ${topic.name}`} onClick={() => void remove()}>
                  ×
                </IconButton>
              ) : null}
            </span>
          ) : null}
        </div>
      )}
      {error ? (
        <p className={styles.inlineError} role="alert">
          {error}
        </p>
      ) : null}
      {topic.docs.length ? (
        <ul className={styles.docs}>
          {topic.docs.map((doc) => (
            <li key={doc.id}>
              <button
                type="button"
                className={[styles.docLink, doc.id === openId && styles.docOn]
                  .filter(Boolean)
                  .join(' ')}
                aria-current={doc.id === openId ? 'page' : undefined}
                onClick={() => onOpen(doc.id)}
              >
                {doc.title}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  )
}

function AddTopic({
  projectKey,
  section,
  label,
  onAdded,
}: {
  projectKey: string
  section: DocSection
  label: string
  onAdded: (name: string) => Promise<void>
}) {
  const [adding, setAdding] = useState(false)

  if (!adding) {
    return (
      <button type="button" className={styles.addTopic} onClick={() => setAdding(true)}>
        + Topic
      </button>
    )
  }

  return (
    <NameForm
      label={`New topic under ${label}`}
      initial=""
      submitLabel="Add"
      onCancel={() => setAdding(false)}
      onSubmit={async (name) => {
        await api.createDocTopic(projectKey, section, name)
        await onAdded(name)
        setAdding(false)
      }}
    />
  )
}

/** One line of text and a button — adding a topic and renaming one. */
function NameForm({
  label,
  initial,
  submitLabel,
  onSubmit,
  onCancel,
}: {
  label: string
  initial: string
  submitLabel: string
  onSubmit: (name: string) => Promise<void>
  onCancel: () => void
}) {
  const [name, setName] = useState(initial)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!name.trim()) return
    setBusy(true)
    setError(null)
    try {
      await onSubmit(name.trim())
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'That did not save.')
      setBusy(false)
    }
  }

  return (
    <form className={styles.nameForm} onSubmit={(event) => void submit(event)}>
      <input
        aria-label={label}
        placeholder={label}
        value={name}
        maxLength={80}
        autoFocus
        onChange={(event) => setName(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') onCancel()
        }}
      />
      <div className={styles.nameFormButtons}>
        <Button small variant="go" type="submit" disabled={busy || !name.trim()}>
          {submitLabel}
        </Button>
        <Button small variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
      {error ? (
        <p className={styles.inlineError} role="alert">
          {error}
        </p>
      ) : null}
    </form>
  )
}

function IconButton({
  label,
  onClick,
  disabled = false,
  children,
}: {
  label: string
  onClick: () => void
  disabled?: boolean
  children: string
}) {
  return (
    <button
      type="button"
      className={styles.iconButton}
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
    >
      <span aria-hidden="true">{children}</span>
    </button>
  )
}

// --- Asking the docs -------------------------------------------------------------

/**
 * One line to ask the docs a question in. Enter asks; the answer is drawn in
 * the pane. Kept to what was last asked when the address changes under it —
 * back, or a link — so the box always says what the pane is answering.
 */
function AskBox({
  asked,
  asking,
  onAsk,
}: {
  asked: string
  asking: boolean
  onAsk: (question: string) => void
}) {
  const [question, setQuestion] = useState(asked)
  const id = useId()

  useEffect(() => setQuestion(asked), [asked])

  function submit(event: FormEvent) {
    event.preventDefault()
    const trimmed = question.trim()
    if (trimmed) onAsk(trimmed)
  }

  return (
    <form role="search" className={styles.ask} onSubmit={submit}>
      <label className={styles.askLabel} htmlFor={id}>
        Ask the docs
      </label>
      <div className={styles.askRow}>
        <input
          id={id}
          type="search"
          className={styles.askInput}
          placeholder="Where is a sub-task’s move refused?"
          value={question}
          maxLength={1000}
          onChange={(event) => setQuestion(event.target.value)}
        />
        <Button small variant="go" type="submit" disabled={asking || !question.trim()}>
          {asking ? 'Asking…' : 'Ask'}
        </Button>
      </div>
    </form>
  )
}

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

function percent(probability: number | null): string | null {
  return probability === null ? null : `${Math.round(probability * 100)}% relevant`
}

/** Below this, a section jev read and set aside is not worth a line beside an
 * answer: it was weighed for its title and found to be about something else. */
const WORTH_A_LOOK = 0.3

/** `Engineering / APIs / Webhooks` and a section, drawn the way the reader's
 * breadcrumb is. */
function Where({ path, section }: { path: string; section: string | null }) {
  const parts = [...path.split(' / '), ...(section ? [section] : [])]
  return (
    <p className={styles.filed}>
      {parts.map((part, index) => (
        <span key={index}>
          {index ? <span aria-hidden="true"> › </span> : null}
          {part}
        </span>
      ))}
    </p>
  )
}

function AnswerView({
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
  const head = (status?: DocAnswerStatus, relevance?: string | null) => (
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

  if (pending) {
    return (
      <article className={styles.reader} aria-busy="true">
        {head()}
        <p className={styles.quiet}>
          Finding the section that answers it — a few seconds, while jev reads the likely ones.
        </p>
      </article>
    )
  }
  if (error || !answer) {
    return (
      <article className={styles.reader}>
        {head()}
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
      {head(answer.status, answered ? percent(answer.found?.relevance ?? null) : null)}

      {answer.found && answered ? <Found found={answer.found} onOpen={onOpen} /> : null}

      {!answered ? (
        <p className={styles.miss}>
          {answer.status === 'unavailable'
            ? `The docs could not be asked just now: ${answer.reason ?? 'jev did not answer'}.`
            : answer.status === 'unverified'
              ? 'No section passed the relevance check, so nothing here is sure to answer it. ' +
                'An agent that works it out from the code writes it into the docs.'
              : 'Nothing in the docs answers this yet. An agent that works it out from the ' +
                'code writes it into the docs, so the next time it is asked it is here.'}
        </p>
      ) : null}

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
                  {[...other.path.split(' / '), ...(other.section ? [other.section] : [])].join(
                    ' › ',
                  )}
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

// --- The pane --------------------------------------------------------------------

function Overview({ tree }: { tree: DocTree }) {
  const topics = tree.sections.reduce((total, part) => total + part.topics.length, 0)
  if (topics === 0) {
    return (
      <EmptyState>
        No topics yet. Add one under Product or Engineering — “Goals”, “APIs”, “DB schema” — and
        docs are filed under it.
      </EmptyState>
    )
  }
  return (
    <EmptyState>
      {tree.doc_count === 0
        ? `${topics} topic${topics === 1 ? '' : 's'}, no docs yet. Press + beside a topic to write the first.`
        : `${tree.doc_count} doc${tree.doc_count === 1 ? '' : 's'} across ${topics} topic${topics === 1 ? '' : 's'}. Pick one to read it, or ask the docs a question.`}
    </EmptyState>
  )
}

function DocReader({
  doc,
  tree,
  writable,
  focusSection,
  onBack,
  onEdit,
  onChanged,
  onDeleted,
  announce,
}: {
  doc: Doc
  tree: DocTree
  writable: boolean
  /** The section an answer pointed into, scrolled to and marked when the doc opens. */
  focusSection: string | null
  /** Back to the answer this doc was opened from. */
  onBack: (() => void) | undefined
  onEdit: () => void
  onChanged: () => Promise<void>
  onDeleted: () => Promise<void>
  announce: (message: string) => void
}) {
  const [error, setError] = useState<string | null>(null)
  const body = useRef<HTMLDivElement>(null)

  // The section is found by its text rather than an anchor: the markdown has
  // no ids. A heading of that title first — before an `## Index` entry that
  // starts with the same words — and otherwise the list item or paragraph a
  // section jev-docs made from it opens with, its title being those words.
  useEffect(() => {
    if (!focusSection || !body.current) return
    const wanted = focusSection.replace(/…$/, '').trim().toLowerCase()
    const text = (element: HTMLElement) => (element.textContent ?? '').trim().toLowerCase()
    const all = (selector: string) =>
      Array.from(body.current?.querySelectorAll<HTMLElement>(selector) ?? [])
    const target =
      all('h1, h2, h3, h4').find((element) => text(element) === wanted) ??
      all('li, p').find((element) => text(element).startsWith(wanted))
    if (!target) return
    target.classList.add(styles.answered ?? '')
    target.scrollIntoView({ block: 'start' })
  }, [focusSection, doc.body])
  const sectionLabel = tree.sections.find((part) => part.section === doc.section)?.label
  const siblings =
    tree.sections.flatMap((part) => part.topics).find((topic) => topic.id === doc.topic_id)?.docs ??
    []
  const index = siblings.findIndex((sibling) => sibling.id === doc.id)

  async function attempt(action: () => Promise<void>) {
    setError(null)
    try {
      await action()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'That did not work.')
    }
  }

  return (
    <article className={styles.reader}>
      {onBack ? (
        <button type="button" className={styles.back} onClick={onBack}>
          ← Back to the answer
        </button>
      ) : null}
      <header className={styles.readerHead}>
        <div className={styles.readerTitle}>
          <p className={styles.filed}>
            {sectionLabel} <span aria-hidden="true">›</span> {doc.topic_name}
          </p>
          <h2>{doc.title}</h2>
          <p className={styles.byline}>
            {doc.author ? (
              <>
                <Avatar name={doc.author.name} colour={doc.author.colour} small />
                <span>{doc.author.name}</span>
                <span aria-hidden="true">·</span>
              </>
            ) : null}
            <span>Updated {formatStamp(doc.updated_at)}</span>
          </p>
        </div>
        {writable ? (
          <div className={styles.readerTools}>
            <Button small variant="go" onClick={onEdit}>
              Edit
            </Button>
            <label className={styles.moveTo}>
              <span className={styles.visuallyHidden}>Filed under</span>
              <select
                value={doc.topic_id}
                onChange={(event) => {
                  const topicId = event.target.value
                  const topic = tree.sections
                    .flatMap((part) => part.topics)
                    .find((candidate) => candidate.id === topicId)
                  void attempt(async () => {
                    await api.updateDoc(doc.id, { topic_id: topicId })
                    await onChanged()
                    announce(`Moved “${doc.title}” to ${topic?.name ?? 'another topic'}.`)
                  })
                }}
              >
                {tree.sections.map((part) => (
                  <optgroup key={part.section} label={part.label}>
                    {part.topics.map((topic) => (
                      <option key={topic.id} value={topic.id}>
                        {topic.name}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </label>
            <IconButton
              label="Move up in its topic"
              disabled={index <= 0}
              onClick={() =>
                void attempt(async () => {
                  await api.updateDoc(doc.id, { position: index - 1 })
                  await onChanged()
                })
              }
            >
              ↑
            </IconButton>
            <IconButton
              label="Move down in its topic"
              disabled={index < 0 || index >= siblings.length - 1}
              onClick={() =>
                void attempt(async () => {
                  await api.updateDoc(doc.id, { position: index + 1 })
                  await onChanged()
                })
              }
            >
              ↓
            </IconButton>
            <Button
              small
              danger
              onClick={() => {
                if (!window.confirm(`Delete “${doc.title}”? This cannot be undone.`)) return
                void attempt(async () => {
                  await api.deleteDoc(doc.id)
                  await onDeleted()
                })
              }}
            >
              Delete
            </Button>
          </div>
        ) : null}
      </header>
      {error ? <ErrorBanner>{error}</ErrorBanner> : null}
      {doc.body.trim() ? (
        <div ref={body}>
          <Markdown source={doc.body} />
        </div>
      ) : (
        <p className={styles.quiet}>This doc is empty.</p>
      )}
    </article>
  )
}

/**
 * Title and markdown, with the page it will be beside it.
 *
 * Side by side on a wide screen, so what is being written and how it will
 * read are one glance apart; stacked on a narrow one, where "Preview" is a
 * toggle instead. ⌘/Ctrl+S saves, because a textarea this size is where
 * people reach for it without thinking.
 */
function DocEditor({
  heading,
  initial,
  onSave,
  onCancel,
}: {
  heading: string
  initial: { title: string; body: string }
  onSave: (doc: { title: string; body: string }) => Promise<void>
  onCancel: () => void
}) {
  const [title, setTitle] = useState(initial.title)
  const [body, setBody] = useState(initial.body)
  const [preview, setPreview] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function save(event?: FormEvent) {
    event?.preventDefault()
    if (!title.trim() || busy) return
    setBusy(true)
    setError(null)
    try {
      await onSave({ title: title.trim(), body })
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'That did not save.')
      setBusy(false)
    }
  }

  function onKeyDown(event: KeyboardEvent) {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
      event.preventDefault()
      void save()
    }
  }

  const dirty = title !== initial.title || body !== initial.body

  return (
    <form className={styles.editor} onSubmit={(event) => void save(event)} onKeyDown={onKeyDown}>
      <div className={styles.editorHead}>
        <h2 className={styles.editorHeading}>{heading}</h2>
        <div className={styles.readerTools}>
          <button
            type="button"
            className={styles.previewToggle}
            aria-pressed={preview}
            onClick={() => setPreview((shown) => !shown)}
          >
            Preview
          </button>
          <Button
            small
            variant="ghost"
            onClick={() => {
              if (dirty && !window.confirm('Discard your changes?')) return
              onCancel()
            }}
          >
            Cancel
          </Button>
          <Button small variant="go" type="submit" disabled={busy || !title.trim()}>
            {busy ? 'Saving…' : 'Save'}
          </Button>
        </div>
      </div>
      {error ? <ErrorBanner>{error}</ErrorBanner> : null}
      <input
        className={styles.titleInput}
        aria-label="Title"
        placeholder="Title"
        value={title}
        maxLength={200}
        autoFocus={!initial.title}
        onChange={(event) => setTitle(event.target.value)}
      />
      <div className={[styles.split, preview && styles.previewing].filter(Boolean).join(' ')}>
        <textarea
          className={styles.bodyInput}
          aria-label="Markdown"
          placeholder={
            '# Heading\n\nWrite in markdown — tables, task lists and code blocks all work.'
          }
          value={body}
          autoFocus={Boolean(initial.title)}
          onChange={(event) => setBody(event.target.value)}
        />
        <div className={styles.preview} aria-label="Preview">
          {body.trim() ? <Markdown source={body} /> : <p className={styles.quiet}>Nothing yet.</p>}
        </div>
      </div>
    </form>
  )
}
