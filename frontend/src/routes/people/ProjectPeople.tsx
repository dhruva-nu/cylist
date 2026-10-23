/**
 * A project's people.
 *
 * The directory is global — a client who works on three projects is one entry
 * — so this screen does two things: it shows who is on *this* project, and it
 * lets you pull anyone from the directory onto it.
 */

import { useMutation } from '@tanstack/react-query'
import { Link, useParams } from '@tanstack/react-router'
import { useState } from 'react'
import { api, type Member, type Person } from '../../api/client'
import { queryKeys, useIdentity, useInvalidate, useMembers } from '../../api/queries'
import { AccountDialog } from '../../components/AccountDialog'
import { InviteDialog } from '../../components/InviteDialog'
import { PersonDialog } from '../../components/PersonDialog'
import { PageHead } from '../../components/Shell'
import { Button, EmptyState, ErrorBanner, ErrorNotice, useAnnouncer } from '../../components/ui'
import { usePermissions } from '../usePermissions'
import { DirectoryDialog } from './DirectoryDialog'
import { PeopleGroup } from './PeopleGroup'
import styles from './ProjectPeople.module.css'
import { handlesOwnErrors } from '../../api/mutationErrors'

export function ProjectPeople() {
  const { projectKey } = useParams({ from: '/p/$projectKey/people' })
  const invalidate = useInvalidate()
  const [addingPerson, setAddingPerson] = useState(false)
  const [choosingFromDirectory, setChoosingFromDirectory] = useState(false)
  const [editing, setEditing] = useState<Person | null>(null)
  const [inviting, setInviting] = useState<Person | null>(null)
  const [settingPasswordFor, setSettingPasswordFor] = useState<Person | null>(null)
  const { announce } = useAnnouncer()

  // Who is looking. Already in the cache — the auth gate asked for it before
  // this screen existed — so reading it here costs nothing and is what makes
  // "you" mean the reader rather than one flagged row in the directory.
  const identity = useIdentity()
  const members = useMembers(projectKey)

  // What the reader may do here. Membership is one permission of its own —
  // saying who is on a board is a different job from renaming it.
  const may = usePermissions(projectKey)

  async function refresh() {
    await invalidate(
      queryKeys.members(projectKey),
      queryKeys.projects,
      queryKeys.people,
      queryKeys.permissions(projectKey),
    )
  }

  const removeFromProject = useMutation({
    meta: handlesOwnErrors,
    mutationFn: (leaving: Member) => {
      const staying = (members.data?.members ?? []).filter((person) => person.id !== leaving.id)
      return api.setMembers(
        projectKey,
        staying.map((person) => person.id),
      )
    },
    onSuccess: async (_result, leaving) => {
      announce(`${leaving.name} is no longer on this project.`)
      await refresh()
    },
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
                <Button variant="go" onClick={() => setAddingPerson(true)}>
                  + Add a person
                </Button>
                <Button onClick={() => setChoosingFromDirectory(true)}>From the directory</Button>
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

      <ErrorNotice error={removeFromProject.error} />

      <PeopleGroup
        title="Team"
        people={team}
        identity={identity.data}
        mayRemove={maySayWhoIsHere}
        onEdit={setEditing}
        onInvite={setInviting}
        onOpenAccount={setSettingPasswordFor}
        onRemove={removeFromProject.mutate}
      />
      <PeopleGroup
        title="Clients"
        people={clients}
        identity={identity.data}
        mayRemove={maySayWhoIsHere}
        onEdit={setEditing}
        onInvite={setInviting}
        onOpenAccount={setSettingPasswordFor}
        onRemove={removeFromProject.mutate}
      />

      {addingPerson ? (
        <PersonDialog
          title="Add a person"
          projectKey={projectKey}
          onSaved={(name) => announce(`${name} is now on this project.`)}
          currentMemberIds={members.data.members.map((person) => person.id)}
          onDone={refresh}
          onClose={() => setAddingPerson(false)}
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

      {settingPasswordFor ? (
        <AccountDialog
          person={settingPasswordFor}
          onSaved={(name) => announce(`${name} can sign in now.`)}
          onDone={refresh}
          onClose={() => setSettingPasswordFor(null)}
        />
      ) : null}

      {choosingFromDirectory ? (
        <DirectoryDialog
          projectKey={projectKey}
          currentMemberIds={members.data.members.map((person) => person.id)}
          onSaved={(count) =>
            announce(
              `Membership saved. ${count} ${count === 1 ? 'person is' : 'people are'} on this project.`,
            )
          }
          onDone={refresh}
          onClose={() => setChoosingFromDirectory(false)}
        />
      ) : null}
    </>
  )
}
