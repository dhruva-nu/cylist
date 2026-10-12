/**
 * The docs' left-hand pane: the box to ask them a question, and both sections
 * with their topics and docs.
 */

import { useMutation } from '@tanstack/react-query'
import { useId, useState, type FormEvent } from 'react'
import {
  api,
  errorMessage,
  type DocSection,
  type DocTopicWithDocs,
  type DocTree,
} from '../../api/client'
import { Button } from '../../components/ui'
import { IconButton } from './IconButton'
import styles from './ProjectDocs.module.css'

export function DocNav({
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

/** An error said beside the control it came from, rather than across the page. */
function InlineError({ error }: { error: unknown }) {
  if (error === null || error === undefined) return null
  return (
    <p className={styles.inlineError} role="alert">
      {errorMessage(error)}
    </p>
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

  const shift = useMutation({
    mutationFn: async (step: -1 | 1) => {
      const order = siblings.map((sibling) => sibling.id)
      order.splice(index, 1)
      order.splice(index + step, 0, topic.id)
      await api.reorderDocTopics(projectKey, section, order)
      await onChanged()
      return step
    },
    onSuccess: (step) => announce(`Moved ${topic.name} ${step < 0 ? 'up' : 'down'}.`),
  })

  const remove = useMutation({
    mutationFn: async () => {
      await api.deleteDocTopic(topic.id)
      await onChanged()
    },
    onSuccess: () => announce(`Deleted the topic ${topic.name}.`),
  })

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
                onClick={() => shift.mutate(-1)}
              >
                ↑
              </IconButton>
              <IconButton
                label={`Move ${topic.name} down`}
                disabled={index === siblings.length - 1}
                onClick={() => shift.mutate(1)}
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
                <IconButton
                  label={`Delete ${topic.name}`}
                  onClick={() => {
                    if (window.confirm(`Delete the topic “${topic.name}”?`)) remove.mutate()
                  }}
                >
                  ×
                </IconButton>
              ) : null}
            </span>
          ) : null}
        </div>
      )}
      <InlineError error={shift.error ?? remove.error} />
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
  const save = useMutation({ mutationFn: onSubmit })

  function submit(event: FormEvent) {
    event.preventDefault()
    if (name.trim()) save.mutate(name.trim())
  }

  return (
    <form className={styles.nameForm} onSubmit={submit}>
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
        <Button small variant="go" type="submit" disabled={save.isPending || !name.trim()}>
          {submitLabel}
        </Button>
        <Button small variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
      <InlineError error={save.error} />
    </form>
  )
}

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
  const [lastAsked, setLastAsked] = useState(asked)
  const id = useId()

  // Caught up during render rather than in an effect, so the box never paints
  // the old question beside the new answer. Not by remounting on a key: the
  // box keeps its focus across the question it has just asked.
  if (asked !== lastAsked) {
    setLastAsked(asked)
    setQuestion(asked)
  }

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
