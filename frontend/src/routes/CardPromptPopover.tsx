/**
 * The popover under a waiting card on the board (CYLIST-86): the agent's
 * question or permission, its choices, and a reply box — answered without
 * opening the card.
 *
 * When it opens and closes is `cardPopover.ts`; this file wires the events
 * to it and puts the box on the screen.
 *
 * Drawn `position: fixed` rather than inside the card, because a column's
 * card list scrolls and clips whatever hangs out of it — and the box is
 * longer than the gap to the next card. It is still the card's next sibling
 * in the document, though, not a portal at the end of the page: that is what
 * lets Tab walk from the card straight into its choices. And it is outside
 * the `<article>`, so nothing in it is part of the card's drag handle or its
 * click-to-open.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useReducer,
  useRef,
  useState,
  type FocusEvent,
  type PointerEvent,
  type MutableRefObject,
} from 'react'

import type { AgentPrompt } from '../api/client'
import { AgentPromptAnswer } from '../components/AgentPromptAnswer'
import {
  isPopoverOpen,
  placePopover,
  POPOVER_CLOSED,
  POPOVER_LEAVE_GRACE_MS,
  popoverReducer,
  type Placement,
  type PopoverPart,
} from './cardPopover'
import styles from './CardPromptPopover.module.css'

/**
 * The board's screen-reader announcer, for the cards deep inside it: an
 * answer closes the popover it was given in, and the card's new line is not
 * otherwise read out.
 */
export const BoardAnnounce = createContext<(message: string) => void>(() => undefined)

/** The event handlers one half — the card or the popover — wears. */
export interface PopoverHandlers {
  onPointerEnter: (event: PointerEvent) => void
  onPointerLeave: () => void
  onFocus: (event: FocusEvent) => void
  onBlur: (event: FocusEvent) => void
}

export function useCardPopover({
  answerable,
  dragging,
}: {
  answerable: boolean
  dragging: boolean
}) {
  const [state, dispatch] = useReducer(popoverReducer, POPOVER_CLOSED)
  const cardRef = useRef<HTMLElement | null>(null)
  const popoverRef = useRef<HTMLDivElement | null>(null)
  const leaving = useRef<Partial<Record<PopoverPart, number>>>({})
  const open = isPopoverOpen(state, { answerable, dragging })

  useEffect(() => {
    const timers = leaving.current
    return () => Object.values(timers).forEach((id) => window.clearTimeout(id))
  }, [])

  // Whatever closed it, the popover is gone: see `popoverGone`.
  useEffect(() => {
    if (open) return
    window.clearTimeout(leaving.current.popover)
    dispatch({
      type: 'popoverGone',
      focusOnCard: cardRef.current !== null && cardRef.current.contains(document.activeElement),
    })
  }, [open])

  const inside = useCallback(
    (node: EventTarget | null) =>
      node instanceof Node &&
      (cardRef.current?.contains(node) === true || popoverRef.current?.contains(node) === true),
    [],
  )

  const handlersFor = (part: PopoverPart): PopoverHandlers => ({
    onPointerEnter: (event) => {
      window.clearTimeout(leaving.current[part])
      dispatch({ type: 'pointerEnter', part, pointerType: event.pointerType })
    },
    onPointerLeave: () => {
      window.clearTimeout(leaving.current[part])
      leaving.current[part] = window.setTimeout(
        () => dispatch({ type: 'pointerLeave', part }),
        POPOVER_LEAVE_GRACE_MS,
      )
    },
    onFocus: (event) => {
      dispatch({ type: 'focusIn', part, keyboard: event.target.matches(':focus-visible') })
    },
    onBlur: (event) => {
      if (!inside(event.relatedTarget)) dispatch({ type: 'focusOut' })
    },
  })

  /**
   * Escape from anywhere in either half closes it and keeps focus on the
   * card. Listened for on the document, in the capture phase, because the
   * controls inside a card — its assignee picker — stop their keys from
   * reaching the card, so they do not start a drag.
   */
  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key !== 'Escape' || !inside(document.activeElement)) return
      event.preventDefault()
      if (!cardRef.current?.contains(document.activeElement)) cardRef.current?.focus()
      dispatch({ type: 'escape' })
    }
    document.addEventListener('keydown', onKeyDown, true)
    return () => document.removeEventListener('keydown', onKeyDown, true)
  }, [open, inside])

  /** An answer went through: the popover has done its job. */
  const onAnswered = useCallback(() => {
    if (popoverRef.current?.contains(document.activeElement)) cardRef.current?.focus()
    dispatch({ type: 'answered' })
  }, [])

  return {
    open,
    cardRef,
    popoverRef,
    card: handlersFor('card'),
    popover: handlersFor('popover'),
    onAnswered,
  }
}

export function CardPromptPopover({
  taskRef,
  prompt,
  cardRef,
  popoverRef,
  handlers,
  onAnswered,
}: {
  taskRef: string
  prompt: AgentPrompt
  cardRef: MutableRefObject<HTMLElement | null>
  popoverRef: MutableRefObject<HTMLDivElement | null>
  handlers: PopoverHandlers
  onAnswered: (answered: AgentPrompt) => void
}) {
  const [placement, setPlacement] = useState<Placement | null>(null)
  const announce = useContext(BoardAnnounce)
  const now = new Date()

  useLayoutEffect(() => {
    const place = () => {
      const card = cardRef.current
      const popover = popoverRef.current
      if (!card || !popover) return
      setPlacement(
        placePopover(card.getBoundingClientRect(), popover.offsetHeight, {
          width: document.documentElement.clientWidth,
          height: window.innerHeight,
        }),
      )
    }
    place()
    // Capture, so a column's own scroll moves it too — not just the page's.
    window.addEventListener('scroll', place, true)
    window.addEventListener('resize', place)
    const resized = new ResizeObserver(place)
    if (popoverRef.current) resized.observe(popoverRef.current)
    if (cardRef.current) resized.observe(cardRef.current)
    return () => {
      window.removeEventListener('scroll', place, true)
      window.removeEventListener('resize', place)
      resized.disconnect()
    }
  }, [cardRef, popoverRef])

  return (
    <div
      ref={popoverRef}
      className={`${styles.cardPopover} ${placement?.side === 'above' ? styles.cardPopoverAbove : ''}`}
      style={
        placement
          ? { top: placement.top, left: placement.left, width: placement.width }
          : { visibility: 'hidden', top: 0, left: 0 }
      }
      {...handlers}
      // The board behind it listens for nothing, but a click here is never a
      // click on the card it hangs from, whatever is added up the tree later.
      onClick={(event) => event.stopPropagation()}
      onPointerDown={(event) => event.stopPropagation()}
    >
      <div className={styles.cardPopoverBox}>
        <AgentPromptAnswer
          taskRef={taskRef}
          prompt={prompt}
          now={now}
          variant="popover"
          onAnswered={(answered) => {
            announce(`Answered ${taskRef}: ${answered.answer ?? ''}. The agent is working again.`)
            onAnswered(answered)
          }}
        />
      </div>
    </div>
  )
}
