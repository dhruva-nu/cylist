/**
 * One choice from a handful, drawn as joined buttons and behaving as a radio
 * group.
 *
 * A radio group rather than a row of buttons: the options are one choice with
 * one answer, so a screen reader should say "2 of 3" and the arrow keys should
 * move between them. Roving tabindex keeps the whole control to a single tab
 * stop — two adjacent tab stops that look like a single control is how a
 * five-field form comes to feel like a ten-field one.
 *
 * The vault's kind picker, the goals page's list-or-calendar and the sidebar's
 * theme are all this. The first two wear the stylesheet beside it; the
 * sidebar, where it sits in a narrower column, passes its own classes.
 */

import { useRef, type KeyboardEvent } from 'react'
import styles from './SegmentedChoice.module.css'

export interface SegmentOption<Value extends string> {
  value: Value
  label: string
}

/** Which way an arrow key moves through the group: forward, back, or not at all. */
function arrowStep(key: string): number {
  if (key === 'ArrowRight' || key === 'ArrowDown') return 1
  if (key === 'ArrowLeft' || key === 'ArrowUp') return -1
  return 0
}

export function SegmentedChoice<Value extends string>({
  label,
  options,
  value,
  onChange,
  className = styles.segmented,
  selectedClassName = styles.on,
}: {
  /** What the group as a whole is choosing, for a screen reader. */
  label: string
  options: readonly SegmentOption<Value>[]
  value: Value
  onChange: (value: Value) => void
  className?: string | undefined
  selectedClassName?: string | undefined
}) {
  const group = useRef<HTMLDivElement>(null)

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const step = arrowStep(event.key)
    if (step === 0) return
    event.preventDefault()

    const at = options.findIndex((option) => option.value === value)
    const next = options[(at + step + options.length) % options.length]
    if (next === undefined) return

    onChange(next.value)
    // Focus follows the choice, or the group's single tab stop would be left
    // sitting on the option the reader has just moved away from.
    group.current?.querySelector<HTMLElement>(`[data-value="${next.value}"]`)?.focus()
  }

  return (
    <div
      ref={group}
      role="radiogroup"
      aria-label={label}
      className={className}
      onKeyDown={onKeyDown}
    >
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          data-value={option.value}
          aria-checked={option.value === value}
          tabIndex={option.value === value ? 0 : -1}
          className={option.value === value ? selectedClassName : undefined}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}
