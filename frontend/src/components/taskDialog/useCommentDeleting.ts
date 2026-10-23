import { useMutation } from '@tanstack/react-query'
import { api, type TaskComment } from '../../api/client'
import { useIdentity, usePermissionGrid } from '../../api/queries'
import { can } from '../../routes/projectPermissions'
import { mayDelete, type Viewer } from '../commentDelete'
import { useRefreshTask } from './useRefreshTask'
import { handlesOwnErrors } from '../../api/mutationErrors'

/** What `useCommentDeleting` hands a timeline. */
export interface CommentDeleting {
  /** Whether this reader is offered the ✕ on this entry. */
  deletable: (entry: TaskComment) => boolean
  remove: (entry: TaskComment) => void
  /** The entry whose delete is in flight, so its button can go quiet. */
  removingId: string | null
  error: Error | null
}

/**
 * Taking something said off a card, for whichever half of the dialog is open.
 *
 * One hook rather than a copy in each, for the reason `useSubtaskTicking` is
 * one: the read view and the form both draw the same timeline, and a gesture
 * that worked differently depending on which of them you were looking at
 * would be two gestures.
 *
 * It asks the permission grid as well as the session, because the two halves
 * of the rule are answered in different places — who you are, and what your
 * role here is. Both queries are ones another screen already holds, so this
 * adds no fetch the project has not made.
 */
export function useCommentDeleting({
  projectKey,
  announce,
  onDone,
}: {
  projectKey: string
  announce: (message: string) => void
  onDone: () => Promise<void>
}): CommentDeleting {
  const refreshTask = useRefreshTask(onDone)
  const identity = useIdentity()
  const grid = usePermissionGrid(projectKey)

  const viewer: Viewer = {
    identity: identity.data,
    // Permissive while in flight, the way `can` is: the server is the fence,
    // and a button that flickers in after a round trip reads as a glitch.
    amAdmin: grid.data?.may_manage ?? false,
    mayComment: can(grid.data?.mine, 'comments'),
  }

  const remove = useMutation({
    meta: handlesOwnErrors,
    mutationFn: (entry: TaskComment) => api.deleteComment(entry.id),
    onSuccess: async (_void, entry) => {
      announce('Comment deleted.')
      await refreshTask(entry.task_id)
    },
  })

  return {
    deletable: (entry) => mayDelete(entry, viewer),
    remove: (entry) => remove.mutate(entry),
    removingId: remove.isPending ? remove.variables.id : null,
    error: remove.error,
  }
}
