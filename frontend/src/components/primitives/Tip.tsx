import { useState } from 'react'
import { createPortal } from 'react-dom'
import styles from './ui.module.css'

/** Where a tip is pointing, and what it has to say there. */
export interface TipState {
  text: string
  note: string
  /** Viewport coordinates of the middle of the thing being described. */
  x: number
  top: number
  bottom: number
}

/**
 * A hover label for a control too small to carry its own text.
 *
 * `show` is handed the pointer event so it can measure the element under the
 * cursor; the caller supplies the words, because the element itself often has
 * none — that is the reason for the tip.
 */
export function useTip() {
  const [tip, setTip] = useState<TipState | null>(null)

  const show = (event: { currentTarget: Element }, text: string, note: string) => {
    const box = event.currentTarget.getBoundingClientRect()
    setTip({ text, note, x: box.left + box.width / 2, top: box.top, bottom: box.bottom })
  }

  return { tip, show, hide: () => setTip(null) }
}

/** How close to the top of the viewport an anchor can be before its tip goes underneath. */
const ROOM_ABOVE = 90
/** Half a full-width tip: how far from either edge of the viewport its centre may sit. */
const EDGE_MARGIN = 140

/**
 * The tip itself, drawn into `document.body`.
 *
 * A portal rather than a child of what it describes: board columns scroll and
 * clip, so a tip positioned inside one is cut off by the very overflow that
 * makes the board usable. Fixed coordinates put it back over the top of
 * everything, and it is nudged below its anchor when there is no room above.
 */
export function Tip({ tip }: { tip: TipState | null }) {
  if (tip === null) return null

  const below = tip.top < ROOM_ABOVE

  return createPortal(
    <span
      role="tooltip"
      className={`${styles.tip} ${below ? styles.tipBelow : ''}`}
      style={{
        // Clamped to the viewport: half a full-width tip either side of the
        // anchor is what centring needs, and the segments at the ends of a
        // card near the edge of the board do not have it.
        left: Math.min(
          Math.max(tip.x, EDGE_MARGIN),
          Math.max(window.innerWidth - EDGE_MARGIN, EDGE_MARGIN),
        ),
        top: below ? tip.bottom : tip.top,
      }}
    >
      <span className={styles.tipText}>{tip.text}</span>
      <span className={styles.tipNote}>{tip.note}</span>
    </span>,
    document.body,
  )
}
