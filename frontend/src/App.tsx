/**
 * The authentication gate.
 *
 * Everything behind a session lives in the router; `/me` decides whether the
 * router or the sign-in screen is mounted, so no route needs its own guard.
 *
 * `/invite/<token>` is read here rather than in the router, and read before
 * `/me` is even asked. It is the one address that means something to somebody
 * who has no session and is not trying to sign in — sending them to a sign-in
 * screen for a password they have not chosen yet would be a dead end — and
 * putting it in the router would mean mounting the router for a visitor the
 * gate exists to keep out.
 */

import { useQuery } from '@tanstack/react-query'
import { RouterProvider } from '@tanstack/react-router'
import { ApiError, api } from './api/client'
import { AcceptInvite } from './routes/AcceptInvite'
import { SignIn } from './routes/SignIn'
import { router } from './router'
import { EmptyState, ErrorBanner } from './components/ui'

const INVITE_PATH = /^\/invite\/([^/?#]+)/

/** The invitation token in the address bar, if that is where we are. */
export function inviteTokenIn(pathname: string): string | null {
  const matched = INVITE_PATH.exec(pathname)
  return matched ? decodeURIComponent(matched[1]!) : null
}

export function App() {
  const invitation = inviteTokenIn(window.location.pathname)

  const identity = useQuery({
    queryKey: ['me'],
    queryFn: api.me,
    retry: (failureCount, error) =>
      // Not being signed in is an answer, not a failure worth retrying.
      !(error instanceof ApiError && error.isUnauthenticated) && failureCount < 2,
  })

  if (invitation) return <AcceptInvite token={invitation} />

  if (identity.isPending) return <EmptyState>Loading…</EmptyState>

  if (identity.error instanceof ApiError && identity.error.isUnauthenticated) {
    return <SignIn />
  }

  if (identity.error) return <ErrorBanner>{identity.error.message}</ErrorBanner>

  return <RouterProvider router={router} />
}
