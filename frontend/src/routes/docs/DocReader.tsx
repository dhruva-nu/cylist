/** One doc, drawn as a page, with the tools that file, reorder and delete it. */

import { useMutation } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'
import { api, type Doc, type DocTree } from '../../api/client'
import { formatStamp } from '../../components/format'
import { Markdown } from '../../components/Markdown'
import { Avatar, Button, ErrorNotice } from '../../components/ui'
import { IconButton } from './IconButton'
import styles from './ProjectDocs.module.css'
import { handlesOwnErrors } from '../../api/mutationErrors'
import { invalidates, affects } from '../../api/queries'

/**
 * Scroll to and mark the part of a doc an answer pointed into.
 *
 * The section is found by its text rather than an anchor: the markdown has
 * no ids. A heading of that title first — before an `## Index` entry that
 * starts with the same words — and otherwise the list item or paragraph a
 * section jev-docs made from it opens with, its title being those words.
 */
function markSection(body: HTMLElement, section: string, className: string) {
  const wanted = section.replace(/…$/, '').trim().toLowerCase()
  const textOf = (element: HTMLElement) => (element.textContent ?? '').trim().toLowerCase()
  const all = (selector: string) => Array.from(body.querySelectorAll<HTMLElement>(selector))
  const target =
    all('h1, h2, h3, h4').find((element) => textOf(element) === wanted) ??
    all('li, p').find((element) => textOf(element).startsWith(wanted))
  if (!target) return
  target.classList.add(className)
  target.scrollIntoView({ block: 'start' })
}

export function DocReader({
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
  const body = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (focusSection && body.current) {
      markSection(body.current, focusSection, styles.answered ?? '')
    }
  }, [focusSection, doc.body])

  /** Every change made from here goes through this, so they share one error line. */
  const change = useMutation({
    meta: { ...handlesOwnErrors, ...invalidates(...affects.docs) },
    mutationFn: (run: () => Promise<void>) => run(),
  })

  const topics = tree.sections.flatMap((part) => part.topics)
  const sectionLabel = tree.sections.find((part) => part.section === doc.section)?.label
  const siblings = topics.find((topic) => topic.id === doc.topic_id)?.docs ?? []
  const index = siblings.findIndex((sibling) => sibling.id === doc.id)

  function fileUnder(topicId: string) {
    const topic = topics.find((candidate) => candidate.id === topicId)
    change.mutate(async () => {
      await api.updateDoc(doc.id, { topic_id: topicId })
      await onChanged()
      announce(`Moved “${doc.title}” to ${topic?.name ?? 'another topic'}.`)
    })
  }

  function moveTo(position: number) {
    change.mutate(async () => {
      await api.updateDoc(doc.id, { position })
      await onChanged()
    })
  }

  function remove() {
    if (!window.confirm(`Delete “${doc.title}”? This cannot be undone.`)) return
    change.mutate(async () => {
      await api.deleteDoc(doc.id)
      await onDeleted()
    })
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
              <select value={doc.topic_id} onChange={(event) => fileUnder(event.target.value)}>
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
              onClick={() => moveTo(index - 1)}
            >
              ↑
            </IconButton>
            <IconButton
              label="Move down in its topic"
              disabled={index < 0 || index >= siblings.length - 1}
              onClick={() => moveTo(index + 1)}
            >
              ↓
            </IconButton>
            <Button small danger onClick={remove}>
              Delete
            </Button>
          </div>
        ) : null}
      </header>
      <ErrorNotice error={change.error} />
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
