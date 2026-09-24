/**
 * A project's areas, in the sidebar: Goals, Board, People and Agents.
 *
 * They used to be eight tabs across the bar — Overview, Board, Goals, Files,
 * Vault, People, Roles, Agents — and the bar hid all eight under 720px, which
 * left a phone with the overview's cards as the only way between them. Four is
 * what somebody working a project actually moves between. The rest keep their
 * pages and lose their tabs:
 *
 * - Overview is gone. It was a page of links to the other areas, and a column
 *   of links down the edge of every screen is that page without the detour.
 *   Its address sends you to the board — see `router.tsx`.
 * - Roles is reached from People, which already links to it; while you are on
 *   it, People is the item lit, because that is where you came from.
 * - Files and Vault are a line under the four in the open panel. They hold
 *   real documents and real credentials, so they cannot be allowed to become
 *   pages you can only reach by typing their address — but they are places
 *   you fetch something from rather than places you work, and a line of small
 *   type says that without dressing them up as two more areas.
 *
 * Drawn in two shapes, because the sidebar is. Open, it is a column of rows
 * with a mark and a name each. Collapsed to its rail it is the marks alone
 * down a 46px column, named by their tooltips and to a screen reader — and
 * under 900px, where the rail is a strip across the top instead, it is the
 * names alone, because a strip has the width for four short words and not
 * for four words and four marks. Either way the four are on screen whatever
 * state the sidebar is in, which is what a panel that can be shut owes the
 * one thing in it you cannot do without.
 *
 * Off a project there is nothing to navigate between, and it draws nothing.
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
  agents: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
      <rect x="4" y="7" width="16" height="12" rx="3" />
      <path d="M12 7V4" />
      <circle cx="9.5" cy="13" r="1.2" fill="currentColor" stroke="none" />
      <circle cx="14.5" cy="13" r="1.2" fill="currentColor" stroke="none" />
    </svg>
  ),
}

/**
 * The four, in this order. Goals first because it is the widest view of the
 * project — what the work is for — and the board, people and agents are what
 * the work is, who is doing it and what is helping them.
 */
const AREAS = [
  { to: '/p/$projectKey/goals', name: 'Goals', icon: ICONS.goals },
  { to: '/p/$projectKey/board', name: 'Board', icon: ICONS.board },
  { to: '/p/$projectKey/people', name: 'People', icon: ICONS.people },
  { to: '/p/$projectKey/agents', name: 'Agents', icon: ICONS.agents },
] as const

export function ProjectNav({ rail = false }: { rail?: boolean }) {
  const matchRoute = useMatchRoute()
  const match = matchRoute({ to: '/p/$projectKey', fuzzy: true })
  if (!match) return null

  const { projectKey } = match
  // Roles has no item of its own, and is reached from People's heading.
  const onRoles = Boolean(matchRoute({ to: '/p/$projectKey/roles' }))

  return (
    <nav className={rail ? styles.rail : styles.panel} aria-label="Project">
      <ul className={styles.areas}>
        {AREAS.map((area) => (
          <li key={area.to}>
            <Link
              to={area.to}
              params={{ projectKey }}
              className={`${styles.area} ${area.name === 'People' && onRoles ? styles.on : ''}`}
              activeProps={{ className: styles.on }}
              title={rail ? area.name : undefined}
            >
              <span className={styles.icon}>{area.icon}</span>
              <span className={styles.name}>{area.name}</span>
            </Link>
          </li>
        ))}
      </ul>
      {rail ? null : (
        <p className={styles.also}>
          Also here:{' '}
          <Link
            to="/p/$projectKey/files"
            params={{ projectKey }}
            activeProps={{ className: styles.alsoOn }}
          >
            Files
          </Link>{' '}
          ·{' '}
          <Link
            to="/p/$projectKey/vault"
            params={{ projectKey }}
            activeProps={{ className: styles.alsoOn }}
          >
            Vault
          </Link>
        </p>
      )}
    </nav>
  )
}
