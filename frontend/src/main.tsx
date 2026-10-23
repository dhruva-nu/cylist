import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import { reportFailure } from './api/mutationErrors'
import { refreshAfterWrite } from './api/refresh'
import { noteIfSignedOut } from './api/session'
import { Toasts } from './components/Toasts'
import { keptCurrentBySocket } from './routes/useBoardSocket'
import { applyStoredTheme } from './theme/theme'
import './theme/tokens.css'

// Before React mounts, so the first paint is already the right theme rather
// than the light one flashing on the way to the dark one.
applyStoredTheme()

/** What a write that succeeds makes stale, and what a failed one says — see `refreshAfterWrite`. */
const mutationCache = new MutationCache({
  onError: (error, _variables, _context, mutation) => {
    noteIfSignedOut(queryClient, error)
    reportFailure(error, mutation)
  },
  onSuccess: (_data, _variables, _context, mutation) => refreshAfterWrite(queryClient, mutation),
})

/**
 * A request that comes back unauthenticated sends the whole app to the sign-in
 * screen, whichever screen asked — see `noteIfSignedOut`.
 */
const queryCache = new QueryCache({
  onError: (error) => noteIfSignedOut(queryClient, error),
})

const queryClient: QueryClient = new QueryClient({
  queryCache,
  mutationCache,
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      // Cylist is written on by agents over the same API, so coming back to
      // the tab is a good moment to find out what changed while you were gone —
      // except for a board's cards while its socket is up, which hears of every
      // change anyway and catches up on its own when the tab comes back.
      refetchOnWindowFocus: (query) => !keptCurrentBySocket(query.queryKey),
    },
  },
})

const container = document.getElementById('root')
if (!container) {
  throw new Error('index.html is missing its #root element.')
}

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
      <Toasts />
    </QueryClientProvider>
  </StrictMode>,
)
