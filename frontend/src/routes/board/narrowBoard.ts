/**
 * Which cards the board draws, and where: the filters, the hides, the columns
 * and the lanes. None of it touches React — it is the board's arithmetic,
 * worked out afresh from the cards it already holds on every render.
 */

import type { BoardColumn, Goal, Person, Task, Template } from '../../api/client'
import { agentCounts, passesAgentFilter, type AgentFilter } from '../agentState'
import { hidesCancelled, isCancelled, type StatusChoice } from '../boardHiding'
import { filterTasks, parseQuery, tokenize } from '../boardSearch'
import { cardsOwned, ownedByAny } from './boardPeople'
import { laneKeyOfTask, NO_GOAL_LANE } from './dropTargets'

/** The goal filter's choices: every card, the cards on no goal, or one goal's id. */
export type GoalChoice = 'all' | 'none' | (string & {})

/** What the reader has asked the board to narrow itself to. */
export interface BoardFilters {
  search: string
  goal: GoalChoice
  status: StatusChoice
  agent: AgentFilter
  /** Whose cards to show, by person id (CYLIST-99). Empty is everyone's. */
  people: string[]
  showCancelled: boolean
}

/**
 * The cards the board draws, once the search box, the quick filters and the
 * cancelled hide have each had their say — and how many that hide is holding
 * back.
 *
 * The cards on hold are not filtered here: they are held back at the foot of
 * each column, which is a decision each column makes for itself — see
 * `boardHiding.ts`. What this does hand on for them is `parsed`, the half of
 * that decision the whole board shares.
 *
 * Client-side, over what's already fetched: the board holds every task in
 * memory regardless, and a search endpoint would be a second way to ask a
 * question this data already answers.
 */
export function narrowBoard(
  tasks: Task[],
  { search, goal, status, agent, people, showCancelled }: BoardFilters,
  columns: BoardColumn[],
  members: Person[],
) {
  const parsed = parseQuery(tokenize(search))
  const narrowed = filterTasks(tasks, parsed, columns, members)
    // The quick filters, ANDed on top of the search box the same way its own
    // tags are ANDed together — see `filterTasks`. Kept out of that function
    // rather than folded into `ParsedSearch`: these two already have their own
    // exact-match values (a goal's id, one of the four statuses) from a
    // dropdown, where `goal:` and `blk:`/`hld:` are typed and only ever
    // narrower questions — "on no goal" or "is blocked" — not this.
    .filter((task) => {
      if (goal === 'all') return true
      return goal === 'none' ? task.goal_id === null : task.goal_id === goal
    })
    .filter((task) => status === 'all' || task.status === status)
  /**
   * The hides, settled last — and only once everything else has had its say.
   *
   * Last because they are the two filters that answer to the others: asking
   * the board for the cancelled cards stands the cancelled hide down rather
   * than emptying the board, which is `hidesCancelled`'s whole job, and the
   * same goes for `hidesHold`. The count is taken off the narrowed set rather
   * than off every card there is, so the number on the button is how many this
   * board, as it is currently being read, is holding back.
   */
  const hidingCancelled = hidesCancelled(showCancelled, status, parsed)
  const unhide = (cards: Task[]) =>
    hidingCancelled ? cards.filter((task) => !isCancelled(task)) : cards
  /**
   * The people filter (CYLIST-99), ANDed on like the quick filters. Each
   * person's count is taken before it is applied, the way the agent chips'
   * are: the number beside a name is how many cards picking that person alone
   * would leave, whoever else is picked.
   */
  const peopleTally = cardsOwned(unhide(narrowed))
  const theirs = narrowed.filter((task) => ownedByAny(task, people))
  const cancelledCount = theirs.filter(isCancelled).length
  const unhidden = unhide(theirs)
  /**
   * The agent chips, last of all. Their counts are taken from the board as
   * everything else has narrowed it but before the chip itself has, so the
   * number on a chip is exactly how many cards pressing it leaves — and both
   * chips keep their numbers while either is pressed.
   */
  const agentTally = agentCounts(unhidden)
  const visibleTasks =
    agent === 'all' ? unhidden : unhidden.filter((task) => passesAgentFilter(task, agent))
  return { visibleTasks, cancelledCount, hidingCancelled, parsed, agentTally, peopleTally }
}

/** The cards to draw, sorted into their columns in the order they arrived. */
export function tasksByColumn(columns: BoardColumn[], tasks: Task[]): Map<string, Task[]> {
  const byColumn = new Map(columns.map((column) => [column.id, [] as Task[]]))
  // The server sends top-level cards only, so nothing here is column-less; the
  // check is what makes that a statement rather than an assumption.
  for (const task of tasks) {
    if (task.column_id !== null) byColumn.get(task.column_id)?.push(task)
  }
  return byColumn
}

/** One lane of the board: a goal's share of every column, or the share on no goal. */
export interface Lane {
  key: string
  goal: Goal | null
}

/**
 * The lanes to draw, in the goals page's own order, with everything on no
 * goal at the bottom.
 *
 * Every open goal gets a lane whether or not a card is on it yet: an empty
 * lane is the drop target that puts the first card on a goal. A settled goal
 * only appears while it still has cards on the board, because a lane for
 * work that has stopped is a row of nothing that never goes away.
 */
export function lanesFor(goals: Goal[], visibleTasks: Task[]): Lane[] {
  return [
    ...goals
      .filter(
        (goal) => goal.status === 'open' || visibleTasks.some((task) => task.goal_id === goal.id),
      )
      .map((goal) => ({ key: goal.id, goal })),
    { key: NO_GOAL_LANE, goal: null },
  ]
}

/** One lane's share of one column's cards. */
export function tasksInLane(columnTasks: Task[], laneKey: string): Task[] {
  return columnTasks.filter((task) => laneKeyOfTask(task) === laneKey)
}

/**
 * The columns a card may not be dropped in, by its template's own stages.
 *
 * Empty for a card with no template, and for one whose template has no
 * stages: silence is not a ban. Empty is also what an unloaded template list
 * gives, which is the right failure — the board waves the card through and
 * the server, which is where the rule actually lives, has the last word.
 */
export function forbiddenColumnsFor(
  task: Task | null | undefined,
  templates: Template[],
  columns: BoardColumn[],
): Set<string> {
  const template = templates.find((candidate) => candidate.id === task?.template_id)
  if (!template || !template.allowed_column_ids.length) return new Set()
  return new Set(
    columns
      .filter((column) => !template.allowed_column_ids.includes(column.id))
      .map((column) => column.id),
  )
}
