import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type BoardColumn, type ColumnInput } from '../../api/client'
import { DialogActions, Field, Modal, ModalBody } from '../../components/Modal'
import { Button, ErrorNotice } from '../../components/ui'
import styles from './ColumnDialog.module.css'
import { handlesOwnErrors } from '../../api/mutationErrors'

/** Creates a column, or renames and deletes one. */
export function ColumnDialog({
  projectKey,
  column,
  canDelete,
  isLast,
  maxOutcomes,
  announce,
  onDone,
  onClose,
}: {
  projectKey: string
  /** Null to add a new column. */
  column: BoardColumn | null
  canDelete: boolean
  /**
   * Whether this is the board's last column, which is the only one that can be
   * divided into outcomes. A new column is added to the right, so it will be —
   * but it is not one yet, and offering the sections before the column exists
   * is asking about a thing that has nowhere to go.
   */
  isLast: boolean
  maxOutcomes: number
  announce: (message: string) => void
  onDone: () => Promise<void>
  onClose: () => void
}) {
  const [form, setForm] = useState<ColumnInput>({
    name: column?.name ?? '',
    description: column?.description ?? '',
    outcomes: column?.outcomes ?? [],
  })
  const outcomes = form.outcomes ?? []

  const save = useMutation({
    meta: handlesOwnErrors,
    mutationFn: (input: ColumnInput) =>
      column ? api.updateColumn(column.id, input) : api.createColumn(projectKey, input),
    onSuccess: async (saved) => {
      announce(column ? `Column renamed to ${saved.name}.` : `Column ${saved.name} added.`)
      await onDone()
      onClose()
    },
  })

  const remove = useMutation({
    meta: handlesOwnErrors,
    mutationFn: (existing: BoardColumn) => api.deleteColumn(existing.id),
    onSuccess: async (_deleted, existing) => {
      announce(`Column ${existing.name} deleted.`)
      await onDone()
      onClose()
    },
  })

  const complete =
    form.name.trim() && form.description.trim() && outcomes.every((label) => label.trim())

  function updateForm(changes: Partial<ColumnInput>) {
    setForm((current) => ({ ...current, ...changes }))
  }

  return (
    <Modal
      title={column ? 'Edit column' : 'New column'}
      onClose={onClose}
      footer={
        <DialogActions
          submitLabel={column ? 'Save' : 'Add column'}
          pendingLabel="Saving…"
          pending={save.isPending}
          disabled={!complete}
          onSubmit={() => save.mutate(form)}
          onCancel={onClose}
          note={
            column ? (
              <Button
                variant="ghost"
                danger
                className={styles.spacer}
                disabled={remove.isPending}
                onClick={() => remove.mutate(column)}
              >
                Delete column
              </Button>
            ) : null
          }
        />
      }
    >
      <ModalBody>
        <ErrorNotice error={save.error ?? remove.error} />
        {column && !canDelete ? (
          <p className={styles.note}>
            A board keeps at least two columns, so this one has to stay.
          </p>
        ) : null}
        <Field label="Name" required>
          <input
            value={form.name}
            maxLength={80}
            onChange={(event) => updateForm({ name: event.target.value })}
            placeholder="Review"
          />
        </Field>
        <Field label="Description" required hint="What belongs here, so it does not drift.">
          <textarea
            value={form.description}
            onChange={(event) => updateForm({ description: event.target.value })}
            placeholder="Waiting on PR review or QA"
          />
        </Field>

        {/* Only at the end of the board. An outcome is how a piece of work
            ended, and nothing ends in the middle of one. */}
        {column && isLast ? (
          <OutcomesField
            saved={column.outcomes}
            outcomes={outcomes}
            maxOutcomes={maxOutcomes}
            onChange={(next) => updateForm({ outcomes: next })}
          />
        ) : null}
      </ModalBody>
    </Modal>
  )
}

/** What an outcome box suggests while it is empty, by its place in the list. */
const OUTCOME_PLACEHOLDERS = ['Done', 'Cancelled', 'In prod']

/**
 * The sections the board's last column is divided into, as a list to edit:
 * a box per section, a × to take one away, and a button to add another.
 */
function OutcomesField({
  saved,
  outcomes,
  maxOutcomes,
  onChange,
}: {
  /** The sections as the server has them, before this edit. */
  saved: string[]
  outcomes: string[]
  maxOutcomes: number
  onChange: (outcomes: string[]) => void
}) {
  return (
    <Field
      label="Outcomes"
      hint={`How work can end here — up to ${maxOutcomes} sections this column is divided into. Leave it empty to draw no distinction.`}
    >
      <div className={styles.outcomes}>
        {outcomes.map((label, index) => (
          // Keyed by position, not by the label: the label is what is being
          // typed, so keying by it would remount the box on every keystroke.
          <div key={index} className={styles.outcomeRow}>
            <span className={styles.outcomeNumber}>{index + 1}</span>
            <input
              value={label}
              maxLength={40}
              className={styles.grow}
              aria-label={`Outcome ${index + 1}`}
              placeholder={OUTCOME_PLACEHOLDERS[index] ?? 'Outcome'}
              onChange={(event) =>
                onChange(outcomes.map((one, at) => (at === index ? event.target.value : one)))
              }
            />
            <Button
              small
              variant="ghost"
              aria-label={`Remove outcome ${index + 1}`}
              onClick={() => onChange(outcomes.filter((_, at) => at !== index))}
            >
              ×
            </Button>
          </div>
        ))}
        {outcomes.length < maxOutcomes ? (
          <Button small variant="ghost" onClick={() => onChange([...outcomes, ''])}>
            + Add an outcome
          </Button>
        ) : null}
        {/* Said here rather than left to the refusal: the cards are
            already in the sections, and what becomes of them is the
            thing anybody shortening the list is actually asking. */}
        {saved.length > outcomes.length ? (
          <p className={styles.note}>
            Cards in a section you have taken away move to the last one still standing.
          </p>
        ) : null}
      </div>
    </Field>
  )
}
