/**
 * A project's areas, and the two places they are listed.
 *
 * The bar carries four tabs — Goals, Board, People, Agents — which are what
 * somebody working a project moves between all day. The sidebar carries every
 * area: those four, and Roles, Files and Vault, which are places you go to
 * fetch or settle something rather than places you work. So the bar stays a
 * short row across the top of the page, and nothing is reachable only by
 * typing its address.
 *
 * Overview is in neither. It was a page of cards linking to the other areas,
 * and a list of them down the edge of every screen is that page without the
 * detour; its address sends you to the board — see `router.tsx`.
 *
 * The sidebar list is drawn in two shapes, because the sidebar is. Open, it is
 * a column of rows with a mark and a name each. Collapsed to its rail it is
 * the marks alone down a 46px column, named by their tooltips and to a screen
 * reader. Under 900px, where the rail is a strip across the top instead, it
 * draws nothing: seven names do not fit across a phone, and the four you use
 * most are in the bar's own row directly beneath it — see `.tabs` in the
 * stylesheet. The other three are one press of the handle away.
 *
 * Off a project there is nothing to navigate between, and neither draws
 * anything.
 */

import { Link, useMatchRoute } from '@tanstack/react-router'
import styles from './ProjectNav.module.css'

const ICONS = {
  goals: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
      <circle cx="12" cy="12" r="8" />
      <circle cx="12" cy="12" r="4" />
      <circle cx="12" cy="12" r="1" fill="currentColor" />
    </svg>
  ),
  board: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
      <rect x="3" y="4" width="5" height="16" rx="1.5" />
      <rect x="10" y="4" width="5" height="10" rx="1.5" />
      <rect x="17" y="4" width="4" height="13" rx="1.5" />
    </svg>
  ),
  people: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
      <circle cx="9" cy="8" r="3.2" />
      <path d="M3.5 19a5.5 5.5 0 0 1 11 0" />
      <circle cx="17" cy="9" r="2.5" />
      <path d="M15.5 14.5A4.5 4.5 0 0 1 21 19" />
    </svg>
  ),
  roles: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
      <path d="M12 3 5 6v5c0 4.4 3 8.3 7 10 4-1.7 7-5.6 7-10V6z" />
      <path d="m9 12 2 2 4-4" />
    </svg>
  ),
  agents: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
      <rect x="4" y="7" width="16" height="12" rx="3" />
      <path d="M12 7V4" />
      <circle cx="9.5" cy="13" r="1.2" fill="currentColor" stroke="none" />
      <circle cx="14.5" cy="13" r="1.2" fill="currentColor" stroke="none" />
    </svg>
  ),
  files: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
    </svg>
  ),
  vault: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
      <rect x="4" y="10" width="16" height="10" rx="2" />
      <path d="M8 10V7a4 4 0 0 1 8 0v3" />
      <circle cx="12" cy="15" r="1.3" />
    </svg>
  ),
}

/**
 * Every area, in the order the sidebar lists them. `inBar` marks the four the
 * bar carries too, in the same order, so the two lists never disagree about
 * which comes first.
 */
const AREAS = [
  { to: '/p/$projectKey/goals', name: 'Goals', icon: ICONS.goals, inBar: true },
  { to: '/p/$projectKey/board', name: 'Board', icon: ICONS.board, inBar: true },
  { to: '/p/$projectKey/people', name: 'People', icon: ICONS.people, inBar: true },
  { to: '/p/$projectKey/roles', name: 'Roles', icon: ICONS.roles, inBar: false },
  { to: '/p/$projectKey/agents', name: 'Agents', icon: ICONS.agents, inBar: true },
  { to: '/p/$projectKey/files', name: 'Files', icon: ICONS.files, inBar: false },
  { to: '/p/$projectKey/vault', name: 'Vault', icon: ICONS.vault, inBar: false },
] as const

/** The project the address is in, or null off a project. */
function useProjectKey(): string | null {
  const matchRoute = useMatchRoute()
  const match = matchRoute({ to: '/p/$projectKey', fuzzy: true })
  return match ? match.projectKey : null
}

/** The bar's four tabs. */
export function ProjectTabs() {
  const projectKey = useProjectKey()
  if (projectKey === null) return null

  return (
    <nav className={styles.tabs} aria-label="Project">
      {AREAS.filter((area) => area.inBar).map((area) => (
        <Link
          key={area.to}
          to={area.to}
          params={{ projectKey }}
          activeProps={{ className: styles.tabOn }}
        >
          {area.name}
        </Link>
      ))}
    </nav>
  )
}

/** Every area, as the sidebar lists them: rows in the panel, marks on the rail. */
export function ProjectNav({ rail = false }: { rail?: boolean }) {
  const projectKey = useProjectKey()
  if (projectKey === null) return null

  return (
    <nav className={rail ? styles.rail : styles.panel} aria-label="Project areas">
      <ul className={styles.areas}>
        {AREAS.map((area) => (
          <li key={area.to}>
            <Link
              to={area.to}
              params={{ projectKey }}
              className={styles.area}
              activeProps={{ className: styles.on }}
              title={rail ? area.name : undefined}
            >
              <span className={styles.icon}>{area.icon}</span>
              <span className={styles.name}>{area.name}</span>
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  )
}
