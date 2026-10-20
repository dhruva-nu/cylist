import { useState } from 'react'
import { isMe, type Person } from '../../api/client'
import { useIdentity } from '../../api/queries'

/**
 * Who a comment is written as: the reader, until they pick somebody else.
 *
 * Held as a choice that may not have been made yet rather than as an id
 * seeded on first render, because the session can land after the dialog
 * opens — a comment composed before it did would otherwise be unattributed.
 * Falls through to '' (unattributed) for a reader who is not on this project.
 */
export function useCommentAuthor(members: Person[]): [string, (id: string) => void] {
  const identity = useIdentity()
  const [chosen, setChosen] = useState<string | null>(null)
  const mine = members.find((person) => isMe(person, identity.data))?.id ?? ''

  return [chosen ?? mine, setChosen]
}
