import type { Person } from '../../api/client'
import { MentionBox } from '../Mentions'
import { Button } from '../ui'
import styles from './SubStatusListEditor.module.css'

/** How many stages a card may be divided into. */
const MOST_STAGES = 4

/**
 * The stage list, edited by hand: renamed in place, reordered, removed, added.
 *
 * Order is the axis the board's slider slides along, so moving a stage is a
 * real edit rather than delete-and-retype. The current stage travels with its
 * label — move "Review" up and the card is still on "Review" — which is why
 * this owns the marker as well as the words.
 */
export function SubStatusListEditor({
  value,
  current,
  members,
  onChange,
}: {
  value: string[]
  current: number
  members: Person[]
  onChange: (value: string[], current: number) => void
}) {
  /** Swap two neighbours, taking the marker along if it is on one of them. */
  const swap = (a: number, b: number) => {
    const next = [...value]
    next[a] = value[b] as string
    next[b] = value[a] as string

    let marker = current
    if (current === a) marker = b
    else if (current === b) marker = a
    onChange(next, marker)
  }

  const remove = (index: number) => {
    const next = value.filter((_, position) => position !== index)
    // The marker follows what is left: a stage taken from behind it pulls it
    // back one, and taking the current stage leaves the marker on whatever
    // has moved up into its place — or on the new last stage if nothing has.
    const lastStage = Math.max(next.length - 1, 0)
    const marker = index < current ? current - 1 : Math.min(current, lastStage)
    onChange(next, marker)
  }

  const rename = (index: number, text: string) => {
    onChange(
      value.map((label, position) => (position === index ? text : label)),
      current,
    )
  }

  return (
    <div className={styles.subStatusEditor}>
      {value.map((label, index) => (
        // Keyed by position, not by the label: the label is what is being
        // typed, so keying by it would remount the box on every keystroke.
        <div key={index} className={styles.subStatusEditorRow}>
          <span
            className={`${styles.subStatusEditorNumber} ${
              index === current ? styles.subStatusEditorNow : ''
            }`}
            title={index === current ? 'The stage this card is on' : `Stage ${index + 1}`}
          >
            {index + 1}
          </span>
          {/* People but not files: a stage is a step, and a step named after
              a document would be a link on every board card that reached it.
              A file belongs in the description that explains the step. */}
          <MentionBox
            value={label}
            members={members}
            className={styles.grow}
            maxLength={60}
            placeholder={`Stage ${index + 1}`}
            aria-label={`Sub-status stage ${index + 1}`}
            onChange={(text) => rename(index, text)}
          />
          <Button
            variant="ghost"
            small
            disabled={index === 0}
            aria-label={`Move stage ${index + 1} up`}
            title="Move up"
            onClick={() => swap(index, index - 1)}
          >
            ↑
          </Button>
          <Button
            variant="ghost"
            small
            disabled={index === value.length - 1}
            aria-label={`Move stage ${index + 1} down`}
            title="Move down"
            onClick={() => swap(index, index + 1)}
          >
            ↓
          </Button>
          <Button
            variant="ghost"
            small
            aria-label={`Remove stage ${index + 1}`}
            title="Remove"
            onClick={() => remove(index)}
          >
            ×
          </Button>
        </div>
      ))}
      {value.length < MOST_STAGES ? (
        <Button variant="ghost" small onClick={() => onChange([...value, ''], current)}>
          + Add stage
        </Button>
      ) : null}
    </div>
  )
}
