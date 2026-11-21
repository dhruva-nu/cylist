/**
 * The board switcher: a small dial at the board's left edge.
 *
 * A right-pointing chevron sits halfway down the board's left edge; that is
 * the only thing drawn until it is wanted. Hovering it opens the dial and a
 * click keeps it open. There is no panel behind it: only the boards' names
 * and one thin ring line, the names riding along the ring's outside edge with
 * the board you are on beside the chevron and the others curving back to the
 * left and fading.
 *
 * Scrolling over it turns it, in the direction scrolled, one board for every
 * 50px — so a long flick turns several — and it wraps round. The board
 * switches as it turns. ↑/↓ turn it from the keyboard and Esc closes it.
 *
 * The names carry a halo in the page's own background colour, which is what
 * keeps them readable over the cards without a panel to sit on.
 */

import { useEffect, useRef, useState } from 'react'
import type { BoardSummary } from './boards'
import styles from './BoardDial.module.css'

/** One board as the dial draws it. */
export interface DialBoard {
  key: string
  name: string
  summary: BoardSummary
}

/** Scroll travel for one step of the dial. */
const NOTCH_PX = 50
/** How long the pointer may be away before an unpinned dial closes. */
const CLOSE_DELAY_MS = 220

/*
 * The geometry. The ring's centre sits off to the left of the board; the
 * chevron sits on the ring where it crosses the middle, and each name sits
 * STEP degrees further round a slightly larger circle than the one drawn.
 */
const CHEVRON_X = 18
const RING_R = 104
const NAME_R = 144
const NAME_X = CHEVRON_X + 40
const CENTRE_Y = 115
const STEP = 20

/** Where the board `k` steps from the selected one is drawn. */
export function dialPlace(k: number) {
  const angle = (k * STEP * Math.PI) / 180
  const x = NAME_X - NAME_R * (1 - Math.cos(angle))
  const y = CENTRE_Y + NAME_R * Math.sin(angle)
  const far = Math.abs(k)
  const scale = k === 0 ? 1 : Math.max(0.8, 1 - far * 0.08)
  return {
    transform: `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px) translateY(-50%) rotate(${(k * STEP * 0.6).toFixed(1)}deg) scale(${scale.toFixed(2)})`,
    opacity: [1, 0.62, 0.32, 0][Math.min(far, 3)] ?? 0,
    zIndex: 10 - far,
    far,
  }
}

/** How many steps round from `selected` the board at `index` is, the short way: −n/2…n/2. */
export function stepsFrom(index: number, selected: number, count: number): number {
  let k = (((index - selected) % count) + count) % count
  if (k > count / 2) k -= count
  return k
}

export function BoardDial({
  boards,
  selectedKey,
  onSelect,
}: {
  boards: DialBoard[]
  selectedKey: string
  onSelect: (key: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [pinned, setPinned] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const closeTimer = useRef<number | null>(null)
  const travel = useRef(0)

  const count = boards.length
  const selected = Math.max(
    0,
    boards.findIndex((board) => board.key === selectedKey),
  )
  const current = boards[selected]

  // Read through a ref by the wheel listener, which is attached once.
  const turnRef = useRef<(by: number) => void>(() => {})
  turnRef.current = (by: number) => {
    const next = boards[(((selected + by) % count) + count) % count]
    if (next) onSelect(next.key)
  }

  function cancelClose() {
    if (closeTimer.current !== null) window.clearTimeout(closeTimer.current)
    closeTimer.current = null
  }

  useEffect(() => cancelClose, [])

  // Not React's `onWheel`: React listens passively, and a dial that turns
  // while the board scrolls underneath it is two things moving at once.
  useEffect(() => {
    const root = rootRef.current
    if (!root) return
    function onWheel(event: WheelEvent) {
      event.preventDefault()
      cancelClose()
      setOpen(true)
      travel.current += event.deltaY
      // Every whole notch at once: turning one at a time would read the same
      // selection for each, since the board has not redrawn in between.
      const steps = Math.trunc(travel.current / NOTCH_PX)
      if (steps === 0) return
      travel.current -= steps * NOTCH_PX
      turnRef.current(steps)
    }
    root.addEventListener('wheel', onWheel, { passive: false })
    return () => root.removeEventListener('wheel', onWheel)
  }, [])

  function close() {
    cancelClose()
    setOpen(false)
    setPinned(false)
  }

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      setOpen(true)
      turnRef.current(event.key === 'ArrowDown' ? 1 : -1)
    } else if (event.key === 'Escape' && open) {
      event.stopPropagation()
      close()
    }
  }

  return (
    <div
      ref={rootRef}
      className={[styles.dial, open && styles.open].filter(Boolean).join(' ')}
      onMouseEnter={() => {
        cancelClose()
        setOpen(true)
      }}
      onMouseLeave={() => {
        if (pinned) return
        cancelClose()
        closeTimer.current = window.setTimeout(() => setOpen(false), CLOSE_DELAY_MS)
      }}
      onKeyDown={onKeyDown}
    >
      <button
        type="button"
        className={styles.chevron}
        style={{ left: CHEVRON_X }}
        aria-expanded={open}
        aria-label={`Board: ${current?.name ?? ''}. Press up or down to switch boards.`}
        title="Switch board — hover, then scroll"
        onClick={() => {
          if (pinned) close()
          else {
            setOpen(true)
            setPinned(true)
          }
        }}
      >
        <svg
          viewBox="0 0 24 24"
          width="18"
          height="18"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="m9 6 6 6-6 6" />
        </svg>
      </button>

      {open ? (
        <div className={styles.face} role="group" aria-label="Boards">
          <span
            className={styles.ring}
            style={{
              left: CHEVRON_X - 2 * RING_R,
              top: CENTRE_Y - RING_R,
              width: 2 * RING_R,
              height: 2 * RING_R,
            }}
            aria-hidden="true"
          />
          {boards.map((board, index) => {
            const k = stepsFrom(index, selected, count)
            const place = dialPlace(k)
            const isOn = k === 0
            const tallest = Math.max(1, ...board.summary.perColumn)
            return (
              <button
                key={board.key}
                type="button"
                className={[styles.item, isOn && styles.itemOn].filter(Boolean).join(' ')}
                style={{ transform: place.transform, opacity: place.opacity, zIndex: place.zIndex }}
                tabIndex={place.far <= 1 ? 0 : -1}
                aria-hidden={place.far >= 3 ? true : undefined}
                aria-current={isOn ? 'page' : undefined}
                onClick={() => onSelect(board.key)}
              >
                <span className={styles.mark} aria-hidden="true">
                  {board.summary.perColumn.map((cards, i) => (
                    <span
                      key={i}
                      className={styles.bar}
                      style={{ height: Math.max(2, Math.round((12 * cards) / tallest)) }}
                    />
                  ))}
                </span>
                <span>{board.name}</span>
                <span className={styles.count}>
                  {board.summary.attention ? (
                    <span className={styles.dot} title={board.summary.attention} />
                  ) : null}
                  {board.summary.open}
                  <span className="visually-hidden">
                    {' '}
                    open{board.summary.attention ? `, ${board.summary.attention}` : ''}
                  </span>
                </span>
                {isOn ? <span className={styles.flow}>{board.summary.flow}</span> : null}
              </button>
            )
          })}
        </div>
      ) : null}
    </div>
  )
}
