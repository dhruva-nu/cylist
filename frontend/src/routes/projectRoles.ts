/**
 * What the roles screen needs to know about a role, kept out of the TSX.
 *
 * It used to answer "am I allowed to?" from the member list too. It does not
 * any more: CYLIST-46 made the server say so outright, in `may_manage` on the
 * permission grid, because three different callers may administer a project
 * and only one of them is visible in a member list.
 */

import type { RoleSummary } from '../api/client'

/**
 * Whether a project already has a role by this name, ignoring case.
 *
 * The server refuses the clash either way; asking here is what lets the form
 * say so before a round trip, and what keeps "Reviewer" and "reviewer" from
 * looking like two different answers to somebody typing.
 */
export function nameIsTaken(roles: RoleSummary[], name: string, excludingId?: string): boolean {
  const wanted = name.trim().toLocaleLowerCase()
  if (wanted === '') return false
  return roles.some((role) => role.id !== excludingId && role.name.toLocaleLowerCase() === wanted)
}

/**
 * Why a role cannot be deleted, or null if it can.
 *
 * The same two rules the server enforces, worded for somebody looking at the
 * button rather than at a response: the admin role is what makes managing any
 * of this possible, and a role somebody is wearing should not vanish off their
 * name because a list was tidied.
 */
export function whyUndeletable(role: RoleSummary): string | null {
  if (role.is_admin) return 'The Admin role is what lets anyone manage the others.'
  if (role.member_count > 0) {
    const who = role.member_count === 1 ? 'somebody' : `${role.member_count} people`
    return `Move ${who} off it first.`
  }
  return null
}
