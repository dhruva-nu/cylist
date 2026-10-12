import { useCallback, useEffect, useRef, useState, type FocusEvent } from 'react'

/**
 * The marks on a card that open into what they stand for: the pull-request
 * mark into the list of them, the stage bar into the stages, the sub-task
 * count into the sub-tasks.
 */
export type CardFocus = 'prs' | 'stages' | 'subs'

/** How long a mark has to be rested on before it opens: long enough that
 * passing over one on the way to another does nothing. */
const MARK_REST_MS = 200

/** How long an open list waits after the pointer leaves before it shuts —
 * long enough to travel from one mark to the next, so the slot goes straight
 * from one list to the other instead of dropping back to the facts between. */
const MARK_GRACE_MS = 350

/**
 * Which of those marks has opened its list, and which is waiting to.
 *
 * A mark opens only after the pointer has rested on it, never as it passes:
 * the stage bar and the sub-task count sit on the row above the pull-request
 * mark, and a mark that opened on contact opened on the way down to the one
 * below it. Focus opens at once, because a keyboard does not pass over
 * anything, and so does a click — for a pointer that cannot hover. A click
 * never shuts: the pointer that clicked has usually just watched the list
 * open, and shutting it then would undo what it came for. Leaving shuts.
 *
 * Whatever opens goes into the one slot under the card's bottom line, and the
 * slot holds itself open while the pointer is in it, so its rows can be used.
 */
export function useCardFocus() {
  const [focus, setFocus] = useState<CardFocus | null>(null)
  const [arming, setArming] = useState<CardFocus | null>(null)
  // Each list mounts the first time it opens and stays, so it has something
  // to slide out with when the next one takes the slot.
  const [opened, setOpened] = useState<ReadonlySet<CardFocus>>(() => new Set())
  const openTimer = useRef<number | undefined>(undefined)
  const closeTimer = useRef<number | undefined>(undefined)

  // Neither timer may fire into a card that has gone — dropped into another
  // column, filtered away — so both are cleared when it unmounts.
  useEffect(
    () => () => {
      window.clearTimeout(openTimer.current)
      window.clearTimeout(closeTimer.current)
    },
    [],
  )

  const show = useCallback((which: CardFocus | null) => {
    window.clearTimeout(openTimer.current)
    window.clearTimeout(closeTimer.current)
    setArming(null)
    setFocus(which)
    if (which) setOpened((seen) => (seen.has(which) ? seen : new Set(seen).add(which)))
  }, [])
  const arm = (which: CardFocus) => {
    window.clearTimeout(openTimer.current)
    window.clearTimeout(closeTimer.current)
    if (focus === which) return
    setArming(which)
    openTimer.current = window.setTimeout(() => show(which), MARK_REST_MS)
  }
  const letGo = () => {
    window.clearTimeout(openTimer.current)
    setArming(null)
    closeTimer.current = window.setTimeout(() => setFocus(null), MARK_GRACE_MS)
  }
  const keep = () => window.clearTimeout(closeTimer.current)

  /** What a mark spreads onto itself. Focus opens only when it came from the
   * keyboard: a click has its own say, below, and focus a click leaves behind
   * outlives the pointer that gave it. */
  const mark = (which: CardFocus): MarkHandlers => ({
    onPointerEnter: () => arm(which),
    onPointerLeave: letGo,
    onFocus: (event) => {
      if (event.currentTarget.matches(':focus-visible')) show(which)
    },
    onBlur: letGo,
    onPick: () => show(which),
  })
  /** What the slot under the bottom line spreads onto itself. */
  const slot = { onPointerEnter: keep, onPointerLeave: letGo, onFocus: keep, onBlur: letGo }

  return { focus, arming, opened, mark, slot, letGo }
}

/** The handlers `useCardFocus` hands a mark. */
export interface MarkHandlers {
  onPointerEnter: () => void
  onPointerLeave: () => void
  onFocus: (event: FocusEvent<HTMLElement>) => void
  onBlur: () => void
  /** A click on the mark, which opens its list at once. */
  onPick: () => void
}
