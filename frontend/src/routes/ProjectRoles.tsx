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
 * **Roles down the left, one role's detail on the right** — the vault's shape,
 * for the vault's reason. A role now carries three different kinds of answer
 * (what it may do, where on the board, how far it may read) and laying every
 * role's three out at once made a page you scrolled to compare two things that
 * were never on screen together. One role at a time is also how the decision
 * is actually made.
 *
 * Who holds a role lives in that role's pane rather than in a directory of its
 * own, which is the other half of the same idea: the list of people with no
 * role is the "Everyone else" line, so nothing is hidden by grouping them.
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
  type ColumnRule,
  type Identity,
  type Member,
  type Permission,
  type PermissionInfo,
  type RolePermissions,
  type RoleSummary,
  type Sensitivity,
  type SensitivityInfo,
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
  cardStyles,
  useAnnouncer,
} from '../components/ui'
import {
  clearanceOf,
  countPeople,
  explain,
  inOrder,
  isEveryoneElse,
  isFixed,
  summarise,
  summariseColumns,
  toggled,
  withColumnRule,
} from './projectPermissions'
import styles from './ProjectRoles.module.css'
import { nameIsTaken, whyUndeletable } from './projectRoles'

/** How a line of the grid is addressed, both on the wire and in local state. */
function keyOf(line: RolePermissions): string {
  return line.role_id ?? 'everyone-else'
}

/** The record without this key: a line's held-back edit, let go of. */
function withoutKey<T>(record: Record<string, T>, key: string): Record<string, T> {
  const rest = { ...record }
  delete rest[key]
  return rest
}

export function ProjectRoles() {
  const { projectKey } = useParams({ from: '/p/$projectKey/roles' })
  const queryClient = useQueryClient()
  const [addingRole, setAddingRole] = useState(false)
  const { message, announce } = useAnnouncer()

  /**
   * Rows the reader has just ticked, before the server has answered.
   *
   * Without this a tick box springs back for as long as the round trip takes,
   * which reads as the click not having registered — and the fix somebody
   * reaches for is to click it again.
   */
  const [pendingPermissions, setPendingPermissions] = useState<Record<string, Permission[]>>({})
  const [pendingColumns, setPendingColumns] = useState<Record<string, ColumnRule[]>>({})

  /**
   * Which line the right-hand pane is about.
   *
   * Null means "whichever is first", resolved below rather than written into
   * state by an effect: the first line is the admin role, a deleted role
   * falls back to it without anything having to notice, and the pane is never
   * about nothing.
   */
  const [chosenKey, setChosenKey] = useState<string | null>(null)

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
      setPendingPermissions((held) => withoutKey(held, keyOf(line)))
    },
  })

  const saveColumns = useMutation({
    mutationFn: ({ line, columns }: { line: RolePermissions; columns: ColumnRule[] }) =>
      api.setColumnRules(projectKey, line.role_id, columns),
    onSuccess: async (saved) => {
      await refresh()
      announce(`${saved.name}: ${summariseColumns(saved)}.`)
    },
    onSettled: (_saved, _error, { line }) => {
      setPendingColumns((held) => withoutKey(held, keyOf(line)))
    },
  })

  const saveClearance = useMutation({
    mutationFn: ({ line, clearance }: { line: RolePermissions; clearance: Sensitivity }) =>
      api.setClearance(projectKey, line.role_id, clearance),
    onSuccess: async (saved) => {
      await refresh()
      announce(`${saved.name} now reads up to ${saved.clearance}.`)
    },
  })

  const assignRole = useMutation({
    mutationFn: ({ personId, role }: { personId: string; role: string | null }) =>
      api.setMemberRole(projectKey, personId, role),
    onSuccess: refresh,
  })

  const deleteRole = useMutation({
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
  const lines = inOrder(grid.data)
  const selectedLine = lines.find((line) => keyOf(line) === chosenKey) ?? lines[0]
  if (selectedLine === undefined) return <EmptyState>This project has no roles.</EmptyState>
  const selectedKey = keyOf(selectedLine)
  const selectedPermissions = pendingPermissions[selectedKey] ?? selectedLine.permissions
  const selectedColumnRules = pendingColumns[selectedKey] ?? selectedLine.columns

  const togglePermission = (entry: Permission, on: boolean) => {
    const next = toggled(selectedPermissions, entry, on)
    setPendingPermissions((current) => ({ ...current, [selectedKey]: next }))
    save.mutate({ line: selectedLine, permissions: next })
  }

  const toggleColumnRule = (columnId: string, field: 'may_enter' | 'may_stage', on: boolean) => {
    const next = withColumnRule(selectedColumnRules, columnId, field, on)
    setPendingColumns((current) => ({ ...current, [selectedKey]: next }))
    saveColumns.mutate({ line: selectedLine, columns: next })
  }

  return (
    <>
      <PageHead
        title="Roles & permissions"
        actions={
          amAdmin ? (
            <Button variant="go" onClick={() => setAddingRole(true)}>
              + Add a role
            </Button>
          ) : null
        }
      >
        A role says what somebody is on this board, and what they may do on it. Everybody on a
        project can see this page; only an admin can change it.
      </PageHead>

      {save.error ? <ErrorBanner>{save.error.message}</ErrorBanner> : null}
      {saveColumns.error ? <ErrorBanner>{saveColumns.error.message}</ErrorBanner> : null}
      {saveClearance.error ? <ErrorBanner>{saveClearance.error.message}</ErrorBanner> : null}
      {assignRole.error ? <ErrorBanner>{assignRole.error.message}</ErrorBanner> : null}
      {deleteRole.error ? <ErrorBanner>{deleteRole.error.message}</ErrorBanner> : null}
      <LiveRegion message={message} />

      {!amAdmin ? (
        <p className={styles.note}>
          You are not an admin of this project, so this is a read-only view of how it is set up.
        </p>
      ) : null}

      <div className={styles.split}>
        <RoleList lines={lines} selectedKey={selectedKey} onSelect={setChosenKey} />

        <div className={`${cardStyles.card} ${styles.pane}`}>
          <RoleDetail
            line={selectedLine}
            role={(roles.data ?? []).find((candidate) => candidate.id === selectedLine.role_id)}
            catalogue={catalogue}
            levels={grid.data.levels}
            members={members.data.members}
            identity={identity.data}
            amAdmin={amAdmin}
            permissions={selectedPermissions}
            columnRules={selectedColumnRules}
            busy={{
              permissions: save.isPending,
              columns: saveColumns.isPending,
              clearance: saveClearance.isPending,
              deleting: deleteRole.isPending,
            }}
            onPermission={togglePermission}
            onColumn={toggleColumnRule}
            onClearance={(clearance) => saveClearance.mutate({ line: selectedLine, clearance })}
            onSetRole={(personId, role) => assignRole.mutate({ personId, role })}
            onDelete={(role) => {
              // Back to the admin line first: the pane is about to be about a
              // role that no longer exists, and falling back silently reads as
              // the delete having done something else.
              setChosenKey(null)
              deleteRole.mutate(role)
            }}
            roles={roles.data ?? []}
          />
        </div>
      </div>

      {addingRole ? (
        <AddRoleDialog
          projectKey={projectKey}
          roles={roles.data ?? []}
          onSaved={announce}
          onDone={refresh}
          onClose={() => setAddingRole(false)}
        />
      ) : null}
    </>
  )
}

/** The roles down the left, one button each, with the selected one marked. */
function RoleList({
  lines,
  selectedKey,
  onSelect,
}: {
  lines: RolePermissions[]
  selectedKey: string
  onSelect: (key: string) => void
}) {
  return (
    <nav className={styles.list} aria-label="Roles on this project">
      {lines.map((line) => (
        <button
          key={keyOf(line)}
          type="button"
          className={`${styles.listRow} ${keyOf(line) === selectedKey ? styles.selected : ''}`}
          aria-current={keyOf(line) === selectedKey}
          onClick={() => onSelect(keyOf(line))}
        >
          <span className={styles.dot} style={{ background: line.colour }} aria-hidden />
          <span className={styles.listName}>{line.name}</span>
          <span className={styles.listCount}>{line.member_count || ''}</span>
        </button>
      ))}
    </nav>
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

/**
 * One role, and everything there is to say about it.
 *
 * The three axes read top to bottom in the order they narrow each other: what
 * this role may do at all, then where on the board it may do the card half of
 * it, then how far it may read. Who holds it comes first, because that is what
 * the reader clicked the name to find out.
 */
function RoleDetail({
  line,
  role,
  roles,
  catalogue,
  levels,
  members,
  identity,
  amAdmin,
  permissions,
  columnRules,
  busy,
  onPermission,
  onColumn,
  onClearance,
  onSetRole,
  onDelete,
}: {
  line: RolePermissions
  /** The role row behind this line, absent for the baseline. */
  role: RoleSummary | undefined
  roles: RoleSummary[]
  catalogue: PermissionInfo[]
  levels: SensitivityInfo[]
  members: Member[]
  identity: Identity | undefined
  amAdmin: boolean
  /** What the role holds, with any tick still on its way to the server. */
  permissions: Permission[]
  /** Where on the board it may work, likewise. */
  columnRules: ColumnRule[]
  busy: { permissions: boolean; columns: boolean; clearance: boolean; deleting: boolean }
  onPermission: (entry: Permission, on: boolean) => void
  onColumn: (columnId: string, field: 'may_enter' | 'may_stage', on: boolean) => void
  onClearance: (level: Sensitivity) => void
  onSetRole: (personId: string, role: string | null) => void
  onDelete: (role: RoleSummary) => void
}) {
  const undeletable = role ? whyUndeletable(role) : null
  const why = explain(line) ?? role?.description
  const holders = members.filter((person) =>
    isEveryoneElse(line) ? person.role === null : person.role?.id === line.role_id,
  )

  return (
    <div className={styles.detail}>
      <header className={styles.detailHead}>
        <div className={styles.detailTitle}>
          <span className={styles.dot} style={{ background: line.colour }} aria-hidden />
          <h2>{line.name}</h2>
          <span className={styles.count}>{summarise({ ...line, permissions }, catalogue)}</span>
        </div>
        {/* Not drawn on the admin role or the baseline: neither can be deleted,
            and a greyed button explaining that is still a button. */}
        {amAdmin && role && !isEveryoneElse(line) && !isFixed(line) ? (
          <Button
            variant="ghost"
            small
            danger
            disabled={undeletable !== null || busy.deleting}
            title={undeletable ?? undefined}
            onClick={() => onDelete(role)}
          >
            Delete role
          </Button>
        ) : null}
      </header>

      {/* One line, not two: the admin role carries a seeded description that
          says what `explain` says, and printing both reads as a stutter. */}
      {why ? <p className={styles.why}>{why}</p> : null}

      <section className={styles.axis}>
        <header>
          <b>Who holds it</b>
          <span className={styles.count}>{countPeople(line.member_count)}</span>
        </header>
        <Holders
          holders={holders}
          identity={identity}
          roles={roles}
          amAdmin={amAdmin}
          baseline={isEveryoneElse(line)}
          onSetRole={onSetRole}
        />
      </section>

      {/* Two columns rather than one long scroll. The twelve tick boxes are
          the tall thing and the two narrowing axes are the short ones, so
          stacking all three put the clearance a screen and a half below the
          permission it qualifies. Side by side they are visible together,
          which is how the decision is actually made. */}
      <div className={styles.axes}>
        <section className={styles.axis}>
          <header>
            <b>What it can do</b>
          </header>
          <ul className={styles.permissions}>
            {catalogue.map((entry) => (
              <Tick
                key={entry.key}
                entry={entry}
                on={isFixed(line) || permissions.includes(entry.key)}
                disabled={!amAdmin || isFixed(line) || busy.permissions}
                onChange={(on) => onPermission(entry.key, on)}
              />
            ))}
          </ul>
        </section>

        {/* Both of these narrow what the tick boxes beside them allowed —
            they never widen it — so they sit in the second column, under a
            heading that says which permission each is about. */}
        <div className={styles.narrowing}>
          <Workflow
            columnRules={columnRules}
            summary={summariseColumns({ ...line, columns: columnRules })}
            disabled={!amAdmin || isFixed(line) || busy.columns}
            gated={!isFixed(line) && !permissions.includes('tasks')}
            onChange={onColumn}
          />

          <Clearance
            levels={levels}
            value={clearanceOf(line)}
            disabled={!amAdmin || isFixed(line) || busy.clearance}
            onChange={onClearance}
          />
        </div>
      </div>
    </div>
  )
}

/**
 * The people on this line, and the control that moves one off it.
 *
 * The same `select` the People page used to carry, grouped by what somebody
 * is rather than listed flat. Changing it here moves that person to another
 * line of the sidebar, which is the whole of what "say what they are" means.
 */
function Holders({
  holders,
  identity,
  roles,
  amAdmin,
  baseline,
  onSetRole,
}: {
  holders: Member[]
  identity: Identity | undefined
  roles: RoleSummary[]
  amAdmin: boolean
  /** Whether this is the "Everyone else" line, which nobody is *given*. */
  baseline: boolean
  onSetRole: (personId: string, role: string | null) => void
}) {
  if (holders.length === 0) {
    return (
      <p className={styles.why}>
        {baseline
          ? 'Everybody on this project has been given a role.'
          : 'Nobody holds this role yet. Give it to somebody from their own line.'}
      </p>
    )
  }

  return (
    <ul className={styles.holders}>
      {holders.map((person) => (
        <li key={person.id}>
          <Avatar name={person.name} colour={person.colour} />
          <span className={styles.who}>
            <b>{person.name}</b>
            <span className={styles.title}>{person.title}</span>
          </span>
          <KindTag kind={person.kind} />
          {isMe(person, identity) ? <span className={styles.you}>you</span> : null}
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
          ) : null}
        </li>
      ))}
    </ul>
  )
}

/**
 * Where on the board this role may work.
 *
 * Two ticks per column and they are different jobs: moving a card into Review
 * is work, while deciding that a Hotfix in Review passes through "Drafted,
 * Reviewed, Merged" is designing the workflow — and a team often wants the
 * second in one person's hands while everybody does the first.
 */
function Workflow({
  columnRules,
  summary,
  disabled,
  gated,
  onChange,
}: {
  columnRules: ColumnRule[]
  summary: string
  disabled: boolean
  /** Whether the flat `tasks` permission is off, which makes all of this moot. */
  gated: boolean
  onChange: (columnId: string, field: 'may_enter' | 'may_stage', on: boolean) => void
}) {
  if (columnRules.length === 0) return null

  return (
    <section className={styles.axis}>
      <header>
        <b>Where on the board</b>
        <span className={styles.count}>{gated ? 'No cards at all' : summary}</span>
      </header>
      {gated ? (
        <p className={styles.why}>
          This role cannot change cards, so where on the board it may do so does not arise.
        </p>
      ) : (
        <table className={styles.columns}>
          <thead>
            <tr>
              <th scope="col">Column</th>
              <th scope="col">Move cards in</th>
              <th scope="col">Set sub-stages</th>
            </tr>
          </thead>
          <tbody>
            {columnRules.map((column) => (
              <tr key={column.column_id}>
                <th scope="row">{column.name}</th>
                <td>
                  <input
                    type="checkbox"
                    checked={column.may_enter}
                    disabled={disabled}
                    aria-label={`Move cards into ${column.name}`}
                    onChange={(event) =>
                      onChange(column.column_id, 'may_enter', event.target.checked)
                    }
                  />
                </td>
                <td>
                  <input
                    type="checkbox"
                    checked={column.may_stage}
                    disabled={disabled}
                    aria-label={`Set the sub-stages for ${column.name}`}
                    onChange={(event) =>
                      onChange(column.column_id, 'may_stage', event.target.checked)
                    }
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}

/**
 * How sensitive a thing this role may read.
 *
 * A radio group rather than a select: there are three, they are ordered, and
 * the whole point of the control is seeing at a glance which way along that
 * order a role sits.
 */
function Clearance({
  levels,
  value,
  disabled,
  onChange,
}: {
  levels: SensitivityInfo[]
  value: Sensitivity
  disabled: boolean
  onChange: (level: Sensitivity) => void
}) {
  if (levels.length === 0) return null

  return (
    <section className={styles.axis}>
      <header>
        <b>Uploads it can read</b>
        <span className={styles.count}>up to {value}</span>
      </header>
      <ul className={styles.levels}>
        {levels.map((level) => (
          <li key={level.key}>
            <label>
              <input
                type="radio"
                checked={value === level.key}
                disabled={disabled}
                onChange={() => onChange(level.key)}
              />
              <span>
                <b>{level.label}</b>
                <span className={styles.summary}>{level.summary}</span>
              </span>
            </label>
          </li>
        ))}
      </ul>
    </section>
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
