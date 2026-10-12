import type { Board } from '../../api/client'
import { usePopover } from './usePopover'
import styles from './BoardToolbar.module.css'

/**
 * The board's shape, behind one button: its card templates, and another
 * column. Both are pressed once or twice in a board's life, and two buttons
 * for them on the toolbar every day was a toolbar mostly about setup.
 *
 * The column item says how many of the board's columns are used, and at the
 * limit greys rather than goes: this is where the limit is explained, and a
 * missing item cannot say why it is missing.
 */
export function BoardMenu({
  board,
  templateCount,
  onTemplates,
  onAddColumn,
}: {
  board: Board
  templateCount: number
  onTemplates: () => void
  onAddColumn: () => void
}) {
  const { open, setOpen, root, button } = usePopover()
  const used = board.columns.length
  const full = used >= board.max_columns

  return (
    <div className={styles.popRoot} ref={root}>
      <button
        ref={button}
        type="button"
        className={`${styles.toolButton} ${open ? styles.toolButtonOpen : ''}`}
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen(!open)}
      >
        Board
        <span aria-hidden="true">⌄</span>
      </button>
      {open ? (
        <div className={`${styles.popover} ${styles.menuPopover}`} role="menu" aria-label="Board">
          <button
            type="button"
            role="menuitem"
            className={styles.menuItem}
            onClick={() => {
              setOpen(false)
              onTemplates()
            }}
          >
            <span className={styles.menuText}>
              Card templates
              <span className={styles.menuHint}>
                {templateCount === 1 ? '1 saved' : `${templateCount} saved`}
              </span>
            </span>
          </button>
          <button
            type="button"
            role="menuitem"
            className={styles.menuItem}
            // aria-disabled rather than disabled, so it can still be reached
            // and read: see the note on the component.
            aria-disabled={full}
            onClick={() => {
              if (full) return
              setOpen(false)
              onAddColumn()
            }}
          >
            <span className={styles.menuText}>
              Add a column
              <span className={styles.menuHint}>
                {full
                  ? `All ${board.max_columns} used — a board holds ${board.max_columns} columns`
                  : `${used} of ${board.max_columns} used`}
              </span>
              <span className={styles.meter} aria-hidden="true">
                {Array.from({ length: board.max_columns }, (_, index) => (
                  <i key={index} className={index < used ? styles.meterOn : undefined} />
                ))}
              </span>
            </span>
          </button>
        </div>
      ) : null}
    </div>
  )
}
