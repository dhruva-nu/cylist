/**
 * Creates a person, or edits one.
 *
 * Shared by the project's People screen and the home screen's "you" card,
 * because they are the same form: the directory is global, and the only thing
 * that changes is what happens around the edges of the save. From inside a
 * project the new person is put on that project straight away — being asked to
 * then go and add them would be silly.
 *
 * Being in the directory and being able to sign in are separate facts, and
 * this form only sets the first. Nobody gets an account by having a row typed
 * for them; they get one by accepting an invitation, which is a button on the
 * People screen and a password only they ever see.
 */

import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type Person, type PersonInput, type PersonKind } from '../api/client'
import { Field, FieldPair, Modal, ModalBody } from './Modal'
import { Button, ErrorBanner } from './ui'

const EMPTY_PERSON: PersonInput = {
  name: '',
  kind: 'team',
  title: '',
  responsibilities: '',
  email: '',
}

export function PersonDialog({
  title,
  person,
  projectKey,
  currentMemberIds,
  onSaved,
  onDone,
  onClose,
}: {
  title: string
  person?: Person
  /** Set to put the new person on that project as well as in the directory. */
  projectKey?: string
  currentMemberIds?: string[]
  onSaved: (name: string) => void
  onDone: () => Promise<void>
  onClose: () => void
}) {
  const [form, setForm] = useState<PersonInput>(
    person
      ? {
          name: person.name,
          kind: person.kind,
          title: person.title,
          responsibilities: person.responsibilities,
          email: person.email ?? '',
        }
      : EMPTY_PERSON,
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

  const complete = form.name.trim() && form.title.trim() && form.responsibilities.trim()

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
            {saveButtonLabel(save.isPending, person)}
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
            value={form.title}
            onChange={(event) => setForm({ ...form, title: event.target.value })}
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
        <Field
          label="Email"
          hint={
            form.kind === 'team'
              ? 'Also what they sign in with, once you invite them.'
              : 'Clients are named on the work, not signed in to it.'
          }
        >
          <input
            type="email"
            value={form.email ?? ''}
            onChange={(event) => setForm({ ...form, email: event.target.value })}
          />
        </Field>
      </ModalBody>
    </Modal>
  )
}

/** What the Save button says it will do. */
function saveButtonLabel(saving: boolean, person: Person | undefined): string {
  if (saving) return 'Saving…'
  if (person) return 'Save'
  return 'Add person'
}
