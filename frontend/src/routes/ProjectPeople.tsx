/**
 * A project's people.
 *
 * The directory is global — a client who works on three projects is one entry
 * — so this screen does two things: it shows who is on *this* project, and it
 * lets you pull anyone from the directory onto it.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from '@tanstack/react-router'
import { useState } from 'react'
import { api, isMe, type Identity, type Member, type Person } from '../api/client'
import { AccountDialog } from '../components/AccountDialog'
import { InviteDialog } from '../components/InviteDialog'
import { Modal, ModalBody } from '../components/Modal'
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
import { usePermissions } from './usePermissions'

export function ProjectPeople() {
  const { projectKey } = useParams({ from: '/p/$projectKey/people' })
  const queryClient = useQueryClient()
  const [adding, setAdding] = useState(false)
  const [choosing, setChoosing] = useState(false)
  const [editing, setEditing] = useState<Person | null>(null)
  const [inviting, setInviting] = useState<Person | null>(null)
  const [opening, setOpening] = useState<Person | null>(null)
  const { message, announce } = useAnnouncer()

  // Who is looking. Already in the cache — the auth gate asked for it before
  // this screen existed — so reading it here costs nothing and is what makes
  // "you" mean the reader rather than one flagged row in the directory.
  const identity = useQuery({ queryKey: ['me'], queryFn: api.me })

  const members = useQuery({
    queryKey: ['members', projectKey],
    queryFn: () => api.listMembers(projectKey),
  })

  // What the reader may do here. Membership is one permission of its own —
  // saying who is on a board is a different job from renaming it.
  const may = usePermissions(projectKey)

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['members', projectKey] }),
      queryClient.invalidateQueries({ queryKey: ['project-summary', projectKey] }),
      queryClient.invalidateQueries({ queryKey: ['projects'] }),
      queryClient.invalidateQueries({ queryKey: ['people'] }),
      queryClient.invalidateQueries({ queryKey: ['permissions', projectKey] }),
    ])
  }

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
  const maySayWhoIsHere = may('people')

  return (
    <>
      <PageHead
        title="People"
        actions={
          <>
            {/* A button that always refuses is worse than one that is not
                there, so both of these are drawn only for somebody whose role
                lets them say who is on this board. */}
            {maySayWhoIsHere ? (
              <>
                <Button variant="go" onClick={() => setAdding(true)}>
                  + Add a person
                </Button>
                <Button onClick={() => setChoosing(true)}>From the directory</Button>
              </>
            ) : null}
            {/* Readable by everybody: a badge nobody can look up is a badge
                nobody can read. What can be changed there is the server's
                business, and that page says so. */}
            <Link to="/p/$projectKey/roles" params={{ projectKey }} className={styles.rolesLink}>
              Roles &amp; permissions
            </Link>
          </>
        }
      >
        Who is involved and what they do. When a task goes on hold or gets blocked, you can tag the
        person it is waiting on.
      </PageHead>

      {remove.error ? <ErrorBanner>{remove.error.message}</ErrorBanner> : null}
      <LiveRegion message={message} />

      <Group
        title="Team"
        people={team}
        identity={identity.data}
        mayRemove={maySayWhoIsHere}
        onEdit={setEditing}
        onInvite={setInviting}
        onOpenAccount={setOpening}
        onRemove={remove.mutate}
      />
      <Group
        title="Clients"
        people={clients}
        identity={identity.data}
        mayRemove={maySayWhoIsHere}
        onEdit={setEditing}
        onInvite={setInviting}
        onOpenAccount={setOpening}
        onRemove={remove.mutate}
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

      {opening ? (
        <AccountDialog
          person={opening}
          onSaved={(name) => announce(`${name} can sign in now.`)}
          onDone={refresh}
          onClose={() => setOpening(null)}
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
  mayRemove,
  onEdit,
  onInvite,
  onOpenAccount,
  onRemove,
}: {
  title: string
  people: Member[]
  identity: Identity | undefined
  mayRemove: boolean
  onEdit: (person: Person) => void
  onInvite: (person: Person) => void
  onOpenAccount: (person: Person) => void
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
                  {isMe(person, identity) ? <span className={styles.you}>you</span> : null}
                  {person.is_agent ? (
                    <span
                      className={styles.agent}
                      title="Cylist's own machine. It is given work like anybody else, and never signs in."
                    >
                      agent
                    </span>
                  ) : null}
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
                  {/* Clients are named on the work, not signed in to it,
                      the agent is a machine and does not sign in at all, and
                      somebody who already has an account has nothing to
                      accept — so the button is only offered where it would
                      do something. */}
                  {person.kind === 'team' &&
                  !person.is_agent &&
                  !person.has_account &&
                  !person.archived_at ? (
                    <Button variant="ghost" small onClick={() => onInvite(person)}>
                      {person.invite_is_pending ? 'Re-invite' : 'Invite'}
                    </Button>
                  ) : null}
                  {/* The other door, offered on the same row and second: an
                      invitation is the one that leaves the password with
                      nobody but them, so it reads first. This one is still
                      here for somebody with no mailbox you can reach, and is
                      the only way back in for somebody who has lost the
                      password they already set. */}
                  {person.kind === 'team' && !person.is_agent && !person.archived_at ? (
                    <Button variant="ghost" small onClick={() => onOpenAccount(person)}>
                      {person.has_account ? 'Reset password' : 'Set a password'}
                    </Button>
                  ) : null}
                  {mayRemove ? (
                    <Button variant="ghost" small danger onClick={() => onRemove(person.id)}>
                      Remove
                    </Button>
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
