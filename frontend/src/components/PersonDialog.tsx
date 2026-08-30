/**
 * Creates a person, or edits one.
 *
 * Shared by the project's People screen and the home screen's "you" card,
 * because they are the same form: the directory is global, and the only thing
 * that changes is what happens around the edges of the save. From inside a
 * project the new person is put on that project straight away — being asked to
 * then go and add them would be silly.
 */

import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type Person, type PersonInput, type PersonKind } from '../api/client'
import { Field, FieldPair, Modal, ModalBody } from './Modal'
import { Button, ErrorBanner } from './ui'
import styles from './PersonDialog.module.css'

const EMPTY: PersonInput = {
  name: '',
  kind: 'team',
  role: '',
  responsibilities: '',
  email: '',
  is_me: false,
}

export function PersonDialog({
  title,
  person,
  projectKey,
  currentMemberIds,
  claimingMe = false,
  onSaved,
  onDone,
  onClose,
}: {
  title: string
  person?: Person
  /** Set to put the new person on that project as well as in the directory. */
  projectKey?: string
  currentMemberIds?: string[]
  /** Open with "this is me" already ticked — the home screen's card does. */
  claimingMe?: boolean
  onSaved: (name: string) => void
  onDone: () => Promise<void>
  onClose: () => void
}) {
  const [form, setForm] = useState<PersonInput>(
    person
      ? {
          name: person.name,
          kind: person.kind,
          role: person.role,
          responsibilities: person.responsibilities,
          email: person.email ?? '',
          is_me: person.is_me,
        }
      : { ...EMPTY, is_me: claimingMe },
  )

  const save = useMutation({
    mutationFn: async (input: PersonInput) => {
      const payload = { ...input, email: input.email?.trim() ? input.email.trim() : null }
      if (person) {
        await api.updatePerson(person.id, payload)
        return payload.name
      }
      const created = await api.createPerson(payload)
      if (projectKey) {
        await api.setMembers(projectKey, [...(currentMemberIds ?? []), created.id])
      }
      return created.name
    },
    onSuccess: async (name) => {
      await onDone()
      onSaved(name)
      onClose()
    },
  })

  const complete = form.name.trim() && form.role.trim() && form.responsibilities.trim()

  return (
    <Modal
      title={title}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="go"
            disabled={save.isPending || !complete}
            onClick={() => save.mutate(form)}
          >
            {save.isPending ? 'Saving…' : person ? 'Save' : 'Add person'}
          </Button>
        </>
      }
    >
      <ModalBody>
        {save.error ? <ErrorBanner>{save.error.message}</ErrorBanner> : null}
        <FieldPair>
          <Field label="Name" required>
            <input
              value={form.name}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
            />
          </Field>
          <Field label="Kind" required>
            <select
              value={form.kind}
              onChange={(event) => setForm({ ...form, kind: event.target.value as PersonKind })}
            >
              <option value="team">Team member</option>
              <option value="client">Client</option>
            </select>
          </Field>
        </FieldPair>
        <Field label="Who is this?" required>
          <input
            value={form.role}
            onChange={(event) => setForm({ ...form, role: event.target.value })}
            placeholder="Finance controller, Atlas"
          />
        </Field>
        <Field
          label="What do they do?"
          required
          hint="What you would tag them about when a task is waiting on somebody."
        >
          <textarea
            value={form.responsibilities}
            onChange={(event) => setForm({ ...form, responsibilities: event.target.value })}
          />
        </Field>
        <Field label="Email">
          <input
            type="email"
            value={form.email ?? ''}
            onChange={(event) => setForm({ ...form, email: event.target.value })}
          />
        </Field>
        <label className={styles.me}>
          <input
            type="checkbox"
            checked={form.is_me ?? false}
            onChange={(event) => setForm({ ...form, is_me: event.target.checked })}
          />
          <span>
            <b>This is me</b>
            <span className={styles.hint}>
              One entry in the directory is you. You are put on every project you create, and
              whoever held it before gives it up.
            </span>
          </span>
        </label>
      </ModalBody>
    </Modal>
  )
}
