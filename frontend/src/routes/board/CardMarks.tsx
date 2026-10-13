/**
 * The marks on a card's face that open into what they stand for: the stage
 * bar, the sub-task count and the pull-request mark. Which is open, and which
 * is waiting to, is `useCardFocus`'s to decide.
 */

import type { PrRef, Task } from '../../api/client'
import { leastAdvancedPrState } from '../../components/prRefs'
import { PrIcon, prRefLabel } from '../../components/ui'
import { TickIcon } from './icons'
import { keepFromCard } from './keepFromCard'
import type { MarkHandlers } from './useCardFocus'
import styles from './TaskCard.module.css'

/** A mark's own events, without the click it takes for itself. */
function markEvents(handlers: MarkHandlers) {
  const { onPointerEnter, onPointerLeave, onFocus, onBlur } = handlers
  return { onPointerEnter, onPointerLeave, onFocus, onBlur }
}

/** A mark's classes: open, or filling its hairline while it waits to open. */
function markClass(kind: string | undefined, open: boolean, arming: boolean) {
  return [styles.mark, kind, open && styles.markOpen, arming && styles.markArming]
    .filter(Boolean)
    .join(' ')
}

interface MarkProps {
  open: boolean
  arming: boolean
  handlers: MarkHandlers
}

/**
 * The stage a card is on, as a short bar and its name — and the control that
 * moves it, from the keyboard. Pointed at, it opens the stages themselves
 * (see `StageList`), which is where a pointer moves it.
 *
 * Every key and press stops here: the card around this is dnd-kit's drag
 * handle and the button that opens the dialog, and an Enter left to bubble
 * would open a task the reader was only stepping through.
 */
export function StageMark({
  task,
  onMove,
  open,
  arming,
  handlers,
}: MarkProps & {
  task: Task
  /** Undefined for the overlay's copy, which cannot be moved in mid-air. */
  onMove: ((index: number) => void) | undefined
}) {
  const labels = task.sub_statuses
  const index = task.sub_status_index ?? 0
  const current = labels[index] ?? ''
  const done = index === labels.length - 1

  return (
    <span
      className={markClass(styles.stageMark, open, arming)}
      role={onMove ? 'slider' : undefined}
      tabIndex={onMove ? 0 : undefined}
      aria-label={onMove ? 'Stage' : undefined}
      aria-valuemin={onMove ? 1 : undefined}
      aria-valuemax={onMove ? labels.length : undefined}
      aria-valuenow={onMove ? index + 1 : undefined}
      aria-valuetext={`Stage ${index + 1} of ${labels.length}: ${current}`}
      aria-expanded={open}
      {...markEvents(handlers)}
      onPointerDown={keepFromCard.onPointerDown}
      onClick={(event) => {
        event.stopPropagation()
        handlers.onPick()
      }}
      onKeyDown={(event) => {
        if (!onMove) return
        event.stopPropagation()
        const to = {
          ArrowLeft: index - 1,
          ArrowDown: index - 1,
          ArrowRight: index + 1,
          ArrowUp: index + 1,
          Home: 0,
          End: labels.length - 1,
        }[event.key]
        if (to === undefined) return
        event.preventDefault()
        if (to !== index && to >= 0 && to < labels.length) onMove(to)
      }}
    >
      <span className={styles.stageTrack} aria-hidden="true">
        {labels.map((label, position) => (
          <span
            key={`${position}-${label}`}
            className={[
              styles.stageStep,
              (position < index || (position === index && done)) && styles.stageStepDone,
              position === index && !done && styles.stageStepHere,
            ]
              .filter(Boolean)
              .join(' ')}
          />
        ))}
      </span>
      <span className={styles.stageName}>{current}</span>
      <span className={styles.stageCount}>
        {index + 1}/{labels.length}
      </span>
    </span>
  )
}

/**
 * How much of the card's work is split off and how much of that is done: a
 * tick and a count. Pointed at, it opens the parts themselves.
 */
export function SubtaskMark({ task, open, arming, handlers }: MarkProps & { task: Task }) {
  const done = task.subtask_count - task.open_subtask_count
  const label = `${done} of ${task.subtask_count} sub-tasks finished`

  return (
    <button
      type="button"
      className={markClass(styles.subtaskMark, open, arming)}
      aria-label={label}
      aria-expanded={open}
      {...markEvents(handlers)}
      {...keepFromCard}
      onClick={(event) => {
        event.stopPropagation()
        handlers.onPick()
      }}
    >
      <TickIcon />
      <span aria-hidden="true">
        {done}/{task.subtask_count}
      </span>
    </button>
  )
}

/**
 * Every pull request on the card as one mark: the furthest behind decides its
 * colour and drawing, since the card is not merged while any of its work is
 * still in review, and a count says how many it stands for. Pointed at, it
 * swaps the card's line of facts for the list of them.
 */
export function PrMark({ refs, open, arming, handlers }: MarkProps & { refs: PrRef[] }) {
  const state = leastAdvancedPrState(refs) ?? 'raised'
  const only = refs.length === 1 ? refs[0] : undefined
  const label = only
    ? `Pull request ${prRefLabel(only.url)}, ${state}`
    : `${refs.length} pull requests, the furthest behind ${state}`

  return (
    <button
      type="button"
      className={`${markClass(styles.prMark, open, arming)} ${styles[`pr_${state}`]}`}
      aria-label={label}
      aria-expanded={open}
      title={label}
      {...markEvents(handlers)}
      {...keepFromCard}
      onClick={(event) => {
        event.stopPropagation()
        handlers.onPick()
      }}
    >
      <PrIcon state={state} size={13} />
      {refs.length > 1 ? <span className={styles.prCount}>×{refs.length}</span> : null}
    </button>
  )
}
