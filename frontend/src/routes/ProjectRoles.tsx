/**
 * The screen an admin controls a project from: who is what here, and what
 * each of those things may do.
 *
 * Both halves are on one page because they are one decision. "Aditi is a
 * Reviewer" means nothing until somewhere says what a Reviewer may do, and a
 * grid of permissions means nothing without the names beside it. CYLIST-45
 * put the first half in a dialog off the People page; this is where it lives
 * now, and People links across to it.
 *
 * Everybody can read this page. Only an admin can change anything on it —
 * which is the server's rule, mirrored here so that a reader who cannot act
 * is shown why rather than offered buttons that refuse.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams } from '@tanstack/react-router'
import { useState } from 'react'
import {
  api,
  isMe,
  type Identity,
  type Member,
  type Permission,
  type PermissionInfo,
  type RolePermissions,
  type RoleSummary,
} from '../api/client'
import { Field, Modal, ModalBody } from '../components/Modal'
import { PageHead } from '../components/Shell'
import {
  Avatar,
  Button,
  EmptyState,
  ErrorBanner,
  KindTag,
  LiveRegion,
  RoleTag,
  cardStyles,
  useAnnouncer,
} from '../components/ui'
import {
  countPeople,
  explain,
  inOrder,
  isEveryoneElse,
  isFixed,
  summarise,
  toggled,
} from './projectPermissions'
import styles from './ProjectRoles.module.css'
import { nameIsTaken, whyUndeletable } from './projectRoles'

/** How a line of the grid is addressed, both on the wire and in local state. */
function keyOf(line: RolePermissions): string {
  return line.role_id ?? 'everyone-else'
}

export function ProjectRoles() {
  const { projectKey } = useParams({ from: '/p/$projectKey/roles' })
  const queryClient = useQueryClient()
  const [adding, setAdding] = useState(false)
  const { message, announce } = useAnnouncer()

  /**
   * Rows the reader has just ticked, before the server has answered.
   *
   * Without this a tick box springs back for as long as the round trip takes,
   * which reads as the click not having registered — and the fix somebody
   * reaches for is to click it again.
   */
  const [pending, setPending] = useState<Record<string, Permission[]>>({})

  const identity = useQuery({ queryKey: ['me'], queryFn: api.me })
  const members = useQuery({
    queryKey: ['members', projectKey],
    queryFn: () => api.listMembers(projectKey),
  })
  const roles = useQuery({
    queryKey: ['roles', projectKey],
    queryFn: () => api.listRoles(projectKey),
  })
  const grid = useQuery({
    queryKey: ['permissions', projectKey],
    queryFn: () => api.getPermissions(projectKey),
  })

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['members', projectKey] }),
      queryClient.invalidateQueries({ queryKey: ['roles', projectKey] }),
      queryClient.invalidateQueries({ queryKey: ['permissions', projectKey] }),
    ])
  }

  const save = useMutation({
    mutationFn: ({ line, permissions }: { line: RolePermissions; permissions: Permission[] }) =>
      line.role_id === null
        ? api.setBaselinePermissions(projectKey, permissions)
        : api.setRolePermissions(projectKey, line.role_id, permissions),
    onSuccess: async (saved) => {
      await refresh()
      announce(`${saved.name}: ${saved.permissions.length} of what this project can grant.`)
    },
    // Settled rather than success: a refused save has to put the box back
    // where the server still has it, or the screen keeps claiming a change
    // nobody made.
    onSettled: (_saved, _error, { line }) => {
      setPending((held) => {
        const rest = { ...held }
        delete rest[keyOf(line)]
        return rest
      })
    },
  })

  const setRole = useMutation({
    mutationFn: ({ personId, role }: { personId: string; role: string | null }) =>
      api.setMemberRole(projectKey, personId, role),
    onSuccess: refresh,
  })

  const drop = useMutation({
    mutationFn: (role: RoleSummary) => api.deleteRole(projectKey, role.id),
    onSuccess: async () => {
      await refresh()
      announce('Role deleted.')
    },
  })

  if (members.isPending || grid.isPending) return <EmptyState>Loading roles…</EmptyState>
  if (members.error) return <ErrorBanner>{members.error.message}</ErrorBanner>
  if (grid.error) return <ErrorBanner>{grid.error.message}</ErrorBanner>

  const amAdmin = grid.data.may_manage
  const catalogue = grid.data.catalogue

  return (
    <>
      <PageHead
        title="Roles & permissions"
        actions={
          amAdmin ? (
            <Button variant="go" onClick={() => setAdding(true)}>
              + Add a role
            </Button>
          ) : null
        }
      >
        A role says what somebody is on this board, and what they may do on it. Everybody on a
        project can see this page; only an admin can change it.
      </PageHead>

      {save.error ? <ErrorBanner>{save.error.message}</ErrorBanner> : null}
      {setRole.error ? <ErrorBanner>{setRole.error.message}</ErrorBanner> : null}
      {drop.error ? <ErrorBanner>{drop.error.message}</ErrorBanner> : null}
      <LiveRegion message={message} />

      {!amAdmin ? (
        <p className={styles.note}>
          You are not an admin of this project, so this is a read-only view of how it is set up.
        </p>
      ) : null}

      <h2 className={styles.heading}>Who is what here</h2>
      <People
        members={members.data.members}
        identity={identity.data}
        roles={roles.data ?? []}
        amAdmin={amAdmin}
        onSetRole={(personId, role) => setRole.mutate({ personId, role })}
      />

      <h2 className={styles.heading}>What each role can do</h2>
      <p className={styles.lead}>
        A new role starts with whatever somebody here with no role can already do, so naming
        somebody is never a demotion by accident.
        {/* Said only to the reader it is about: to everybody else it describes
            tick boxes they cannot reach. */}
        {amAdmin ? ' Changes save as you make them.' : null}
      </p>

      <div className={styles.grid}>
        {inOrder(grid.data).map((line) => {
          const held = pending[keyOf(line)] ?? line.permissions
          const role = (roles.data ?? []).find((candidate) => candidate.id === line.role_id)
          const undeletable = role ? whyUndeletable(role) : 'This is not a role anybody holds.'

          return (
            <section key={keyOf(line)} className={`${cardStyles.card} ${styles.role}`}>
              <header className={styles.roleHead}>
                <span className={styles.dot} style={{ background: line.colour }} aria-hidden />
                <b>{line.name}</b>
                <span className={styles.count}>
                  {summarise({ ...line, permissions: held }, catalogue)}
                </span>
                <span className={styles.people}>{countPeople(line.member_count)}</span>
                {/* Not drawn on the admin role at all: it cannot be deleted, and a
                    greyed button explaining that is still a button. */}
                {amAdmin && role && !isEveryoneElse(line) && !isFixed(line) ? (
                  <Button
                    variant="ghost"
                    small
                    danger
                    disabled={undeletable !== null || drop.isPending}
                    title={undeletable ?? undefined}
                    onClick={() => drop.mutate(role)}
                  >
                    Delete
                  </Button>
                ) : null}
              </header>

              {/* One line, not two: the admin role carries a seeded
                  description that says what `explain` says, and printing both
                  reads as a stutter. */}
              {(explain(line) ?? role?.description) ? (
                <p className={styles.why}>{explain(line) ?? role?.description}</p>
              ) : null}

              <ul className={styles.permissions}>
                {catalogue.map((entry) => (
                  <Tick
                    key={entry.key}
                    entry={entry}
                    on={isFixed(line) || held.includes(entry.key)}
                    disabled={!amAdmin || isFixed(line) || save.isPending}
                    onChange={(on) => {
                      const next = toggled(held, entry.key, on)
                      setPending((current) => ({ ...current, [keyOf(line)]: next }))
                      save.mutate({ line, permissions: next })
                    }}
                  />
                ))}
              </ul>
            </section>
          )
        })}
      </div>

      {adding ? (
        <AddRoleDialog
          projectKey={projectKey}
          roles={roles.data ?? []}
          onSaved={announce}
          onDone={refresh}
          onClose={() => setAdding(false)}
        />
      ) : null}
    </>
  )
}

/** One labelled tick box, with the sentence that says what it allows. */
function Tick({
  entry,
  on,
  disabled,
  onChange,
}: {
  entry: PermissionInfo
  on: boolean
  disabled: boolean
  onChange: (on: boolean) => void
}) {
  return (
    <li className={styles.tick}>
      <label>
        <input
          type="checkbox"
          checked={on}
          disabled={disabled}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span>
          <b>{entry.label}</b>
          <span className={styles.summary}>{entry.summary}</span>
        </span>
      </label>
    </li>
  )
}

/** Everybody on the project, and the role each of them wears. */
function People({
  members,
  identity,
  roles,
  amAdmin,
  onSetRole,
}: {
  members: Member[]
  identity: Identity | undefined
  roles: RoleSummary[]
  amAdmin: boolean
  onSetRole: (personId: string, role: string | null) => void
}) {
  if (members.length === 0) return <EmptyState>Nobody is on this project yet.</EmptyState>

  return (
    <ul className={styles.people_list}>
      {members.map((person) => (
        <li key={person.id} className={`${cardStyles.card} ${styles.person}`}>
          <Avatar name={person.name} colour={person.colour} />
          <div className={styles.who}>
            <div className={styles.nameRow}>
              <b>{person.name}</b>
              <KindTag kind={person.kind} />
              {isMe(person, identity) ? <span className={styles.you}>you</span> : null}
            </div>
            <span className={styles.title}>{person.title}</span>
          </div>
          {amAdmin ? (
            <label className={styles.picker}>
              <span className={styles.srOnly}>{person.name}&rsquo;s role</span>
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
          ) : person.role ? (
            <RoleTag role={person.role} />
          ) : (
            <span className={styles.unsaid}>No role</span>
          )}
        </li>
      ))}
    </ul>
  )
}

/** Invent a role for this board. It starts with what the baseline allows. */
function AddRoleDialog({
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
      onClose()
    },
  })

  const taken = nameIsTaken(roles, name)
  const complete = name.trim() !== '' && !taken

  return (
    <Modal
      title="Add a role"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="go" disabled={!complete || add.isPending} onClick={() => add.mutate()}>
            {add.isPending ? 'Adding…' : 'Add role'}
          </Button>
        </>
      }
    >
      <ModalBody>
        {add.error ? <ErrorBanner>{add.error.message}</ErrorBanner> : null}
        <Field label="Name" required>
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
      </ModalBody>
    </Modal>
  )
}
