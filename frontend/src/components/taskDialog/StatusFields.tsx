/**
 * The status control, and what a stall has to carry.
 *
 * Choosing On hold or Blocked reveals a reason box and a picker of people to
 * tag, and neither the Save button nor the server will let the change through
 * without a reason — a red card that does not say why is a question rather
 * than information.
 */

import { useState } from 'react'
import type { Person, TaskDetail, TaskStatus } from '../../api/client'
import { Field } from '../Modal'
import { STATUSES } from '../taskLabels'
import { Avatar } from '../ui'
import { radioStep, walkWithArrows } from './arrowWalk'
import shared from './shared.module.css'
import styles from './StatusFields.module.css'

/**
 * The status control's state: the status chosen, and the reason and the people
 * a stall has to carry.
 *
 * Held apart from the card's fields because a status is not one of them: it is
 * sent as a request of its own, after the fields, and only when it has changed.
 */
export function useStatusChange(task: TaskDetail | null) {
  const previous = task?.status ?? 'active'
  const [status, setStatus] = useState<TaskStatus>(previous)
  const [reason, setReason] = useState('')
  const [tagged, setTagged] = useState<string[]>(task?.waiting_on.map((person) => person.id) ?? [])

  const changing = status !== previous
  const stalling = changing && status !== 'active'

  function toggleTag(personId: string) {
    setTagged((current) =>
      current.includes(personId) ? current.filter((id) => id !== personId) : [...current, personId],
    )
  }

  return {
    status,
    setStatus,
    reason,
    setReason,
    tagged,
    toggleTag,
    changing,
    stalling,
    /** Whether the card is going on hold or blocked with no reason given. */
    reasonMissing: stalling && !reason.trim(),
  }
}

/**
 * The status control: one choice of four, as a radio group.
 *
 * Arrow keys walk it, as they do any radio group, and focus moves with the
 * choice — see `walkWithArrows`.
 */
export function StatusField({
  status,
  onChange,
}: {
  status: TaskStatus
  onChange: (status: TaskStatus) => void
}) {
  return (
    <Field label="Status">
      <div className={styles.segmented} role="radiogroup" aria-label="Status">
        {STATUSES.map((option, index) => (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={status === option.value}
            // One stop for the whole group, on whichever option is chosen:
            // Tab should pass a four-way choice, not visit it four times.
            tabIndex={status === option.value ? 0 : -1}
            className={status === option.value ? `${styles.on} ${styles[option.value]}` : ''}
            onClick={() => onChange(option.value)}
            onKeyDown={(event) =>
              walkWithArrows(event, {
                options: STATUSES,
                index,
                step: radioStep(event.key),
                role: 'radio',
                choose: (next) => onChange(next.value),
              })
            }
          >
            {option.label}
          </button>
        ))}
      </div>
    </Field>
  )
}

/**
 * Why the card is going on hold or blocked, and who it is now waiting on.
 *
 * Shown the moment either is chosen, because neither the Save button nor the
 * server will let the change through without a reason.
 */
export function StallFields({
  status,
  reason,
  onReason,
  tagged,
  onToggleTag,
  members,
}: {
  status: TaskStatus
  reason: string
  onReason: (reason: string) => void
  /** The ids of the people picked as what the card is waiting on. */
  tagged: string[]
  onToggleTag: (personId: string) => void
  members: Person[]
}) {
  return (
    <div className={`${styles.why} ${styles[status]}`}>
      <Field label="Why is the status changing?" required>
        <textarea
          className={styles.reason}
          value={reason}
          onChange={(event) => onReason(event.target.value)}
          placeholder="Required when moving to On hold or Blocked"
        />
      </Field>
      <Field label="Who is this waiting on?">
        <div className={styles.tagPicker} role="group" aria-label="Who is this waiting on?">
          {members.map((person) => (
            <button
              key={person.id}
              type="button"
              className={tagged.includes(person.id) ? styles.tagged : ''}
              aria-pressed={tagged.includes(person.id)}
              onClick={() => onToggleTag(person.id)}
            >
              <Avatar name={person.name} colour={person.colour} />
              {person.name.split(' ')[0]}
              <span className={shared.aside}>· {person.title.split(',')[0]}</span>
            </button>
          ))}
        </div>
      </Field>
      <small>
        Added to the card&rsquo;s comments and shown on the board as &ldquo;Waiting on @name&rdquo;.
      </small>
    </div>
  )
}
