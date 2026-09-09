/**
 * Formatting shared by the screens that list uploads.
 *
 * Files and skills are both listings of things somebody uploaded — a name, a
 * size and a date — so the two screens agree on how a size reads rather than
 * each carrying its own loop over the units. Dates that mean a *deadline* are
 * a different question and live in `dates.ts`; these are timestamps, which
 * only ever need to say roughly when.
 */

const UNITS = ['bytes', 'KB', 'MB', 'GB', 'TB']

/**
 * A byte count as the nearest sensible unit.
 *
 * One decimal below 10 and none above, so a column of sizes lines up at a
 * readable width: "9.4 MB" and "312 KB" rather than "9.44 MB" and "312.4 KB".
 * Null reads as an em dash — a link's size is not zero, it is unknown.
 */
export function formatSize(bytes: number | null): string {
  if (bytes === null) return '—'
  let size = bytes
  let unit = 0
  while (size >= 1024 && unit < UNITS.length - 1) {
    size /= 1024
    unit += 1
  }
  return `${unit === 0 ? size : size.toFixed(size < 10 ? 1 : 0)} ${UNITS[unit]}`
}

/** A timestamp as a day — "8 Sep". The year is left off as noise. */
export function formatStamp(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}
