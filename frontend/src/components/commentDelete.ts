/**
 * Who is offered the ✕ on a timeline entry.
 *
 * The server's rule, read from the client's side: its author may take back
 * what they said, an admin of the project may take off anybody's, and a status
 * change is nobody's to take off at all — it is the card's own record of when
 * it stopped and why. See `may_delete_this_comment` in `app/routers/tasks.py`,
 * which is what actually refuses; this only decides whether the button is
 * worth drawing, because one that always refuses is worse than one that is not
 * there.
 */

import { isMe, type Identity, type TaskComment } from '../api/client'

export interface Viewer {
  identity: Identity | undefined
  /** Whether the reader administers this project — `may_manage` on the grid. */
  amAdmin: boolean
  /** Whether their role lets them comment here, which is what retracting is. */
  mayComment: boolean
}

export function mayDelete(entry: TaskComment, viewer: Viewer): boolean {
  // Not a remark: the card's history, which is kept whoever is looking.
  if (entry.kind !== 'comment') return false
  if (viewer.amAdmin) return true
  if (entry.author === null) return false
  return isMe(entry.author, viewer.identity) && viewer.mayComment
}
