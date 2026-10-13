import { useState, type KeyboardEvent } from 'react'
import type { Person } from '../../api/client'
import { Tagged } from './Tagged'
import { Tip, useTip } from './Tip'
import styles from './ui.module.css'

/**
 * The stage labels a task's sub-status is made of, and where it has got to.
 *
 * Nothing here is only a colour: the bar says position, the head line names
 * the stage you are on, and the hover tip carries whichever label you point
 * at in full. Labels can be a sentence long, so no part of the design asks
 * one to fit inside a fixed width.
 */
interface SubStatusProps {
  labels: string[]
  index: number
  /**
   * Called with the stage clicked or arrowed to. Omitted where the bar is
   * only reporting — the read-only detail view — which also drops the slider
   * role and the pointer cursor rather than offering a control that does
   * nothing.
   */
  onMove?: (index: number) => void
  /**
   * Let the current stage's label wrap onto as many lines as it needs. For
   * the dialog, which has the width for it; a board card clips to one line
   * instead, and leaves the rest to the tip.
   */
  wrap?: boolean
  /**
   * The project's people, for drawing an `@` tag in a stage label. Omitted
   * where they are not to hand, which only costs the label its highlight —
   * the words are the words either way.
   */
  members?: readonly Person[]
}

/**
 * The stage a key asks the slider to go to, or undefined for a key it does
 * not answer. Down and Left go back a stage, Up and Right forward, as the
 * ARIA slider pattern has them; the result may be off either end, which the
 * caller ignores.
 */
function stageForKey(key: string, index: number, count: number): number | undefined {
  switch (key) {
    case 'ArrowLeft':
    case 'ArrowDown':
      return index - 1
    case 'ArrowRight':
    case 'ArrowUp':
      return index + 1
    case 'Home':
      return 0
    case 'End':
      return count - 1
    default:
      return undefined
  }
}

/**
 * A task's sub-status as a segmented slider: one segment per stage, filled up
 * to the stage it is on, with that stage drawn as the thumb.
 *
 * Interactive, the whole track is one slider — click a segment or arrow along
 * it to move the task to that stage, backwards as readily as forwards. The
 * segments themselves are not focus stops; the track is, and carries the
 * value, so tabbing through a board card is one stop rather than four.
 */
export function SubStatusBar({ labels, index, onMove, wrap = false, members }: SubStatusProps) {
  const tip = useTip()
  /**
   * The stage under the cursor, when it is ahead of the current one. Fills the
   * segments in between at half strength, so the bar shows where a click would
   * take the card before it takes it.
   */
  const [preview, setPreview] = useState<number | null>(null)
  const live = onMove !== undefined
  const current = labels[index] ?? ''
  /** There is no stage past the last one to be unfinished about, so reaching
   * it is done rather than merely arrived — see the thumb's own colour. */
  const done = index === labels.length - 1

  /** The tip for a stage: its label, and what pointing at it is offering. */
  const noteFor = (position: number) =>
    position === index
      ? `Stage ${position + 1} of ${labels.length} · ${done ? 'done' : 'you are here'}`
      : `Stage ${position + 1} of ${labels.length}${live ? ' · click to move here' : ''}`

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (!onMove) return
    // Every key stops here, not just the ones that move the slider: the
    // board card around this is dnd-kit's drag handle and the button
    // that opens the dialog, so an Enter left to bubble would open a
    // task the reader was only stepping through.
    event.stopPropagation()
    const to = stageForKey(event.key, index, labels.length)
    if (to === undefined) return
    event.preventDefault()
    if (to !== index && to >= 0 && to < labels.length) onMove(to)
  }

  function segmentClass(position: number): string {
    return [
      styles.subStatusSegment,
      (position < index || (position === index && done)) && styles.subStatusDone,
      position === index && styles.subStatusHere,
      preview !== null && position > index && position <= preview && styles.subStatusAhead,
    ]
      .filter(Boolean)
      .join(' ')
  }

  return (
    <div className={styles.subStatus}>
      <div className={styles.subStatusHead}>
        <span
          className={`${styles.subStatusNow} ${wrap ? styles.subStatusNowWrapped : ''}`}
          onPointerEnter={(event) => tip.show(event, current, noteFor(index))}
          onPointerLeave={tip.hide}
        >
          {/* A stage label is one line on a card, and the `>` tag it might
              hold is not offered for one — see the editor. */}
          {members ? <Tagged text={current} members={members} /> : current}
        </span>
        <span className={styles.subStatusCount}>
          {index + 1}/{labels.length}
        </span>
      </div>

      <div
        className={`${styles.subStatusTrack} ${live ? styles.subStatusTrackLive : ''}`}
        // Read-only, the track is a picture of the list below it and has
        // nothing of its own to say. Live, it is the slider itself.
        role={live ? 'slider' : undefined}
        aria-hidden={live ? undefined : true}
        tabIndex={live ? 0 : undefined}
        aria-label={live ? 'Sub-status' : undefined}
        aria-valuemin={live ? 1 : undefined}
        aria-valuemax={live ? labels.length : undefined}
        aria-valuenow={live ? index + 1 : undefined}
        aria-valuetext={live ? `Stage ${index + 1} of ${labels.length}: ${current}` : undefined}
        onPointerLeave={() => {
          setPreview(null)
          tip.hide()
        }}
        onFocus={(event) => tip.show(event, current, noteFor(index))}
        onBlur={tip.hide}
        onKeyDown={onKeyDown}
      >
        {labels.map((label, position) => (
          <span
            key={position}
            className={segmentClass(position)}
            onPointerEnter={(event) => {
              setPreview(position)
              tip.show(event, label, noteFor(position))
            }}
            onClick={(event) => {
              if (!onMove) return
              // The card is both the drag handle and the button that opens the
              // dialog, so a click left to bubble would do one of those
              // instead of moving the stage.
              event.stopPropagation()
              if (position !== index) onMove(position)
            }}
            onPointerDown={(event) => event.stopPropagation()}
          />
        ))}
      </div>

      {/* The bar is a picture of the list; this is the list. Screen readers
          get every stage and which one is current, in order, without the
          segments having to be four more things to tab past. */}
      <ol className="visually-hidden">
        {labels.map((label, position) => (
          <li key={position}>
            {position === index ? `${label} — ${done ? 'done' : 'current stage'}` : label}
          </li>
        ))}
      </ol>

      <Tip tip={tip.tip} />
    </div>
  )
}
