import type { KeyboardEvent } from 'react'

/** Which way an arrow key walks a group that runs left to right only — a tab strip. */
export function horizontalStep(key: string): -1 | 0 | 1 {
  if (key === 'ArrowRight') return 1
  if (key === 'ArrowLeft') return -1
  return 0
}

/** Which way an arrow key walks a radio group: right and down forward, left and up back. */
export function radioStep(key: string): -1 | 0 | 1 {
  if (key === 'ArrowRight' || key === 'ArrowDown') return 1
  if (key === 'ArrowLeft' || key === 'ArrowUp') return -1
  return 0
}

/**
 * Choose the option an arrow key lands on, wrapping at either end, and move
 * focus onto it.
 *
 * Focus moves with the choice rather than staying put: the roving tabindex
 * sits on whichever option is chosen, so leaving focus behind would make the
 * second arrow press do nothing. The options are found as the pressed button's
 * siblings with the same role, which is what the group lays out.
 */
export function walkWithArrows<T>(
  event: KeyboardEvent<HTMLButtonElement>,
  {
    options,
    index,
    step,
    role,
    choose,
  }: {
    options: readonly T[]
    index: number
    step: -1 | 0 | 1
    role: 'radio' | 'tab'
    choose: (option: T) => void
  },
) {
  if (step === 0) return

  event.preventDefault()
  const nextIndex = (index + step + options.length) % options.length
  const next = options[nextIndex]
  if (next === undefined) return

  choose(next)
  const group = event.currentTarget.parentElement
  group?.querySelectorAll<HTMLButtonElement>(`[role="${role}"]`)[nextIndex]?.focus()
}
