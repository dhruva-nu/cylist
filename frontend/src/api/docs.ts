/** A project's docs, and asking them a question. */

import { jsonBody, request } from './http'
import type { Person } from './people'

/** One of the two headings every project's docs are filed under. Fixed. */
export type DocSection = 'product' | 'engineering'

/** A doc as the tree lists it — everything but its markdown. */
export interface DocListing {
  id: string
  topic_id: string
  title: string
  position: number
  /** Who wrote it first, most often an agent. Null when nobody in the directory did. */
  author: Person | null
  created_at: string
  updated_at: string
}

/** One doc, its markdown included, and where it is filed. */
export interface Doc extends DocListing {
  project_id: string
  section: DocSection
  topic_name: string
  body: string
}

export interface DocTopic {
  id: string
  project_id: string
  section: DocSection
  name: string
  position: number
  doc_count: number
}

export interface DocTopicWithDocs extends DocTopic {
  docs: DocListing[]
}

/**
 * A project's docs as the Docs page draws them: both sections, always, in the
 * same order, each with its topics and their docs. No bodies.
 */
export interface DocTree {
  sections: { section: DocSection; label: string; topics: DocTopicWithDocs[] }[]
  doc_count: number
}

/**
 * How sure the docs are of an answer. `ok` passed jev's relevance check;
 * `ambiguous` passed it on a close route; `unverified` is only the best guess;
 * `not_documented` is nothing written on it; `unavailable` is jev not asked.
 */
export type DocAnswerStatus = 'ok' | 'ambiguous' | 'unverified' | 'not_documented' | 'unavailable'

/** One section of one doc, as a question was routed to it. */
export interface DocSectionFound {
  doc_id: string
  /** `Engineering / APIs / Webhooks`. */
  path: string
  /** The section's title; null when the answer is the whole doc. */
  section: string | null
  text: string
  whole_doc: boolean
  /** jev's probability, having read the section, that it answers the question. */
  relevance: number | null
}

/** Where the docs answer a question — `POST /projects/{key}/docs/ask`. */
export interface DocAnswer {
  question: string
  status: DocAnswerStatus
  /** The relevance an answer has to reach to be `ok`. */
  threshold: number
  found: DocSectionFound | null
  /** When the question asks both why and how: the other side's section. */
  also: DocSectionFound | null
  alternatives: {
    doc_id: string
    path: string
    section: string | null
    score: number
    relevance: number | null
  }[]
  reason: string | null
}

export interface DocUpdate {
  title?: string
  body?: string
  /** Move the doc to this topic, on the same project. */
  topic_id?: string
  /** Where in its topic, from the top. The bottom when a move leaves it out. */
  position?: number
}

export const docsApi = {
  getDocTree: (projectKey: string) => request<DocTree>(`/projects/${projectKey}/docs`),
  createDocTopic: (projectKey: string, section: DocSection, name: string) =>
    request<DocTopic>(`/projects/${projectKey}/doc-topics`, {
      method: 'POST',
      body: jsonBody({ section, name }),
    }),
  renameDocTopic: (topicId: string, name: string) =>
    request<DocTopic>(`/doc-topics/${topicId}`, { method: 'PATCH', body: jsonBody({ name }) }),
  deleteDocTopic: (topicId: string) =>
    request<{ ok: boolean }>(`/doc-topics/${topicId}`, { method: 'DELETE' }),
  reorderDocTopics: (projectKey: string, section: DocSection, topicIds: string[]) =>
    request<DocTopic[]>(`/projects/${projectKey}/doc-topics/order`, {
      method: 'PUT',
      body: jsonBody({ section, topic_ids: topicIds }),
    }),
  getDoc: (docId: string) => request<Doc>(`/docs/${docId}`),
  askDocs: (projectKey: string, question: string) =>
    request<DocAnswer>(`/projects/${projectKey}/docs/ask`, {
      method: 'POST',
      body: jsonBody({ question }),
    }),
  createDoc: (topicId: string, title: string, body: string) =>
    request<Doc>(`/doc-topics/${topicId}/docs`, {
      method: 'POST',
      body: jsonBody({ title, body }),
    }),
  updateDoc: (docId: string, input: DocUpdate) =>
    request<Doc>(`/docs/${docId}`, { method: 'PATCH', body: jsonBody(input) }),
  deleteDoc: (docId: string) => request<{ ok: boolean }>(`/docs/${docId}`, { method: 'DELETE' }),
}
