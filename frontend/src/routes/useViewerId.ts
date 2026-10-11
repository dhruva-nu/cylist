/**
 * The person looking at the page, by id — or null before `api.me()` has
 * answered, or on a deployment with no accounts yet.
 *
 * What a card compares an answer's `answered_by_id` with to say "You: …"
 * rather than a name. The same `['me']` query every other screen reads, so it
 * is fetched once and shared.
 */

import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

export function useViewerId(): string | null {
  const identity = useQuery({ queryKey: ['me'], queryFn: api.me })
  return identity.data?.person?.id ?? null
}
