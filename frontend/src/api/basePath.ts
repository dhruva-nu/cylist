/**
 * The path prefix this deployment is served under — `/dev_1` — or `''`.
 *
 * Learned at runtime, not baked into the bundle, so one build serves the site
 * root and every dev slot alike. The backend writes it into the
 * `cylist-base-path` meta tag when it serves `index.html` (`app/spa.py`,
 * from `CYLIST_BASE_PATH`); the Vite dev server serves the tag empty, which
 * is the site root.
 *
 * Everything that builds an address the browser will request goes through
 * here: the API client, the board socket, the router's basepath, and the MCP
 * line the Agents page hands out. An address that skips it works at the root
 * and quietly asks the wrong server under a prefix.
 */

export const BASE_PATH_META = 'cylist-base-path'

/** `dev_1/`, `/dev_1` and `/dev_1/` are all `/dev_1`; blank and `/` are `''`. */
export function normaliseBasePath(raw: string | null | undefined): string {
  const trimmed = (raw ?? '').trim().replace(/^\/+|\/+$/g, '')
  return trimmed ? `/${trimmed}` : ''
}

/** The base path a page was served with, from its meta tag. */
export function readBasePath(doc: Pick<Document, 'querySelector'> | undefined): string {
  const tag = doc?.querySelector<HTMLMetaElement>(`meta[name="${BASE_PATH_META}"]`)
  return normaliseBasePath(tag?.content)
}

/** This page's base path. `''` where there is no document, as in a unit test. */
export const BASE_PATH = readBasePath(typeof document === 'undefined' ? undefined : document)

/** `path` (which starts with `/`) under the base path: `/api/v1` → `/dev_1/api/v1`. */
export function withBase(path: string, base: string = BASE_PATH): string {
  return `${base}${path}`
}

/** A location's pathname with the base path taken off, for matching app routes. */
export function stripBase(pathname: string, base: string = BASE_PATH): string {
  if (!base) return pathname
  if (pathname === base) return '/'
  return pathname.startsWith(`${base}/`) ? pathname.slice(base.length) : pathname
}
