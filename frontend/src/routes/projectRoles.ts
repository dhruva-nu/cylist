/**
 * What the People page needs to know about roles, kept out of the TSX.
 *
 * This is the first place the web app asks "am I allowed to?", and it answers
 * from the member list rather than from `identity.scopes`. Scopes say what a
 * credential may do anywhere; whether you administer *this* project is a fact
 * about this board, and the member list is where the server already said it.
 */

import type { Identity, Member, RoleSummary } from '../api/client'

/** The signed-in person's own membership row, if they are on this project. */
export function myMembership(
  members: Member[],
  identity: Identity | undefined,
): Member | undefined {
  const me = identity?.person?.id
  return me === undefined ? undefined : members.find((member) => member.id === me)
}

/**
 * Whether the reader may create, rename and hand out this project's roles.
 *
 * Holding the admin role is the answer. The server has one more case — a
 * project whose last admin was archived lets an `admin`-scoped credential step
 * in — which is deliberately not mirrored here: it is a way back rather than a
 * way of working, and a button that appears on a technicality is worse than
 * one refusal nobody will see.
 */
export function isProjectAdmin(members: Member[], identity: Identity | undefined): boolean {
  return myMembership(members, identity)?.role?.is_admin === true
}

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
