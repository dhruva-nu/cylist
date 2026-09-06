/**
 * Starts a goal, or edits one.
 *
 * The colour is the field that earns its place here. Everything else about a
 * goal can be read; the colour is the only part of it that appears on the
 * board, on every card written under it, so it is chosen from the eight
 * accents the rest of the interface is drawn from rather than from a colour
 * wheel — a board with a hand-picked magenta rail down one column of cards is
 * a board that has stopped looking like one thing.
 */

import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type Goal, type GoalInput, type GoalStatus, type Person } from '../api/client'
import { GOAL_STATUS_LABELS } from './GoalMarks'
import { Field, FieldPair, Modal, ModalBody } from './Modal'
import { Button, ErrorBanner, readableInkOn } from './ui'
import styles from './GoalDialog.module.css'

/**
 * The goal palette, matching `GOAL_PALETTE` in `app/core/palette.py`.
 *
 * Repeated here rather than fetched because it is a design decision rather
 * than data: these are the accents the interface is drawn from, and a client
 * that had to ask the server which colours it may draw with would be asking
 * about its own stylesheet.
 *
 * Its own set rather than the identity palette people and projects draw from
 * — a goal's colour is never written as text, only worn as a rail, a dot, a
 * fill, so it is free of the AA-for-white-initials debt that set carries, and
 * pushed a good deal louder for it.
 */
const PALETTE = [
  '#1FAA5C',
  '#2F6FEB',
  '#E8890B',
  '#8B5CF6',
  '#0D9488',
  '#DB2777',
  '#E5484D',
  '#0891B2',
] as const

const STATUSES: GoalStatus[] = ['open', 'achieved', 'dropped']

export function GoalDialog({
  projectKey,
  goal,
  members,
  announce,
  onDone,
  onClose,
  onDeleted,
}: {
  projectKey: string
  /** The goal being edited, or null to start one. */
  goal: Goal | null
  /** The project's people — a goal's owner is one of them. */
  members: Person[]
  announce: (message: string) => void
  onDone: () => Promise<void>
  onClose: () => void
  /** Where to go once the goal is gone; the goal's own page leaves for the list. */
  onDeleted?: () => void
}) {
  const [form, setForm] = useState<GoalInput>(
    goal
      ? {
          name: goal.name,
          description: goal.description,
          colour: goal.colour,
          target_date: goal.target_date,
          owner_id: goal.owner.id,
          status: goal.status,
        }
      : {
          name: '',
          description: '',
          // Not pre-picked from the palette: an unchosen colour is filled in
          // by the server from the name, which is a better guess than the
          // first swatch and stays stable if the goal is renamed and remade.
          target_date: null,
          owner_id: members.find((person) => person.is_me)?.id ?? members[0]?.id ?? '',
        },
  )
  const [confirmingDelete, setConfirmingDelete] = useState(false)

  const save = useMutation({
    mutationFn: async (input: GoalInput) => {
      const payload = {
        ...input,
        target_date: input.target_date?.trim() ? input.target_date : null,
      }
      if (goal) return api.updateGoal(goal.reference, payload)
      return api.createGoal(projectKey, payload)
    },
    onSuccess: async (saved) => {
      await onDone()
      announce(goal ? `${saved.name} saved.` : `${saved.name} started as ${saved.reference}.`)
      onClose()
    },
  })

  const remove = useMutation({
    mutationFn: () => api.deleteGoal(goal!.reference),
    onSuccess: async () => {
      await onDone()
      announce(`${goal!.name} deleted. Its cards are still on the board.`)
      onClose()
      onDeleted?.()
    },
  })

  const named = form.name.trim().length > 0
  const owned = form.owner_id.length > 0

  return (
    <Modal
      title={goal ? `Edit ${goal.reference}` : 'New goal'}
      onClose={onClose}
      footer={
        <>
          {goal ? (
            <Button
              danger
              disabled={remove.isPending}
              onClick={() => (confirmingDelete ? remove.mutate() : setConfirmingDelete(true))}
            >
              {confirmingDelete ? 'Delete — its cards stay' : 'Delete'}
            </Button>
          ) : null}
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="go"
            disabled={save.isPending || !named || !owned}
            onClick={() => save.mutate(form)}
          >
            {save.isPending ? 'Saving…' : goal ? 'Save' : 'Start goal'}
          </Button>
        </>
      }
    >
      <ModalBody>
        {save.error ? <ErrorBanner>{save.error.message}</ErrorBanner> : null}
        {remove.error ? <ErrorBanner>{remove.error.message}</ErrorBanner> : null}

        <Field label="Name" required hint="What it is called on every card written under it.">
          <input
            value={form.name}
            onChange={(event) => setForm({ ...form, name: event.target.value })}
            placeholder="Search revamp"
          />
        </Field>

        <Field label="What does reaching it mean?">
          <textarea
            value={form.description ?? ''}
            onChange={(event) => setForm({ ...form, description: event.target.value })}
            placeholder="Filters, sorting and saved searches, on every list in the product."
          />
        </Field>

        <Field
          label="Colour"
          hint="The rail down the left of every card on this goal. Left unchosen, one is picked from the name."
        >
          <div className={styles.swatches}>
            {PALETTE.map((colour) => {
              const chosen = form.colour?.toUpperCase() === colour
              return (
                <button
                  key={colour}
                  type="button"
                  className={`${styles.swatch} ${chosen ? styles.chosen : ''}`}
                  style={{ background: colour, color: readableInkOn(colour) }}
                  aria-pressed={chosen}
                  aria-label={colour}
                  onClick={() => setForm({ ...form, colour })}
                >
                  {chosen ? '✓' : ''}
                </button>
              )
            })}
          </div>
        </Field>

        <FieldPair>
          <Field label="Owner" required hint="Who is answerable for the whole of it.">
            <select
              value={form.owner_id}
              onChange={(event) => setForm({ ...form, owner_id: event.target.value })}
            >
              {members.map((person) => (
                <option key={person.id} value={person.id}>
                  {person.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Target date" hint="Leave it empty rather than inventing one.">
            <input
              type="date"
              value={form.target_date ?? ''}
              onChange={(event) => setForm({ ...form, target_date: event.target.value || null })}
            />
          </Field>
        </FieldPair>

        {goal ? (
          <Field
            label="Status"
            hint="A goal cannot be achieved while a card on it is still open. Dropping one can happen at any time."
          >
            <select
              value={form.status ?? 'open'}
              onChange={(event) => setForm({ ...form, status: event.target.value as GoalStatus })}
            >
              {STATUSES.map((status) => (
                <option key={status} value={status}>
                  {GOAL_STATUS_LABELS[status]}
                </option>
              ))}
            </select>
          </Field>
        ) : null}
      </ModalBody>
    </Modal>
  )
}
