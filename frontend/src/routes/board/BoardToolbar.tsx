/**
 * The bar above the board: whose cards it is showing, the two agent counts
 * while there is anything to count, then the search and the board's own
 * controls.
 *
 * No visible page title (CYLIST-97): the breadcrumb and the top nav already
 * say this is the board, and a heading saying it a third time spent the bar's
 * best width on nothing. The counts sit at the start rather than with the
 * controls: they are a signal — how many cards wait on a person, how many
 * have an agent busy — and a signal drawn the size of a button beside the
 * search box was read as one more thing to press.
 */

import type { Board, Goal, Person, Task } from '../../api/client'
import type { AgentFilter } from '../agentState'
import { BoardMenu } from './BoardMenu'
import type { BoardFilters } from './narrowBoard'
import { PeopleFilter } from './PeopleFilter'
import { QuickFilters } from './QuickFilters'
import { SearchBar } from './SearchBar'
import styles from './BoardToolbar.module.css'

/** The filters the reader picks on the toolbar, as against the hides they toggle. */
export type ChosenFilters = Omit<BoardFilters, 'showCancelled'>

export function BoardToolbar({
  board,
  members,
  meId,
  tasks,
  boardName,
  goals,
  filters,
  onFiltersChange,
  showCancelled,
  onShowCancelled,
  cancelledCount,
  hidingCancelled,
  agentTally,
  peopleTally,
  shaping,
}: {
  board: Board
  members: Person[]
  meId: string | null
  /** Every card on the board, unfiltered. */
  tasks: Task[]
  /**
   * Which template's board this is, or null on a project with only the one
   * board. The dial at the board's edge only says so while it is open.
   */
  boardName: string | null
  goals: Goal[]
  filters: ChosenFilters
  onFiltersChange: (changes: Partial<ChosenFilters>) => void
  showCancelled: boolean
  onShowCancelled: (show: boolean) => void
  cancelledCount: number
  hidingCancelled: boolean
  agentTally: { needsYou: number; working: number }
  peopleTally: Map<string, number>
  /**
   * Templates and columns. Both change the shape of the board rather than what
   * is on it, which is one permission, and null where the reader lacks it —
   * the menu is not drawn then.
   */
  shaping: { templateCount: number; onOpenTemplates: () => void; onAddColumn: () => void } | null
}) {
  return (
    <div className={styles.toolbar}>
      {/* Still the page's heading for whoever navigates by headings; it is
          only the eye that has the breadcrumb to read instead. */}
      {boardName ? (
        // Once there are boards to choose between, which one this is has to
        // be said where the eye starts: the breadcrumb only says "Board".
        <h1 className={styles.boardName}>{boardName}</h1>
      ) : (
        <h1 className="visually-hidden">Kanban board</h1>
      )}
      {/* Where the title used to be (CYLIST-99): whose board this is being
          read as comes before what is on it. */}
      <PeopleFilter
        members={members}
        meId={meId}
        tasks={tasks}
        people={filters.people}
        onPeople={(people) => onFiltersChange({ people })}
        tally={peopleTally}
      />
      {/* Only while an agent is on a card this board is showing, or while a
          chip is pressed — a pressed chip is a filter in force, and hiding
          it would leave the board narrowed with no way to let go. Otherwise
          "0 need you · 0 working" is a line about nothing, and the search
          box takes its width. */}
      {agentTally.needsYou + agentTally.working > 0 || filters.agent !== 'all' ? (
        <AgentChips
          value={filters.agent}
          onChange={(agent) => onFiltersChange({ agent })}
          needsYou={agentTally.needsYou}
          working={agentTally.working}
        />
      ) : null}
      <SearchBar
        query={filters.search}
        onChange={(search) => onFiltersChange({ search })}
        columns={board.columns}
        members={members}
        goals={goals}
      />
      <div className={styles.tools}>
        <QuickFilters
          goals={goals}
          goal={filters.goal}
          onGoal={(goal) => onFiltersChange({ goal })}
          status={filters.status}
          onStatus={(status) => onFiltersChange({ status })}
          showCancelled={showCancelled}
          onShowCancelled={onShowCancelled}
          cancelledCount={cancelledCount}
          hiding={hidingCancelled}
        />
        {shaping ? (
          <BoardMenu
            board={board}
            templateCount={shaping.templateCount}
            onTemplates={shaping.onOpenTemplates}
            onAddColumn={shaping.onAddColumn}
          />
        ) : null}
      </div>
    </div>
  )
}

/**
 * "Needs you" and "working" (CYLIST-90): the two questions a board with agents
 * on it gets asked most, as two small counts at the start of the toolbar.
 *
 * Small and quiet on purpose: a dot, a number and two words, with no fill
 * until one is pressed. They are worth a glance, not a toolbar's worth of
 * chrome — and drawn the size of buttons beside the search box they were read
 * as the board's main controls. While the pair is drawn, a zero goes grey
 * rather than away, so one count arriving does not shift the other.
 *
 * Pressing one narrows the board to those cards, ANDed with the search box and
 * the filter like every other filter; pressing it again, or the other one,
 * lets go. One at a time, because "needs you or working" is just "has a live
 * agent", which the borders already show.
 *
 * The counts move with the board's own socket: a `board.changed` refetches the
 * cards and these are counted off them, so there is no polling of their own —
 * and the pair appears and goes the same way, as agents start and stop.
 */
function AgentChips({
  value,
  onChange,
  needsYou,
  working,
}: {
  value: AgentFilter
  onChange: (value: AgentFilter) => void
  needsYou: number
  working: number
}) {
  const chips = [
    {
      key: 'needs_you' as const,
      label: 'need you',
      count: needsYou,
      title: 'Only the cards whose agent is waiting on a person',
      className: styles.needsChip,
    },
    {
      key: 'working' as const,
      label: 'working',
      count: working,
      title: 'Only the cards whose agent is working',
      className: styles.workingChip,
    },
  ]

  return (
    <div className={styles.agentChips} role="group" aria-label="Agent filters">
      {chips.map((chip) => {
        const on = value === chip.key
        return (
          <button
            key={chip.key}
            type="button"
            aria-pressed={on}
            className={[
              styles.agentChip,
              chip.className,
              chip.count > 0 ? styles.agentChipLive : '',
              on ? styles.agentChipOn : '',
            ]
              .filter(Boolean)
              .join(' ')}
            title={on ? 'Show every card again' : chip.title}
            onClick={() => onChange(on ? 'all' : chip.key)}
          >
            <span className={styles.agentChipDot} aria-hidden="true" />
            <span className={styles.agentChipCount}>{chip.count}</span>
            {chip.label}
          </button>
        )
      })}
    </div>
  )
}
