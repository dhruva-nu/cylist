/**
 * The bar above the board: what the page is, the two counts worth glancing
 * at, then the search and the board's own controls.
 *
 * The title used to be a heading of its own above a toolbar of six buttons,
 * which spent two rows of the window before the first card. The counts sit
 * with the title rather than with the controls: they are a signal — how many
 * cards wait on a person, how many have an agent busy — and a signal drawn
 * the size of a button beside the search box was read as one more thing to
 * press.
 */

import type { Board, Goal, Person } from '../../api/client'
import type { AgentFilter } from '../agentState'
import { BoardMenu } from './BoardMenu'
import { ColumnsIcon, LanesIcon } from './icons'
import type { BoardFilters } from './narrowBoard'
import { QuickFilters } from './QuickFilters'
import { SearchBar } from './SearchBar'
import styles from './BoardToolbar.module.css'

/** The filters the reader picks on the toolbar, as against the hides they toggle. */
export type ChosenFilters = Omit<BoardFilters, 'showCancelled'>

export function BoardToolbar({
  board,
  members,
  goals,
  filters,
  onFiltersChange,
  showCancelled,
  onShowCancelled,
  cancelledCount,
  hidingCancelled,
  agentTally,
  lanesOn,
  onLanesOn,
  foldAll,
  shaping,
}: {
  board: Board
  members: Person[]
  goals: Goal[]
  filters: ChosenFilters
  onFiltersChange: (changes: Partial<ChosenFilters>) => void
  showCancelled: boolean
  onShowCancelled: (show: boolean) => void
  cancelledCount: number
  hidingCancelled: boolean
  agentTally: { needsYou: number; working: number }
  lanesOn: boolean
  onLanesOn: (on: boolean) => void
  /**
   * The fold-every-lane button's state and action. Null while the board is not
   * drawn in lanes: off the lanes, it is a button about nothing.
   */
  foldAll: { everyLaneFolded: boolean; onToggle: () => void } | null
  /**
   * Templates and columns. Both change the shape of the board rather than what
   * is on it, which is one permission, and null where the reader lacks it —
   * the menu is not drawn then.
   */
  shaping: { templateCount: number; onOpenTemplates: () => void; onAddColumn: () => void } | null
}) {
  return (
    <div className={styles.toolbar}>
      <div className={styles.titleGroup}>
        <h1 className={styles.pageTitle}>Kanban board</h1>
        <AgentChips
          value={filters.agent}
          onChange={(agent) => onFiltersChange({ agent })}
          needsYou={agentTally.needsYou}
          working={agentTally.working}
        />
      </div>
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
        {/* Columns or lanes: the same board read by column, or split into
            one lane per goal. Two segments rather than a button that says
            "on" and "off", so the board says which way it is being read. */}
        <div className={styles.segmented} role="group" aria-label="Board layout">
          <button type="button" aria-pressed={!lanesOn} onClick={() => onLanesOn(false)}>
            <ColumnsIcon />
            Columns
          </button>
          <button
            type="button"
            aria-pressed={lanesOn}
            onClick={() => onLanesOn(true)}
            title="Split the board into one lane per goal"
          >
            <LanesIcon />
            Lanes
          </button>
        </div>
        {foldAll ? (
          <button
            type="button"
            className={styles.iconButton}
            onClick={foldAll.onToggle}
            aria-label={foldAll.everyLaneFolded ? 'Open every lane' : 'Fold every lane'}
            title={
              foldAll.everyLaneFolded
                ? 'Open every goal back up'
                : 'Fold every goal down to its heading'
            }
          >
            {foldAll.everyLaneFolded ? '⌄' : '⌃'}
          </button>
        ) : null}
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
 * on it gets asked most, as two small counts beside the page's title.
 *
 * Small and quiet on purpose: a dot, a number and two words, with no fill
 * until one is pressed. They are worth a glance, not a toolbar's worth of
 * chrome — and drawn the size of buttons beside the search box they were read
 * as the board's main controls. A zero goes grey rather than away, so the
 * pair never moves the title.
 *
 * Pressing one narrows the board to those cards, ANDed with the search box and
 * the filter like every other filter; pressing it again, or the other one,
 * lets go. One at a time, because "needs you or working" is just "has a live
 * agent", which the borders already show.
 *
 * The counts move with the board's own socket: a `board.changed` refetches the
 * cards and these are counted off them, so there is no polling of their own.
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
