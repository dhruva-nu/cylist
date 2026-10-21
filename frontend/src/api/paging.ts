/**
 * Lists, a page at a time.
 *
 * Every list the server serves comes back as one page of at most `limit` rows
 * and `next`, the id of the last of them, to pass back as `after` for the rows
 * that follow (CYLIST-130). Before that a board answered with every card it
 * held, descriptions and all, which at tens of thousands of cards was a
 * hundred megabytes of JSON.
 *
 * Two ways of reading one. A long list the reader scrolls through — a column
 * of the board, a card's timeline, cold storage — is read a page at a time and
 * offers more. A list that is a set of choices — the people a card can be
 * handed to, the goals it can be put on — is read whole with `fetchAllPages`,
 * because a picker that offers half the people is a picker that is wrong.
 */

import { request } from './http'

/** One page of a list, and the cursor for the next. */
export interface Page<T> {
  items: T[]
  /** Pass as `after` for the next page. Null when this page is the last. */
  next: string | null
}

/** The most a page may hold. The server refuses a larger `limit`. */
export const MAX_PAGE = 100

/**
 * `path` with `after` and `limit` set on its query string, keeping whatever
 * query it already had.
 */
export function pagePath(path: string, after: string | null, limit = MAX_PAGE): string {
  const [base, query = ''] = path.split('?', 2)
  const params = new URLSearchParams(query)
  params.set('limit', String(limit))
  if (after) params.set('after', after)
  return `${base}?${params.toString()}`
}

/** One page of the list at `path`. */
export function fetchPage<T>(path: string, after: string | null = null, limit = MAX_PAGE) {
  return request<Page<T>>(pagePath(path, after, limit))
}

/**
 * Every row of the list at `path`, following each page's cursor to the end.
 *
 * For the lists that are a set of choices, which are short in practice and
 * wrong if cut short.
 */
export async function fetchAllPages<T>(path: string): Promise<T[]> {
  const rows: T[] = []
  let after: string | null = null
  do {
    const page: Page<T> = await fetchPage<T>(path, after)
    rows.push(...page.items)
    after = page.next
  } while (after)
  return rows
}
