/**
 * What the app does when the server says nobody is signed in any more.
 *
 * A session can end under a screen that is already open — the cookie expired, a
 * person revoked it from another device — and until now each screen found out
 * for itself, by showing its own error. The gate in `App.tsx` already knows
 * what to do with an unauthenticated identity, so the whole job is to make the
 * identity read again: it answers 401, and the sign-in screen takes over.
 */

import type { QueryClient } from '@tanstack/react-query'
import { ApiError } from './http'
import { queryKeys } from './queries'

/**
 * Re-ask who is signed in, if `error` says nobody is.
 *
 * Only once an identity has been read: a failed sign-in attempt is also a 401,
 * and while the sign-in screen is up there is no session to have lost.
 */
export function noteIfSignedOut(queryClient: QueryClient, error: unknown): void {
  if (!(error instanceof ApiError) || !error.isUnauthenticated) return
  if (queryClient.getQueryData(queryKeys.identity) === undefined) return
  void queryClient.invalidateQueries({ queryKey: queryKeys.identity })
}
