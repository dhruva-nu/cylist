import { MutationCache, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import { applyStoredTheme } from './theme/theme'
import './theme/tokens.css'

// Before React mounts, so the first paint is already the right theme rather
// than the light one flashing on the way to the dark one.
applyStoredTheme()

/**
 * Anything written invalidates everything read.
 *
 * A screen that forgets one key is a screen where a new task, column or secret
 * only turns up after a reload, and the failure is invisible until somebody
 * hits exactly that path. Refetching the handful of lists this app has on
 * screen costs a few hundred bytes and takes that whole class of bug away;
 * screens still name the keys they care about, which is what makes them wait
 * for the right data before closing a dialog.
 */
const mutationCache = new MutationCache({
  onSuccess: () => {
    void queryClient.invalidateQueries()
  },
})

const queryClient = new QueryClient({
  mutationCache,
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      // Cylist is written on by agents over the same API, so coming back to
      // the tab is a good moment to find out what changed while you were gone.
      refetchOnWindowFocus: true,
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
    </QueryClientProvider>
  </StrictMode>,
)
