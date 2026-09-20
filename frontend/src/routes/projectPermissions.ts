/**
 * What the control screen needs to know about permissions, kept out of the TSX.
 *
 * The screen is one grid — roles down the side, permissions across the top —
 * and almost everything interesting about it is a question about one line or
 * one cell. Those answers live here so they can be tested without a DOM, and
 * so the component is left doing nothing but drawing.
 *
 * Whether the *reader* may change any of it is still `isProjectAdmin` in
 * `./projectRoles`, for the reason given there: it is a fact about this board,
 * and the member list is where the server already said it.
 */

import type {
  ColumnRule,
  Permission,
  PermissionInfo,
  ProjectPermissions,
  RolePermissions,
  Sensitivity,
} from '../api/client'

/**
 * Whether the reader may do this here.
 *
 * Read off `mine`, which the server computes — an admin's role, a narrowed
 * role, or the baseline for somebody nobody has described. A client must not
 * try to work that out from the grid itself: it would have to know the three
 * rules, and it would be wrong about the fourth.
 */
export function can(mine: Permission[] | undefined, permission: Permission): boolean {
  // Undefined while the query is in flight. Permissive on purpose: a button
  // that flickers out of existence on every page load is worse than one that
  // refuses once, and the server is the fence either way.
  return mine === undefined || mine.includes(permission)
}

/** The baseline line — everybody here with no role, and everybody not here. */
export function isEveryoneElse(line: RolePermissions): boolean {
  return line.role_id === null
}

/**
 * Whether this line can be ticked at all.
 *
 * The admin role's cannot: it holds everything by being the admin role, and a
 * screen that let somebody untick "Membership" on it would be a screen for
 * locking a board. The server refuses it too — this is what stops the box
 * looking as though it would have worked.
 */
export function isFixed(line: RolePermissions): boolean {
  return line.is_admin
}

/** The result of ticking or unticking one box, as the whole row to send. */
export function toggled(
  permissions: Permission[],
  permission: Permission,
  on: boolean,
): Permission[] {
  const without = permissions.filter((held) => held !== permission)
  return on ? [...without, permission].sort() : without
}

/**
 * What a line's count reads as: "Everything", "Nothing", or "4 of 10".
 *
 * Counted against the catalogue the server sent rather than against a number
 * compiled in here, so a Cylist that learns an eleventh permission does not
 * start telling everybody they have everything.
 */
export function summarise(line: RolePermissions, catalogue: PermissionInfo[]): string {
  if (isFixed(line)) return 'Everything'
  if (line.permissions.length === 0) return 'Nothing'
  if (line.permissions.length >= catalogue.length) return 'Everything'
  return `${line.permissions.length} of ${catalogue.length}`
}

/** How many people a line applies to, said the way a sentence would. */
export function countPeople(count: number): string {
  if (count === 0) return 'nobody'
  return count === 1 ? '1 person' : `${count} people`
}

/**
 * Why a line is drawn the way it is, for the reader who is not an admin.
 *
 * Only two sentences, because there are only two cases worth explaining: the
 * admin role, whose line is fixed, and the baseline, whose name is not a role
 * anybody can be given.
 */
export function explain(line: RolePermissions): string | null {
  if (isFixed(line)) return 'An admin manages this project, so they can do everything on it.'
  if (isEveryoneElse(line)) {
    return 'Anybody on this project that nobody has given a role to, and anybody not on it.'
  }
  return null
}

/**
 * The grid in drawing order, with the baseline last.
 *
 * The server already sends it that way. Sorting here as well is not
 * belt-and-braces: a line the screen has just saved is spliced back in from a
 * mutation's response, and a row that jumped to the top the moment it was
 * edited would be a grid nobody could work down.
 */
export function inOrder(grid: ProjectPermissions): RolePermissions[] {
  const rank = (line: RolePermissions) => (line.is_admin ? 0 : isEveryoneElse(line) ? 2 : 1)
  return [...grid.roles].sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name))
}

/**
 * The result of ticking one box on a role's workflow line.
 *
 * The whole line goes back to the server every time — see `api.setColumnRules`
 * — so this returns the whole line rather than the one entry that changed.
 */
export function withColumnRule(
  columns: ColumnRule[],
  columnId: string,
  field: 'may_enter' | 'may_stage',
  on: boolean,
): ColumnRule[] {
  return columns.map((column) =>
    column.column_id === columnId ? { ...column, [field]: on } : column,
  )
}

/**
 * How a role's workflow line reads at a glance.
 *
 * "Anywhere" rather than "5 of 5", because an unrestricted line is the normal
 * state and a fraction invites the reader to work out whether it is the whole
 * board. Only a narrowed line gets counted.
 */
export function summariseColumns(line: RolePermissions): string {
  if (isFixed(line)) return 'Anywhere'
  const enter = line.columns.filter((column) => column.may_enter).length
  if (enter === line.columns.length) return 'Anywhere'
  if (enter === 0) return 'Nowhere'
  return `${enter} of ${line.columns.length} columns`
}

/** Whether anything on this line has been narrowed — what a "reset" offers. */
export function isNarrowed(line: RolePermissions): boolean {
  return line.columns.some((column) => !column.may_enter || !column.may_stage)
}

/**
 * The clearance to show for a line.
 *
 * An admin's is not stored and not editable — they read everything by being
 * an admin — so the grid shows the top level rather than whatever a row might
 * once have said.
 */
export function clearanceOf(line: RolePermissions): Sensitivity {
  return isFixed(line) ? 'restricted' : line.clearance
}
