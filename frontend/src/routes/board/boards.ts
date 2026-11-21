/**
 * A board per template.
 *
 * A project's templates each get a board of their own, with the template's
 * stages as its columns: a Bug board reads Triage › Fixing › Verify, a Feature
 * board Backlog › Design › Build › Review. Cards with no template go to
 * General, which keeps the project's own columns.
 *
 * None of this touches React. It is the same cards and columns the board
 * already holds, sorted into the board they belong to.
 */

import type { BoardColumn, Task, Template } from '../../api/client'
import { localToday } from '../../components/dates'

/** The key of the board for cards with no template. */
export const GENERAL_BOARD = 'general'

/** One board: a template's, or General's. */
export interface TemplateBoard {
  /** How the board is named in the address: `general`, or the template's name as a slug. */
  key: string
  /** The template behind it, or null for General. */
  template: Template | null
  name: string
  /** Its columns, in the project's own order. */
  columns: BoardColumn[]
}

/** A template's name as it goes in the address: `Design task` is `design-task`. */
export function boardSlug(name: string): string {
  return (
    name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-|-$/g, '') || 'board'
  )
}

/**
 * A template's columns: the ones its stages name, in the order the project
 * draws them. A template with no stages is unrestricted, so its board has
 * every column.
 */
export function columnsOfTemplate(template: Template, columns: BoardColumn[]): BoardColumn[] {
  if (!template.allowed_column_ids.length) return columns
  return columns.filter((column) => template.allowed_column_ids.includes(column.id))
}

/**
 * Every board, General first and then the templates in the order they came
 * (name order, from the API).
 *
 * A template whose slug is already taken by an earlier board — `Bug!` after
 * `Bug` — keeps a suffix, so every board can still be addressed.
 */
export function boardsFor(templates: Template[], columns: BoardColumn[]): TemplateBoard[] {
  const taken = new Set([GENERAL_BOARD])
  const boards: TemplateBoard[] = [{ key: GENERAL_BOARD, template: null, name: 'General', columns }]
  for (const template of templates) {
    const base = boardSlug(template.name)
    let key = base
    for (let n = 2; taken.has(key); n++) key = `${base}-${n}`
    taken.add(key)
    boards.push({
      key,
      template,
      name: template.name,
      columns: columnsOfTemplate(template, columns),
    })
  }
  return boards
}

/**
 * The cards on one board. A card whose template the board does not know of —
 * the list has not arrived, say — is shown on General rather than nowhere.
 */
export function cardsOnBoard(tasks: Task[], board: TemplateBoard, boards: TemplateBoard[]): Task[] {
  if (board.template) return tasks.filter((task) => task.template_id === board.template?.id)
  const known = new Set(boards.flatMap((one) => (one.template ? [one.template.id] : [])))
  return tasks.filter((task) => task.template_id === null || !known.has(task.template_id))
}

/** The board an address or a remembered choice names, or General when it names none. */
export function pickBoard(boards: TemplateBoard[], ...keys: (string | null | undefined)[]) {
  for (const key of keys) {
    const found = key ? boards.find((board) => board.key === key) : undefined
    if (found) return found
  }
  // General is always first.
  return boards[0] as TemplateBoard
}

/** What a board's name says about it in the dial, before anyone opens it. */
export interface BoardSummary {
  /** Cards in each of its columns, in column order — the workflow mark. */
  perColumn: number[]
  /** Cards not yet in its last column. */
  open: number
  /** Why it wants a look, or null: something open that is overdue or blocked. */
  attention: string | null
  /** Its columns' names, in order: `Triage › Fixing › Verify`. */
  flow: string
}

export function summariseBoard(
  board: TemplateBoard,
  cards: Task[],
  today: string = localToday(),
): BoardSummary {
  const last = board.columns.at(-1)
  const perColumn = board.columns.map(
    (column) => cards.filter((task) => task.column_id === column.id).length,
  )
  const open = cards.filter((task) => task.column_id !== last?.id && task.status !== 'cancelled')
  const blocked = open.filter((task) => task.status === 'blocked').length
  const overdue = open.filter(
    (task) => task.next_due_date !== null && task.next_due_date < today,
  ).length
  const said = [
    overdue ? `${overdue} overdue` : null,
    blocked ? `${blocked} blocked` : null,
  ].filter(Boolean)
  return {
    perColumn,
    open: open.length,
    attention: said.length ? said.join(', ') : null,
    flow: board.columns.map((column) => column.name).join(' › '),
  }
}

/** Where the last board opened on a project is remembered. */
export function rememberedBoardKey(projectKey: string): string {
  return `cylist.board.template.${projectKey}`
}
