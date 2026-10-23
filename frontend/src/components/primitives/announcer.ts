import { announce } from '../announcements'

/**
 * How a screen says something aloud. Hands back the one `announce`, so it is
 * the same function on every render and safe in an effect's dependencies.
 */
export function useAnnouncer(): { announce: typeof announce } {
  return { announce }
}
