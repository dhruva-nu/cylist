import styles from './ui.module.css'

/** Initials from a name — "Aditi K" becomes AK. */
export function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => word[0]?.toUpperCase() ?? '')
    .join('')
}

/**
 * Which of the two fixed inks to write on a background, by its brightness.
 *
 * A person's or project's colour is stored per row and can be any hex at all,
 * so assuming white initials will do is how you get a 3:1 avatar. Anything
 * this cannot read — a keyword, a var() — falls back to the light ink, which
 * is what the identity palette is chosen to carry.
 */
export function readableInkOn(colour: string): string {
  const digits = /^#([\da-f]{3}|[\da-f]{6})$/i.exec(colour.trim())?.[1]
  if (digits === undefined) return 'var(--on-accent)'

  const full = digits.length === 3 ? digits.replace(/./g, (digit) => digit + digit) : digits
  const [r = 0, g = 0, b = 0] = [0, 2, 4].map((offset) => {
    const channel = parseInt(full.slice(offset, offset + 2), 16) / 255
    return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
  })
  const luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b

  // 0.19 is where white stops clearing 4.5:1; below it the light ink wins.
  return luminance > 0.19 ? 'var(--on-accent-dark)' : 'var(--on-accent)'
}

export function Avatar({
  name,
  colour,
  large = false,
  small = false,
}: {
  name: string
  colour: string
  large?: boolean
  /** For a row of them, where each face is a hint rather than the subject. */
  small?: boolean
}) {
  return (
    <span
      className={[styles.avatar, large && styles.avatarLarge, small && styles.avatarSmall]
        .filter(Boolean)
        .join(' ')}
      style={{ background: colour, color: readableInkOn(colour) }}
      title={name}
      aria-hidden="true"
    >
      {initials(name)}
    </span>
  )
}
