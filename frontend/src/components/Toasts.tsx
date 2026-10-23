import { dismissToast, useToasts } from './toasts'
import styles from './Toasts.module.css'

/**
 * Where `showToast`'s messages appear: a stack in the corner, spoken as they
 * arrive. One alert region that is always mounted, because a region added to the
 * page together with its text is not reliably announced.
 */
export function Toasts() {
  const toasts = useToasts()
  return (
    <div className={styles.stack} role="alert" aria-live="assertive">
      {toasts.map((toast) => (
        <p key={toast.id} className={styles.toast}>
          <span>{toast.text}</span>
          <button
            type="button"
            className={styles.dismiss}
            aria-label="Dismiss"
            onClick={() => dismissToast(toast.id)}
          >
            ×
          </button>
        </p>
      ))}
    </div>
  )
}
