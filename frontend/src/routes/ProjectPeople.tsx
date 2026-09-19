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
import { api, isMe, type Identity, type Member, type Person, type RoleSummary } from '../api/client'
import { InviteDialog } from '../components/InviteDialog'
import { Field, Modal, ModalBody } from '../components/Modal'
import { PersonDialog } from '../components/PersonDialog'
import { PageHead } from '../components/Shell'
import {
  Avatar,
  Button,
  EmptyState,
  ErrorBanner,
  KindTag,
  LiveRegion,
  MailIcon,
  RoleTag,
  cardStyles,
  useAnnouncer,
} from '../components/ui'
import styles from './ProjectPeople.module.css'
import { isProjectAdmin, nameIsTaken, whyUndeletable } from './projectRoles'

export function ProjectPeople() {
  const { projectKey } = useParams({ from: '/p/$projectKey/people' })
  const queryClient = useQueryClient()
  const [adding, setAdding] = useState(false)
  const [choosing, setChoosing] = useState(false)
  const [editing, setEditing] = useState<Person | null>(null)
  const [inviting, setInviting] = useState<Person | null>(null)
  const [managingRoles, setManagingRoles] = useState(false)
  const { message, announce } = useAnnouncer()

  // Who is looking. Already in the cache — the auth gate asked for it before
  // this screen existed — so reading it here costs nothing and is what makes
  // "you" mean the reader rather than one flagged row in the directory.
  const identity = useQuery({ queryKey: ['me'], queryFn: api.me })

  const members = useQuery({
    queryKey: ['members', projectKey],
    queryFn: () => api.listMembers(projectKey),
  })

  // Read by everyone, not only admins: the picker on each card needs the list
  // to draw, and a badge nobody can look up is a badge nobody can read.
  const roles = useQuery({
    queryKey: ['roles', projectKey],
    queryFn: () => api.listRoles(projectKey),
  })

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['members', projectKey] }),
      queryClient.invalidateQueries({ queryKey: ['project-summary', projectKey] }),
      queryClient.invalidateQueries({ queryKey: ['projects'] }),
      queryClient.invalidateQueries({ queryKey: ['people'] }),
      queryClient.invalidateQueries({ queryKey: ['roles', projectKey] }),
    ])
  }

  const setRole = useMutation({
    mutationFn: ({ personId, role }: { personId: string; role: string | null }) =>
      api.setMemberRole(projectKey, personId, role),
    onSuccess: refresh,
  })

  const remove = useMutation({
    mutationFn: (personId: string) => {
      const going = (members.data?.members ?? []).find((person) => person.id === personId)
      return api
        .setMembers(
          projectKey,
          (members.data?.members ?? []).filter((p) => p.id !== personId).map((p) => p.id),
        )
        .then((result) => {
          announce(`${going?.name ?? 'That person'} is no longer on this project.`)
          return result
        })
    },
    onSuccess: refresh,
  })

  if (members.isPending) return <EmptyState>Loading people…</EmptyState>
  if (members.error) return <ErrorBanner>{members.error.message}</ErrorBanner>

  const team = members.data.members.filter((person) => person.kind === 'team')
  const clients = members.data.members.filter((person) => person.kind === 'client')
  const amAdmin = isProjectAdmin(members.data.members, identity.data)

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
            {/* Only an admin can act on this, and a button that always
                refuses is worse than one that is not there. */}
            {amAdmin ? <Button onClick={() => setManagingRoles(true)}>Roles</Button> : null}
          </>
        }
      >
        Who is involved and what they do. When a task goes on hold or gets blocked, you can tag the
        person it is waiting on.
      </PageHead>

      {remove.error ? <ErrorBanner>{remove.error.message}</ErrorBanner> : null}
      {setRole.error ? <ErrorBanner>{setRole.error.message}</ErrorBanner> : null}
      <LiveRegion message={message} />

      <Group
        title="Team"
        people={team}
        identity={identity.data}
        roles={roles.data ?? []}
        amAdmin={amAdmin}
        onEdit={setEditing}
        onInvite={setInviting}
        onRemove={remove.mutate}
        onSetRole={(personId, role) => {
          setRole.mutate({ personId, role })
        }}
      />
      <Group
        title="Clients"
        people={clients}
        identity={identity.data}
        roles={roles.data ?? []}
        amAdmin={amAdmin}
        onEdit={setEditing}
        onInvite={setInviting}
        onRemove={remove.mutate}
        onSetRole={(personId, role) => {
          setRole.mutate({ personId, role })
        }}
      />

      {adding ? (
        <PersonDialog
          title="Add a person"
          projectKey={projectKey}
          onSaved={(name) => announce(`${name} is now on this project.`)}
          currentMemberIds={members.data.members.map((person) => person.id)}
          onDone={refresh}
          onClose={() => setAdding(false)}
        />
      ) : null}

      {editing ? (
        <PersonDialog
          title="Edit person"
          person={editing}
          onSaved={(name) => announce(`${name} saved.`)}
          onDone={refresh}
          onClose={() => setEditing(null)}
        />
      ) : null}

      {inviting ? (
        <InviteDialog
          person={inviting}
          onSaved={(name) => announce(`Invitation ready for ${name}.`)}
          onDone={refresh}
          onClose={() => setInviting(null)}
        />
      ) : null}

      {managingRoles ? (
        <RolesDialog
          projectKey={projectKey}
          roles={roles.data ?? []}
          onSaved={announce}
          onDone={refresh}
          onClose={() => setManagingRoles(false)}
        />
      ) : null}

      {choosing ? (
        <DirectoryDialog
          projectKey={projectKey}
          currentMemberIds={members.data.members.map((person) => person.id)}
          onSaved={(count) =>
            announce(
              `Membership saved. ${count} ${count === 1 ? 'person is' : 'people are'} on this project.`,
            )
          }
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
  identity,
  roles,
  amAdmin,
  onEdit,
  onInvite,
  onRemove,
  onSetRole,
}: {
  title: string
  people: Member[]
  identity: Identity | undefined
  roles: RoleSummary[]
  amAdmin: boolean
  onEdit: (person: Person) => void
  onInvite: (person: Person) => void
  onRemove: (personId: string) => void
  onSetRole: (personId: string, role: string | null) => void
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
                  {isMe(person, identity) ? <span className={styles.you}>you</span> : null}
                  {person.role ? <RoleTag role={person.role} /> : null}
                  <AccountTag person={person} />
                </div>
                <span className={styles.title}>{person.title}</span>
                <div className={styles.responsibilities}>{person.responsibilities}</div>
                <div className={styles.contact}>
                  {person.email ? (
                    <span className={styles.email}>
                      <MailIcon />
                      <span>{person.email}</span>
                    </span>
                  ) : null}
                  <Button variant="ghost" small onClick={() => onEdit(person)}>
                    Edit
                  </Button>
                  {/* Clients are named on the work, not signed in to it, and
                      somebody who already has an account has nothing to
                      accept — so the button is only offered where it would
                      do something. */}
                  {person.kind === 'team' && !person.has_account && !person.archived_at ? (
                    <Button variant="ghost" small onClick={() => onInvite(person)}>
                      {person.invite_is_pending ? 'Re-invite' : 'Invite'}
                    </Button>
                  ) : null}
                  <Button variant="ghost" small danger onClick={() => onRemove(person.id)}>
                    Remove
                  </Button>
                  {amAdmin ? (
                    <label className={styles.rolePicker}>
                      <span className={styles.srOnly}>{person.name}'s role</span>
                      <select
                        value={person.role?.id ?? ''}
                        onChange={(event) => onSetRole(person.id, event.target.value || null)}
                      >
                        <option value="">No role</option>
                        {roles.map((role) => (
                          <option key={role.id} value={role.id}>
                            {role.name}
                          </option>
                        ))}
                      </select>
                    </label>
                  ) : null}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  )
}

/**
 * Where a project's admin invents its roles.
 *
 * A list and one form, rather than a dialog per role: the whole point of the
 * screen is seeing what a board already calls people before adding another
 * word for the same thing.
 */
function RolesDialog({
  projectKey,
  roles,
  onSaved,
  onDone,
  onClose,
}: {
  projectKey: string
  roles: RoleSummary[]
  onSaved: (message: string) => void
  onDone: () => Promise<void>
  onClose: () => void
}) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')

  const add = useMutation({
    mutationFn: () =>
      api.createRole(projectKey, { name: name.trim(), description: description.trim() }),
    onSuccess: async (role) => {
      await onDone()
      onSaved(`${role.name} is now a role on this project.`)
      setName('')
      setDescription('')
    },
  })

  const drop = useMutation({
    mutationFn: (role: RoleSummary) => api.deleteRole(projectKey, role.id),
    onSuccess: async () => {
      await onDone()
      onSaved('Role deleted.')
    },
  })

  const taken = nameIsTaken(roles, name)
  const complete = name.trim() !== '' && !taken

  return (
    <Modal title="Roles" onClose={onClose} footer={<Button onClick={onClose}>Done</Button>}>
      <ModalBody>
        {add.error ? <ErrorBanner>{add.error.message}</ErrorBanner> : null}
        {drop.error ? <ErrorBanner>{drop.error.message}</ErrorBanner> : null}
        <p className={styles.lead}>
          What people are on this board. A role says who somebody is; it does not change what they
          can do.
        </p>

        <div className={styles.roleList}>
          {roles.map((role) => {
            const why = whyUndeletable(role)
            return (
              <div key={role.id} className={styles.roleRow}>
                <RoleTag role={role} />
                <span className={styles.roleCount}>
                  {role.member_count === 1 ? '1 person' : `${role.member_count} people`}
                </span>
                <Button
                  variant="ghost"
                  small
                  danger
                  disabled={why !== null || drop.isPending}
                  title={why ?? undefined}
                  onClick={() => drop.mutate(role)}
                >
                  Delete
                </Button>
              </div>
            )
          })}
        </div>

        <Field label="Add a role" required>
          <input
            value={name}
            maxLength={40}
            placeholder="QA"
            onChange={(event) => setName(event.target.value)}
          />
        </Field>
        {taken ? <p className={styles.clash}>This project already has a {name.trim()}.</p> : null}
        <Field label="What it means here" hint="Optional.">
          <input value={description} onChange={(event) => setDescription(event.target.value)} />
        </Field>
        <Button variant="go" disabled={!complete || add.isPending} onClick={() => add.mutate()}>
          {add.isPending ? 'Adding…' : 'Add role'}
        </Button>
      </ModalBody>
    </Modal>
  )
}

/** Pick who from the directory is on this project. */
function DirectoryDialog({
  projectKey,
  currentMemberIds,
  onSaved,
  onDone,
  onClose,
}: {
  projectKey: string
  currentMemberIds: string[]
  onSaved: (count: number) => void
  onDone: () => Promise<void>
  onClose: () => void
}) {
  const [selected, setSelected] = useState<string[]>(currentMemberIds)
  const directory = useQuery({ queryKey: ['people'], queryFn: api.listPeople })

  const save = useMutation({
    mutationFn: () => api.setMembers(projectKey, selected),
    onSuccess: async (result) => {
      await onDone()
      onSaved(result.members.length)
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
                    {person.kind} · {person.title}
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

/** Whether this person can sign in, said in a word next to their name. */
function AccountTag({ person }: { person: Person }) {
  if (person.has_account) {
    return (
      <span className={styles.account} title="Can sign in as themselves.">
        account
      </span>
    )
  }
  if (person.invite_is_pending) {
    return (
      <span className={styles.invited} title="Invited; has not set a password yet.">
        invited
      </span>
    )
  }
  return null
}
