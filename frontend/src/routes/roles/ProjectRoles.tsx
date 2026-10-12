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

import { useMutation } from '@tanstack/react-query'
import { useParams } from '@tanstack/react-router'
import { useState } from 'react'
import {
  api,
  type ColumnRule,
  type Permission,
  type RolePermissions,
  type RoleSummary,
  type Sensitivity,
} from '../../api/client'
import {
  queryKeys,
  useIdentity,
  useInvalidate,
  useMembers,
  usePermissionGrid,
  useRoles,
} from '../../api/queries'
import { PageHead } from '../../components/Shell'
import {
  Button,
  EmptyState,
  ErrorBanner,
  ErrorNotice,
  LiveRegion,
  cardStyles,
  useAnnouncer,
} from '../../components/ui'
import {
  inOrder,
  summariseColumns,
  toggled,
  withColumnRule,
  type ColumnRuleField,
} from '../projectPermissions'
import { RoleDetail } from './RoleDetail'
import { AddRoleDialog, DeleteProject, DeleteProjectDialog } from './RoleDialogs'
import styles from './ProjectRoles.module.css'

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
  const invalidate = useInvalidate()
  const [addingRole, setAddingRole] = useState(false)
  const [deletingProject, setDeletingProject] = useState(false)
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

  const identity = useIdentity()
  const members = useMembers(projectKey)
  const roles = useRoles(projectKey)
  const grid = usePermissionGrid(projectKey)

  async function refresh() {
    await invalidate(
      queryKeys.members(projectKey),
      queryKeys.roles(projectKey),
      queryKeys.permissions(projectKey),
    )
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
  const lines = inOrder(grid.data)
  const selectedLine = lines.find((line) => keyOf(line) === chosenKey) ?? lines[0]
  if (selectedLine === undefined) return <EmptyState>This project has no roles.</EmptyState>
  const selectedKey = keyOf(selectedLine)
  const selectedPermissions = pendingPermissions[selectedKey] ?? selectedLine.permissions
  const selectedColumnRules = pendingColumns[selectedKey] ?? selectedLine.columns
  const roleList = roles.data ?? []

  const togglePermission = (entry: Permission, on: boolean) => {
    const next = toggled(selectedPermissions, entry, on)
    setPendingPermissions((current) => ({ ...current, [selectedKey]: next }))
    save.mutate({ line: selectedLine, permissions: next })
  }

  const toggleColumnRule = (columnId: string, field: ColumnRuleField, on: boolean) => {
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

      <ErrorNotice error={save.error} />
      <ErrorNotice error={saveColumns.error} />
      <ErrorNotice error={saveClearance.error} />
      <ErrorNotice error={assignRole.error} />
      <ErrorNotice error={deleteRole.error} />
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
            role={roleList.find((candidate) => candidate.id === selectedLine.role_id)}
            catalogue={grid.data.catalogue}
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
            roles={roleList}
          />
        </div>
      </div>

      {amAdmin ? <DeleteProject onOpen={() => setDeletingProject(true)} /> : null}

      {addingRole ? (
        <AddRoleDialog
          projectKey={projectKey}
          roles={roleList}
          onSaved={announce}
          onDone={refresh}
          onClose={() => setAddingRole(false)}
        />
      ) : null}

      {deletingProject ? (
        <DeleteProjectDialog projectKey={projectKey} onClose={() => setDeletingProject(false)} />
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
      {lines.map((line) => {
        const key = keyOf(line)
        return (
          <button
            key={key}
            type="button"
            className={`${styles.listRow} ${key === selectedKey ? styles.selected : ''}`}
            aria-current={key === selectedKey}
            onClick={() => onSelect(key)}
          >
            <span className={styles.dot} style={{ background: line.colour }} aria-hidden />
            <span className={styles.listName}>{line.name}</span>
            <span className={styles.listCount}>{line.member_count || ''}</span>
          </button>
        )
      })}
    </nav>
  )
}
