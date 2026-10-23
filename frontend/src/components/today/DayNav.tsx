import { localToday, shiftDay } from '../dates'
import { Button } from '../ui'
import styles from '../Today.module.css'

/** Which day is on screen: back, forward, a date, and back to today. */
export function DayNav({ day, onDay }: { day: string; onDay: (day: string) => void }) {
  const today = localToday()
  return (
    <div className={styles.dayNav}>
      <Button
        small
        variant="ghost"
        aria-label="The day before"
        onClick={() => onDay(shiftDay(day, -1))}
      >
        ‹
      </Button>
      <label className={styles.dayField}>
        <span className="visually-hidden">Which day to plan</span>
        <input
          type="date"
          value={day}
          onChange={(event) => onDay(event.currentTarget.value || today)}
        />
      </label>
      <Button
        small
        variant="ghost"
        aria-label="The day after"
        onClick={() => onDay(shiftDay(day, 1))}
      >
        ›
      </Button>
      <Button small variant="ghost" disabled={day === today} onClick={() => onDay(today)}>
        Today
      </Button>
    </div>
  )
}
