/** A board's columns, and the templates that say where a card may go on it. */

import { jsonBody, request } from './http'
import { fetchAllPages } from './paging'

export interface BoardColumn {
  id: string
  project_id: string
  name: string
  description: string
  position: number
  /**
   * The sections this column is divided into, left to right — "Done",
   * "Cancelled", "In prod". Only the board's last column may have any, and
   * empty is the ordinary case: a column that draws no distinction.
   */
  outcomes: string[]
  task_count: number
}

/** A board's columns plus the limits the server enforces on them. */
export interface Board {
  columns: BoardColumn[]
  min_columns: number
  max_columns: number
  /** How many sections the last column may be divided into. */
  max_outcomes: number
}

export interface ColumnInput {
  name: string
  description: string
  /** Sent only for the board's last column; anywhere else it must be empty. */
  outcomes?: string[]
}

/**
 * One column a template's cards may sit in, and the sub-stages a card passes
 * through there.
 *
 * A card created from the template — or arriving here by a later move — has
 * these labels loaded onto its own `sub_statuses`, the same click-through
 * progress bar every card carries, and cannot leave the column until it is on
 * the last one.
 */
export interface TemplateStage {
  column_id: string
  /** So a stage reads without the board's columns fetched beside it. */
  column_name: string
  /** The sub-stages a card passes through here, left to right. May be empty:
   * a column can be named without asking anything of the card there. */
  sub_stage_labels: string[]
  /** Which of this column's outcomes the template's cards may end on. Empty
   * means all of them, the same silence `sub_stage_labels` keeps. */
  allowed_outcomes: string[]
}

/**
 * A kind of card — "Hotfix", "Design task" — and the rule for where its
 * cards go.
 *
 * A template carries nothing onto the task it makes beyond that rule: no
 * type, no priority. `stages` is the whole of it, one entry per column the
 * template's cards may sit in.
 */
export interface Template {
  id: string
  project_id: string
  name: string
  description: string
  stages: TemplateStage[]
  /**
   * The stages' column ids, in the same order — a convenience for knowing
   * where a card may go without reading what it owes to get there. **Empty
   * means unrestricted**: this template has no stages yet, so its cards go
   * anywhere on the board.
   */
  allowed_column_ids: string[]
  /** How many cards were created from it. Above zero, it cannot be deleted. */
  task_count: number
  created_at: string
}

/**
 * A template as it is written.
 *
 * `stages` is all-or-nothing on an edit: sending it replaces the whole set,
 * because the stages together are the policy. Leave it out to rename alone.
 */
export interface TemplateInput {
  name: string
  description?: string
  stages?: { column_id: string; sub_stage_labels: string[]; allowed_outcomes: string[] }[]
}

export const boardApi = {
  listColumns: (projectKey: string) => request<Board>(`/projects/${projectKey}/columns`),
  createColumn: (projectKey: string, input: ColumnInput) =>
    request<BoardColumn>(`/projects/${projectKey}/columns`, {
      method: 'POST',
      body: jsonBody(input),
    }),
  updateColumn: (id: string, input: Partial<ColumnInput>) =>
    request<BoardColumn>(`/columns/${id}`, { method: 'PATCH', body: jsonBody(input) }),
  deleteColumn: (id: string) => request<{ ok: boolean }>(`/columns/${id}`, { method: 'DELETE' }),
  reorderColumns: (projectKey: string, columnIds: string[]) =>
    request<Board>(`/projects/${projectKey}/columns/order`, {
      method: 'PUT',
      body: jsonBody({ column_ids: columnIds }),
    }),

  listTemplates: (projectKey: string) =>
    fetchAllPages<Template>(`/projects/${projectKey}/templates`),
  createTemplate: (projectKey: string, input: TemplateInput) =>
    request<Template>(`/projects/${projectKey}/templates`, {
      method: 'POST',
      body: jsonBody(input),
    }),
  updateTemplate: (id: string, input: Partial<TemplateInput>) =>
    request<Template>(`/templates/${id}`, { method: 'PATCH', body: jsonBody(input) }),
  deleteTemplate: (id: string) =>
    request<{ ok: boolean }>(`/templates/${id}`, { method: 'DELETE' }),
}
