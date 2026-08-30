/**
 * A project's people.
 *
 * The directory is global — a client who works on three projects is one entry
 * — so this screen does two things: it shows who is on *this* project, and it
 * lets you pull anyone from the directory onto it.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams } from '@tanstack/react-router'
import { useState } from 'react'
import { api, type Person, type PersonInput, type PersonKind } from '../api/client'
import { Field, FieldPair, Modal, ModalBody } from '../components/Modal'
import { PageHead } from '../components/Shell'
import { Avatar, Button, EmptyState, ErrorBanner, KindTag, cardStyles } from '../components/ui'
import styles from './ProjectPeople.module.css'

export function ProjectPeople() {
  const { projectKey } = useParams({ from: '/p/$projectKey/people' })
  const queryClient = useQueryClient()
  const [adding, setAdding] = useState(false)
  const [choosing, setChoosing] = useState(false)
  const [editing, setEditing] = useState<Person | null>(null)

  const members = useQuery({
    queryKey: ['members', projectKey],
    queryFn: () => api.listMembers(projectKey),
  })

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['members', projectKey] }),
      queryClient.invalidateQueries({ queryKey: ['project-summary', projectKey] }),
      queryClient.invalidateQueries({ queryKey: ['projects'] }),
      queryClient.invalidateQueries({ queryKey: ['people'] }),
    ])
  }

  const remove = useMutation({
    mutationFn: (personId: string) =>
      api.setMembers(
        projectKey,
        (members.data?.members ?? []).filter((p) => p.id !== personId).map((p) => p.id),
      ),
    onSuccess: refresh,
  })

  if (members.isPending) return <EmptyState>Loading people…</EmptyState>
  if (members.error) return <ErrorBanner>{members.error.message}</ErrorBanner>

  const team = members.data.members.filter((person) => person.kind === 'team')
  const clients = members.data.members.filter((person) => person.kind === 'client')

  return (
    <>
      <PageHead
        title="People"
        actions={
          <>
            <Button variant="go" onClick={() => setAdding(true)}>
              + Add a person
            </Button>
            <Button onClick={() => setChoosing(true)}>From the directory</Button>
          </>
        }
      >
        Who is involved and what they do. When a task goes on hold or gets blocked, you can tag the
        person it is waiting on.
      </PageHead>

      {remove.error ? <ErrorBanner>{remove.error.message}</ErrorBanner> : null}

      <Group title="Team" people={team} onEdit={setEditing} onRemove={remove.mutate} />
      <Group title="Clients" people={clients} onEdit={setEditing} onRemove={remove.mutate} />

      {adding ? (
        <PersonDialog
          title="Add a person"
          projectKey={projectKey}
          currentMemberIds={members.data.members.map((person) => person.id)}
          onDone={refresh}
          onClose={() => setAdding(false)}
        />
      ) : null}

      {editing ? (
        <PersonDialog
          title="Edit person"
          person={editing}
          onDone={refresh}
          onClose={() => setEditing(null)}
        />
      ) : null}

      {choosing ? (
        <DirectoryDialog
          projectKey={projectKey}
          currentMemberIds={members.data.members.map((person) => person.id)}
          onDone={refresh}
          onClose={() => setChoosing(false)}
        />
      ) : null}
    </>
  )
}

function Group({
  title,
  people,
  onEdit,
  onRemove,
}: {
  title: string
  people: Person[]
  onEdit: (person: Person) => void
  onRemove: (personId: string) => void
}) {
  return (
    <>
      <div className={styles.section}>
        <h2>{title}</h2>
        <span>{people.length}</span>
      </div>
      {people.length === 0 ? (
        <EmptyState>Nobody here yet.</EmptyState>
      ) : (
        <div className={styles.grid}>
          {people.map((person) => (
            <div key={person.id} className={`${cardStyles.card} ${styles.person}`}>
              <Avatar name={person.name} colour={person.colour} large />
              <div className={styles.details}>
                <div className={styles.nameRow}>
                  <b>{person.name}</b>
                  <KindTag kind={person.kind} />
                </div>
                <span className={styles.role}>{person.role}</span>
                <div className={styles.responsibilities}>{person.responsibilities}</div>
                <div className={styles.contact}>
                  {person.email ? <span>✉ {person.email}</span> : null}
                  <Button variant="ghost" small onClick={() => onEdit(person)}>
                    Edit
                  </Button>
                  <Button variant="ghost" small danger onClick={() => onRemove(person.id)}>
                    Remove
                  </Button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  )
}

const EMPTY: PersonInput = {
  name: '',
  kind: 'team',
  role: '',
  responsibilities: '',
  email: '',
}

/**
 * Creates a person, or edits one.
 *
 * When creating from inside a project, the new person is put on that project
 * straight away — being asked to then go and add them would be silly.
 */
function PersonDialog({
  title,
  person,
  projectKey,
  currentMemberIds,
  onDone,
  onClose,
}: {
  title: string
  person?: Person
  projectKey?: string
  currentMemberIds?: string[]
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
        }
      : EMPTY,
  )

  const save = useMutation({
    mutationFn: async (input: PersonInput) => {
      const payload = { ...input, email: input.email?.trim() ? input.email.trim() : null }
      if (person) {
        await api.updatePerson(person.id, payload)
        return
      }
      const created = await api.createPerson(payload)
      if (projectKey) {
        await api.setMembers(projectKey, [...(currentMemberIds ?? []), created.id])
      }
    },
    onSuccess: async () => {
      await onDone()
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
      </ModalBody>
    </Modal>
  )
}

/** Pick who from the directory is on this project. */
function DirectoryDialog({
  projectKey,
  currentMemberIds,
  onDone,
  onClose,
}: {
  projectKey: string
  currentMemberIds: string[]
  onDone: () => Promise<void>
  onClose: () => void
}) {
  const [selected, setSelected] = useState<string[]>(currentMemberIds)
  const directory = useQuery({ queryKey: ['people'], queryFn: api.listPeople })

  const save = useMutation({
    mutationFn: () => api.setMembers(projectKey, selected),
    onSuccess: async () => {
      await onDone()
      onClose()
    },
  })

  function toggle(personId: string) {
    setSelected((current) =>
      current.includes(personId) ? current.filter((id) => id !== personId) : [...current, personId],
    )
  }

  return (
    <Modal
      title="Who is on this project?"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="go" disabled={save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? 'Saving…' : 'Save members'}
          </Button>
        </>
      }
    >
      <ModalBody>
        {save.error ? <ErrorBanner>{save.error.message}</ErrorBanner> : null}
        {directory.isPending ? <p>Loading the directory…</p> : null}
        {directory.data?.length === 0 ? (
          <p>
            The directory is empty. Close this and use <strong>Add a person</strong> instead.
          </p>
        ) : null}
        {directory.data?.length ? (
          <div className={styles.picker}>
            {directory.data.map((person) => (
              <label key={person.id} className={styles.option}>
                <input
                  type="checkbox"
                  checked={selected.includes(person.id)}
                  onChange={() => toggle(person.id)}
                />
                <Avatar name={person.name} colour={person.colour} />
                <span className={styles.optionText}>
                  <b>{person.name}</b>
                  <span>
                    {person.kind} · {person.role}
                  </span>
                </span>
              </label>
            ))}
          </div>
        ) : null}
      </ModalBody>
    </Modal>
  )
}
