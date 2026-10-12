import styles from './ProjectDocs.module.css'

/**
 * A one-character tool — ↑, ↓, ✎, × — named for whoever cannot see the glyph.
 *
 * The label is the accessible name and the hover tip both, because a glyph
 * this small says what it does only to somebody who already knows.
 */
export function IconButton({
  label,
  onClick,
  disabled = false,
  children,
}: {
  label: string
  onClick: () => void
  disabled?: boolean
  children: string
}) {
  return (
    <button
      type="button"
      className={styles.iconButton}
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
    >
      <span aria-hidden="true">{children}</span>
    </button>
  )
}
