import { useMutation } from '@tanstack/react-query'
import { api, type ChecklistState, type Task, type TaskDetail } from '../../api/client'
import { useRefreshTask } from './useRefreshTask'
import { handlesOwnErrors } from '../../api/mutationErrors'

export const CHECKLIST_LABELS: Record<ChecklistState, string> = {
  open: 'Open',
  done: 'Done',
  cancelled: 'Cancelled',
}

/**
 * Ticking a sub-task off, shared by the detail view and the form.
 *
 * Two kinds of sub-task, one gesture. Neither is on the board, so neither is
 * finished by going anywhere: a tick box carries a state, a sub-task carries a
 * finishing time, and ticking either one settles it and stops it holding the
 * parent back.
 *
 * One hook rather than a copy in each list, so the two cannot drift: whichever
 * half of the dialog you are looking at, ticking means the same thing.
 */
export function useSubtaskTicking({
  task,
  announce,
  onDone,
}: {
  task: TaskDetail
  announce: (message: string) => void
  onDone: () => Promise<void>
}) {
  const refreshTask = useRefreshTask(onDone)
  const refresh = () => refreshTask(task.id)

  const setState = useMutation({
    meta: handlesOwnErrors,
    mutationFn: ({ id, state }: { id: string; state: ChecklistState }) =>
      api.updateChecklistItem(id, { state }),
    onSuccess: async (item) => {
      announce(`${item.title} is ${CHECKLIST_LABELS[item.state].toLowerCase()}.`)
      await refresh()
    },
  })

  const setFinished = useMutation({
    meta: handlesOwnErrors,
    mutationFn: ({ child, finished }: { child: Task; finished: boolean }) =>
      api.finishTask(child.id, finished),
    onSuccess: async (child, { finished }) => {
      announce(`${child.reference} is ${finished ? 'finished' : 'open again'}.`)
      await refresh()
    },
  })

  return {
    refresh,
    /** Tick a checklist item, cancel it, or put it back. */
    setItemState: (id: string, state: ChecklistState) => setState.mutate({ id, state }),
    /** Finish a sub-task, or open it again. */
    setFinished: (child: Task, finished: boolean) => setFinished.mutate({ child, finished }),
    busy: setState.isPending || setFinished.isPending,
    error: setState.error ?? setFinished.error,
  }
}
