/** The directory: everybody Cylist knows, across every project. */

import type { Identity, InviteIssued } from './auth'
import { jsonBody, request } from './http'

export type PersonKind = 'team' | 'client'

export interface Person {
  id: string
  name: string
  kind: PersonKind
  /**
   * Their job description — "Finance controller, Atlas".
   *
   * Called `title` rather than `role` since CYLIST-45, which gave the word
   * role to a thing a project's admin creates and hands out. See `Role`.
   */
  title: string
  responsibilities: string
  email: string | null
  colour: string
  archived_at: string | null
  created_at: string
  /**
   * Whether this entry is the machine rather than a person.
   *
   * It is assigned work like anybody else and never signs in — a board hands
   * a card to it the way it hands one to a colleague.
   */
  is_agent: boolean
  /** Whether they can sign in as themselves right now. */
  has_account: boolean
  /** Whether an unaccepted, unexpired invitation is outstanding. */
  invite_is_pending: boolean
}

/**
 * Whether a directory entry is the person currently signed in.
 *
 * There is deliberately no `is_me` on the wire: one person is one payload
 * whoever fetched it. The session already knows who it is — `api.me()` said
 * so — which makes this a comparison rather than a field the server has to
 * get right per request.
 */
export const isMe = (person: Pick<Person, 'id'>, identity: Identity | undefined) =>
  identity?.person?.id === person.id

export interface PersonInput {
  name: string
  kind: PersonKind
  title: string
  responsibilities: string
  email?: string | null
}

export const peopleApi = {
  /** Mint a one-time link letting a team member set their first password. */
  invitePerson: (id: string) => request<InviteIssued>(`/people/${id}/invite`, { method: 'POST' }),
  /**
   * Set the email and password somebody signs in with, on their behalf.
   *
   * The other door, for where an invitation cannot reach: it needs no mailbox
   * and no round trip, at the cost of the caller knowing the password. On
   * somebody who already had one it is a reset and their other sessions go.
   */
  setAccount: (id: string, email: string, password: string) =>
    request<Person>(`/people/${id}/account`, {
      method: 'PUT',
      body: jsonBody({ email, password }),
    }),
  withdrawInvite: (id: string) =>
    request<{ ok: boolean }>(`/people/${id}/invite`, { method: 'DELETE' }),
  listPeople: () => request<Person[]>('/people'),
  createPerson: (input: PersonInput) =>
    request<Person>('/people', { method: 'POST', body: jsonBody(input) }),
  updatePerson: (id: string, input: Partial<PersonInput>) =>
    request<Person>(`/people/${id}`, { method: 'PATCH', body: jsonBody(input) }),
}
