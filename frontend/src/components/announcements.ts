/**
 * Saying something to a screen reader that the screen only shows.
 *
 * A card moving column, a secret revealed, an upload finishing: each is obvious
 * to anyone watching and silent to anyone not. There is one live region for the
 * whole app (`Announcer`), because a region that is added to the page together
 * with its text is not reliably read, and a dozen screens each carrying their
 * own is a dozen places for that to go wrong. Anything may `announce`.
 */

import { useSyncExternalStore } from 'react'

let current = ''
const listeners = new Set<() => void>()

/** Say `text`, politely: after whatever is being read now. */
export function announce(text: string): void {
  // A live region whose text has not changed is not read again, so doing the
  // same thing twice would announce once. The trailing space makes the string
  // new without changing a word of what is spoken.
  current = current === text ? `${text}\u00a0` : text
  listeners.forEach((listener) => listener())
}

/** What the region says now. */
export function currentAnnouncement(): string {
  return current
}

/** Forget what was said. For tests, which share one module between cases. */
export function clearAnnouncement(): void {
  current = ''
  listeners.forEach((listener) => listener())
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useAnnouncement(): string {
  return useSyncExternalStore(subscribe, currentAnnouncement)
}
