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
 * The sections are fixed and the topics are the project's own, one level deep.
 * Nothing here can nest a topic in a topic, because nothing on the server can.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams, useSearch } from '@tanstack/react-router'
import { useEffect, useState, type FormEvent, type KeyboardEvent } from 'react'
import { api, type Doc, type DocSection, type DocTopicWithDocs, type DocTree } from '../api/client'
import { formatStamp } from '../components/format'
import { Markdown } from '../components/Markdown'
import { PageHead } from '../components/Shell'
import { Avatar, Button, EmptyState, ErrorBanner, LiveRegion, useAnnouncer } from '../components/ui'
import styles from './ProjectDocs.module.css'
import { usePermissions } from './usePermissions'

type Mode = { kind: 'read' } | { kind: 'edit'; doc: Doc } | { kind: 'new'; topic: DocTopicWithDocs }

export function ProjectDocs() {
  const { projectKey } = useParams({ from: '/p/$projectKey/docs' })
  const { doc: openId } = useSearch({ from: '/p/$projectKey/docs' })
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

  // Opening a different doc leaves whatever was being edited or written.
  useEffect(() => setMode({ kind: 'read' }), [openId])

  function show(docId: string | null) {
    void navigate({ search: docId ? { doc: docId } : {} })
  }

  async function refresh(docId?: string) {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['docs', projectKey] }),
      queryClient.invalidateQueries({ queryKey: ['project-summary', projectKey] }),
      docId ? queryClient.invalidateQueries({ queryKey: ['doc', docId] }) : null,
    ])
  }

  if (tree.isPending) return <EmptyState>Loading docs…</EmptyState>
  if (tree.error) return <ErrorBanner>{tree.error.message}</ErrorBanner>

  const writable = may('docs')

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
          ) : !openId ? (
            <Overview tree={tree.data} />
          ) : open.isPending ? (
            <EmptyState>Loading…</EmptyState>
          ) : open.error ? (
            <ErrorBanner>{open.error.message}</ErrorBanner>
          ) : (
            <DocReader
              doc={open.data}
              tree={tree.data}
              writable={writable}
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
  onOpen,
  onNew,
  onChanged,
  announce,
}: {
  projectKey: string
  tree: DocTree
  openId: string | null
  writable: boolean
  onOpen: (docId: string) => void
  onNew: (topic: DocTopicWithDocs) => void
  onChanged: () => Promise<void>
  announce: (message: string) => void
}) {
  return (
    <nav className={styles.nav} aria-label="Docs by section and topic">
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
        : `${tree.doc_count} doc${tree.doc_count === 1 ? '' : 's'} across ${topics} topic${topics === 1 ? '' : 's'}. Pick one to read it.`}
    </EmptyState>
  )
}

function DocReader({
  doc,
  tree,
  writable,
  onEdit,
  onChanged,
  onDeleted,
  announce,
}: {
  doc: Doc
  tree: DocTree
  writable: boolean
  onEdit: () => void
  onChanged: () => Promise<void>
  onDeleted: () => Promise<void>
  announce: (message: string) => void
}) {
  const [error, setError] = useState<string | null>(null)
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
        <Markdown source={doc.body} />
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
