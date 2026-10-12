/** Who is on a project, the role each of them holds, and what that role allows. */

import { jsonBody, request } from './http'
import type { Person } from './people'

/**
 * A role on a project — "Reviewer", "QA" — invented by that project's admin.
 *
 * Per project, not per person: the same person can be the Admin of one board
 * and a Reviewer on another. Since CYLIST-46 a role also carries a set of
 * `Permission`s saying what its holders may do here; the admin role holds
 * every one of them by being the admin role, and is the only one that can
 * hand the others out.
 */
export interface Role {
  id: string
  name: string
  description: string
  colour: string
  /** Whether holders of this role may manage the project's roles. */
  is_admin: boolean
}

/** A role as the page an admin manages them from needs it. */
export interface RoleSummary extends Role {
  member_count: number
  created_at: string
}

export interface RoleInput {
  name: string
  description?: string
  colour?: string | null
}

/**
 * One thing a role may allow, from a vocabulary the server fixes.
 *
 * A role's *name* is the admin's invention; what it may do cannot be, because
 * every entry is a fence a particular endpoint recognises. Kept as a union
 * rather than a bare string so that a typo in a permission name is a build
 * error rather than a tick box that silently never matches.
 */
export type Permission =
  | 'tasks'
  | 'comments'
  | 'goals'
  | 'goal_assign'
  | 'goal_owner'
  | 'board'
  | 'files'
  | 'docs'
  | 'vault'
  | 'vault_reveal'
  | 'people'
  | 'agents'
  | 'project'

/**
 * How far something stored on a project may travel.
 *
 * Uploads carry one; roles carry the highest they may read. Ordered:
 * `public` < `internal` < `restricted`.
 */
export type Sensitivity = 'public' | 'internal' | 'restricted'

/** One level, described well enough to draw a picker. */
export interface SensitivityInfo {
  key: Sensitivity
  label: string
  summary: string
}

/**
 * What one role may do at one column of the board.
 *
 * Every column is reported, restricted or not, so the grid draws from this
 * alone. What is *stored* is only the exceptions — see the server's
 * `RoleColumnRule`.
 */
export interface ColumnRule {
  column_id: string
  name: string
  /** Whether a card may be moved into it. */
  may_enter: boolean
  /** Whether the sub-stages a card passes through here may be set. */
  may_stage: boolean
}

/** One permission, described well enough to draw a labelled tick box. */
export interface PermissionInfo {
  key: Permission
  label: string
  summary: string
}

/**
 * One line of the grid: a role, and what it allows.
 *
 * `role_id` is null for the line called "Everyone else" — the project's
 * baseline, which covers everybody on it who has no role and everybody who is
 * not on it at all.
 */
export interface RolePermissions {
  role_id: string | null
  name: string
  colour: string
  is_admin: boolean
  member_count: number
  permissions: Permission[]
  /** One entry per column of the board, in board order. */
  columns: ColumnRule[]
  /** The most sensitive thing this role may read. */
  clearance: Sensitivity
}

/** The whole grid, and the words to draw it with. */
export interface ProjectPermissions {
  catalogue: PermissionInfo[]
  levels: SensitivityInfo[]
  roles: RolePermissions[]
  /** What the reader may do here — what a client hides buttons by. */
  mine: Permission[]
  /**
   * Whether the reader may change any of it.
   *
   * The server's answer rather than one worked out from the member list:
   * three different callers may — the admin role's holders, the bootstrap
   * session, and an `admin`-scoped credential on a project whose last admin
   * was archived — and a client that reimplemented the rule would be wrong
   * about at least one of them.
   */
  may_manage: boolean
}

/**
 * A person as they appear on a project, rather than in the directory.
 *
 * `role` is null until an admin has said what they are, which is what every
 * member starts as.
 */
export interface Member extends Person {
  role: Role | null
}

export const rolesApi = {
  listMembers: (projectKey: string) =>
    request<{ members: Member[] }>(`/projects/${projectKey}/members`),
  setMembers: (projectKey: string, personIds: string[]) =>
    request<{ members: Member[] }>(`/projects/${projectKey}/members`, {
      method: 'PUT',
      body: jsonBody({ person_ids: personIds }),
    }),

  listRoles: (projectKey: string) => request<RoleSummary[]>(`/projects/${projectKey}/roles`),
  createRole: (projectKey: string, input: RoleInput) =>
    request<RoleSummary>(`/projects/${projectKey}/roles`, {
      method: 'POST',
      body: jsonBody(input),
    }),
  deleteRole: (projectKey: string, roleRef: string) =>
    request<void>(`/projects/${projectKey}/roles/${roleRef}`, { method: 'DELETE' }),
  /** `role` is a role id or name; null takes their role off. */
  setMemberRole: (projectKey: string, personId: string, role: string | null) =>
    request<Role | null>(`/projects/${projectKey}/members/${personId}/role`, {
      method: 'PUT',
      body: jsonBody({ role }),
    }),
  getPermissions: (projectKey: string) =>
    request<ProjectPermissions>(`/projects/${projectKey}/permissions`),
  setRolePermissions: (projectKey: string, roleRef: string, permissions: Permission[]) =>
    request<RolePermissions>(`/projects/${projectKey}/roles/${roleRef}/permissions`, {
      method: 'PUT',
      body: jsonBody({ permissions }),
    }),
  /** What somebody here with no role — and anybody not on the project — may do. */
  setBaselinePermissions: (projectKey: string, permissions: Permission[]) =>
    request<RolePermissions>(`/projects/${projectKey}/permissions/everyone-else`, {
      method: 'PUT',
      body: jsonBody({ permissions }),
    }),
  /**
   * Where on the board a role may work. The whole line every time — a column
   * left out is left unrestricted.
   */
  setColumnRules: (projectKey: string, roleId: string | null, columns: ColumnRule[]) =>
    request<RolePermissions>(
      roleId === null
        ? `/projects/${projectKey}/permissions/everyone-else/columns`
        : `/projects/${projectKey}/roles/${roleId}/columns`,
      {
        method: 'PUT',
        body: jsonBody({
          columns: columns.map(({ column_id, may_enter, may_stage }) => ({
            column_id,
            may_enter,
            may_stage,
          })),
        }),
      },
    ),
  /** How sensitive a thing a role may read. */
  setClearance: (projectKey: string, roleId: string | null, clearance: Sensitivity) =>
    request<RolePermissions>(
      roleId === null
        ? `/projects/${projectKey}/permissions/everyone-else/clearance`
        : `/projects/${projectKey}/roles/${roleId}/clearance`,
      { method: 'PUT', body: jsonBody({ clearance }) },
    ),
}
