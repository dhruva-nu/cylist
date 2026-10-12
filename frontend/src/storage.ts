/**
 * Preferences remembered in localStorage, read and written without ever
 * throwing.
 *
 * Every touch of localStorage is wrapped, because reading it throws outright
 * in a Safari private window and in Chrome with third-party cookies blocked —
 * a preference nobody can save is a smaller problem than a page that will not
 * start. So a read that cannot happen answers null, which every caller already
 * treats as "nothing chosen yet", and a write that cannot happen is dropped:
 * the choice still holds for this visit, it just is not remembered.
 */

export function readStored(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

export function writeStored(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    // See above: remembered or not, the choice applies to this visit.
  }
}
