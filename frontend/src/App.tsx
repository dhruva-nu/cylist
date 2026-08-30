/**
 * The authentication gate.
 *
 * Everything behind a session lives in the router; `/me` decides whether the
 * router or the sign-in screen is mounted, so no route needs its own guard.
 */

import { useQuery } from '@tanstack/react-query'
import { RouterProvider } from '@tanstack/react-router'
import { ApiError, api } from './api/client'
import { SignIn } from './routes/SignIn'
import { router } from './router'
import { EmptyState, ErrorBanner } from './components/ui'

export function App() {
  const identity = useQuery({
    queryKey: ['me'],
    queryFn: api.me,
    retry: (failureCount, error) =>
      // Not being signed in is an answer, not a failure worth retrying.
      !(error instanceof ApiError && error.isUnauthenticated) && failureCount < 2,
  })

  if (identity.isPending) return <EmptyState>Loading…</EmptyState>

  if (identity.error instanceof ApiError && identity.error.isUnauthenticated) {
    return <SignIn />
  }

  if (identity.error) return <ErrorBanner>{identity.error.message}</ErrorBanner>

  return <RouterProvider router={router} />
}
