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

import { useQuery } from '@tanstack/react-query'
import { useNavigate, useParams, useSearch } from '@tanstack/react-router'
import { useState } from 'react'
import { api, type Doc, type DocTopicWithDocs, type DocTree } from '../../api/client'
import { queryKeys, useInvalidate } from '../../api/queries'
import { PageHead } from '../../components/Shell'
import { EmptyState, ErrorBanner, LiveRegion, useAnnouncer } from '../../components/ui'
import { usePermissions } from '../usePermissions'
import { AnswerView } from './AnswerView'
import { DocEditor } from './DocEditor'
import { DocNav } from './DocNav'
import { DocReader } from './DocReader'
import styles from './ProjectDocs.module.css'

type Mode = { kind: 'read' } | { kind: 'edit'; doc: Doc } | { kind: 'new'; topic: DocTopicWithDocs }

const READING: Mode = { kind: 'read' }

export function ProjectDocs() {
  const { projectKey } = useParams({ from: '/p/$projectKey/docs' })
  const { doc: openId, ask } = useSearch({ from: '/p/$projectKey/docs' })
  const navigate = useNavigate({ from: '/p/$projectKey/docs' })
  const may = usePermissions(projectKey)
  const invalidate = useInvalidate()
  const { message, announce } = useAnnouncer()

  // Opening a different doc, or asking something, leaves whatever was being
  // edited or written. So a mode remembers the address it was entered at, and
  // lapses back to reading the moment the address is somewhere else — worked
  // out as the page renders, so the old editor is never drawn over the new
  // doc, not even for the one frame an effect would take to notice.
  const address = `${openId ?? ''}?${ask ?? ''}`
  const [entered, setEntered] = useState<{ mode: Mode; address: string }>({
    mode: READING,
    address,
  })
  const mode = entered.address === address ? entered.mode : READING
  const setMode = (next: Mode) => setEntered({ mode: next, address })

  const tree = useQuery({
    queryKey: queryKeys.docTree(projectKey),
    queryFn: ({ signal }) => api.getDocTree(projectKey, signal),
  })

  const open = useQuery({
    queryKey: queryKeys.doc(openId),
    queryFn: ({ signal }) => api.getDoc(openId ?? '', signal),
    enabled: Boolean(openId),
  })

  // Each question is about five jev requests, so an answer is kept rather than
  // asked again whenever the window regains focus; an edit to the docs is what
  // makes it stale, and `refresh` says so.
  const answer = useQuery({
    queryKey: queryKeys.docAnswer(projectKey, ask),
    queryFn: () => api.askDocs(projectKey, ask ?? ''),
    enabled: Boolean(ask),
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
    retry: false,
  })

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
    await invalidate(
      ['docs', projectKey],
      ['doc-answer', projectKey],
      ...(docId ? [['doc', docId]] : []),
    )
  }

  if (tree.isPending) return <EmptyState>Loading docs…</EmptyState>
  if (tree.error) return <ErrorBanner>{tree.error.message}</ErrorBanner>

  const docTree = tree.data
  const writable = may('docs')
  // The section the answer pointed into, when the open doc is the one it named.
  const answeredIn = [answer.data?.found, answer.data?.also].find(
    (found) => found && found.doc_id === openId,
  )

  function pane() {
    if (mode.kind === 'new') {
      return (
        <DocEditor
          heading={`New doc in ${mode.topic.name}`}
          initial={{ title: '', body: '' }}
          onCancel={() => setMode(READING)}
          onSave={async ({ title, body }) => {
            const created = await api.createDoc(mode.topic.id, title, body)
            await refresh()
            announce(`Filed “${created.title}” under ${mode.topic.name}.`)
            setMode(READING)
            show(created.id)
          }}
        />
      )
    }
    if (mode.kind === 'edit') {
      return (
        <DocEditor
          heading={`Editing ${mode.doc.title}`}
          initial={{ title: mode.doc.title, body: mode.doc.body }}
          onCancel={() => setMode(READING)}
          onSave={async ({ title, body }) => {
            await api.updateDoc(mode.doc.id, { title, body })
            await refresh(mode.doc.id)
            announce(`Saved “${title}”.`)
            setMode(READING)
          }}
        />
      )
    }
    if (!openId && ask) {
      return (
        <AnswerView
          key={ask}
          question={ask}
          answer={answer.data}
          pending={answer.isPending}
          error={answer.error}
          onOpen={showFromAnswer}
        />
      )
    }
    if (!openId) return <Overview tree={docTree} />
    if (open.isPending) return <EmptyState>Loading…</EmptyState>
    if (open.error) return <ErrorBanner>{open.error.message}</ErrorBanner>

    const doc = open.data
    return (
      <DocReader
        // A fresh reader per doc, so the next one opens at its top rather
        // than scrolled as far down as the last one was read.
        key={doc.id}
        doc={doc}
        tree={docTree}
        writable={writable}
        focusSection={answeredIn?.whole_doc ? null : (answeredIn?.section ?? null)}
        onBack={ask ? () => void navigate({ search: { ask } }) : undefined}
        onEdit={() => setMode({ kind: 'edit', doc })}
        onChanged={() => refresh(doc.id)}
        onDeleted={async () => {
          await refresh()
          announce(`Deleted “${doc.title}”.`)
          show(null)
        }}
        announce={announce}
      />
    )
  }

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
          tree={docTree}
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
          {pane()}
        </section>
      </div>
    </>
  )
}

/** The pane before a doc is opened or a question asked: what there is to open. */
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
  const topicCount = `${topics} topic${topics === 1 ? '' : 's'}`
  const docCount = `${tree.doc_count} doc${tree.doc_count === 1 ? '' : 's'}`
  return (
    <EmptyState>
      {tree.doc_count === 0
        ? `${topicCount}, no docs yet. Press + beside a topic to write the first.`
        : `${docCount} across ${topicCount}. Pick one to read it, or ask the docs a question.`}
    </EmptyState>
  )
}
