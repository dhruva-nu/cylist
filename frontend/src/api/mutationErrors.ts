/**
 * What happens to a write that failed and that nobody on screen is watching.
 *
 * Most writes belong to a dialog that shows its own failure in place, under the
 * field it came from. The rest used to fail in silence: the card did not move,
 * the sub-task did not tick, and nothing said so. The default is therefore to
 * say it — a toast, in the server's own words — and a write opts out by saying
 * it handles its own (`meta: handlesOwnErrors`), or by having an `onError`,
 * which is the same statement made in code.
 */

import type { Mutation } from '@tanstack/react-query'
import { showToast } from '../components/toasts'
import { ApiError, errorMessage } from './http'

/** `meta` for a write whose screen already reports its failure. */
export const handlesOwnErrors = { handlesOwnErrors: true } as const

/**
 * Tell the person `error` happened, unless the write's own screen will.
 *
 * Not for a lost session: the sign-in screen is about to replace this one, and
 * a toast for it would be the last thing a person saw before being asked to
 * sign in again.
 */
export function reportFailure(
  error: unknown,
  mutation: Pick<Mutation<unknown, unknown, unknown, unknown>, 'meta' | 'options'>,
): void {
  if (mutation.meta?.handlesOwnErrors || mutation.options.onError) return
  if (error instanceof ApiError && error.isUnauthenticated) return
  showToast(errorMessage(error))
}
