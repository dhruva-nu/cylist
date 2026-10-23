/**
 * What is read again after a write succeeds.
 *
 * Anything written invalidates everything read — unless the write says better.
 * A screen that forgets one key is a screen where a new task, column or secret
 * only turns up after a reload, and the failure is invisible until somebody
 * hits exactly that path; refetching the handful of lists this app has on
 * screen costs a few hundred bytes and takes that whole class of bug away.
 * Screens still name the keys they care about, which is what makes them wait
 * for the right data before closing a dialog.
 *
 * The exceptions are a write that says what it changes — `invalidates`, which
 * reads only those — and one that already refreshes everything it touches —
 * `refreshesItself`, which reads nothing more. The board's writes are the
 * second kind, and on top of their own refresh the blanket read every list on
 * screen a second time for each card moved.
 */

import type { Mutation, QueryClient, QueryKey } from '@tanstack/react-query'

export function refreshAfterWrite(
  queryClient: QueryClient,
  mutation: Pick<Mutation<unknown, unknown, unknown, unknown>, 'meta'>,
): void {
  if (mutation.meta?.refreshesItself) return
  const declared = mutation.meta?.invalidates
  if (Array.isArray(declared)) {
    for (const queryKey of declared as QueryKey[]) void queryClient.invalidateQueries({ queryKey })
    return
  }
  void queryClient.invalidateQueries()
}
