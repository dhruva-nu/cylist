/**
 * The frame every screen sits in: the sidebar, and beside it the wordmark,
 * the project's tabs and breadcrumbs over the page. The bar's tabs are five of
 * a project's areas and the sidebar lists all of them — see `ProjectNav.tsx`.
 */

import { Link, Outlet, useMatchRoute } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { type ReactNode, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { ProjectTabs } from './ProjectNav'
import { Sidebar } from './Sidebar'
import { Avatar } from './ui'
import styles from './Shell.module.css'

/** The mark in the bar before anyone has said who they are. A fixed accent,
 * not a token that flips. */
const OWNER_COLOUR = '#4a7b8c'

/**
 * How wide the screen you are on is allowed to be.
 *
 * Four answers. The board is its own container and takes the window; People,
 * Files, Vault and the rest of a project's areas are a directory, a table and
 * two trees, none of which is reading matter, and the 1180px column they used
 * to sit in spent a quarter of a wide display on empty margin while squeezing
 * the content into more, thinner pieces than it wanted.
 *
 * Home is the fourth. It is a gallery — a masthead over a grid of project
 * cards — so it wants a wide page like the areas do, but its heading belongs
 * in the middle over the grid rather than off to one side: the width of a
 * roomy page without the row that a roomy page makes of its head. Left on the
 * reading width it was worse than narrow, because a reading column is sized
 * by its own prose and Home's prose is one sentence — see `.pageGallery` in
 * the stylesheet.
 *
 * Everything left over is prose and stays where prose belongs.
 *
 * The value lands on the frame as a custom property, so the bar and the
 * breadcrumbs line up with the page rather than each carrying a width of their
 * own — see `--page-max` in the stylesheet.
 */
function usePageWidth(): string | undefined {
  const matchRoute = useMatchRoute()

  if (matchRoute({ to: '/p/$projectKey/board' })) return styles.pageBoard
  if (matchRoute({ to: '/' })) return styles.pageGallery
  if (
    matchRoute({ to: '/p/$projectKey/agents' }) ||
    matchRoute({ to: '/p/$projectKey/people' }) ||
    matchRoute({ to: '/p/$projectKey/roles' }) ||
    matchRoute({ to: '/p/$projectKey/files' }) ||
    matchRoute({ to: '/p/$projectKey/docs' }) ||
    matchRoute({ to: '/p/$projectKey/goals' }) ||
    matchRoute({ to: '/p/$projectKey/goals/$goalRef' }) ||
    matchRoute({ to: '/p/$projectKey/vault' })
  ) {
    return styles.pageRoomy
  }
  return styles.pageReading
}

export function Shell() {
  const matchRoute = useMatchRoute()
  // The board and Docs scroll inside themselves — the board's columns, the
  // docs' tree and open doc — so the page is exactly the height left under the
  // bar rather than as tall as what it holds. The board also scrolls sideways,
  // which is why it gets the full window rather than a capped column.
  const wide = Boolean(
    matchRoute({ to: '/p/$projectKey/board' }) || matchRoute({ to: '/p/$projectKey/docs' }),
  )
  const page = usePageWidth() ?? ''

  return (
    <>
      {/*
        The whole sidebar and a few more tab stops sit between the top of the
        page and the content — the panel's handle, a project's areas and the
        sections under them, then the wordmark, four tabs and the breadcrumb.
        Tabbing past all of that on every navigation is the sort of thing that
        makes a keyboard unusable, so there is a way over it. It matters more
        now than it did with the panel on the other side, where it came after
        the content rather than before it.
      */}
      <a href="#content" className={styles.skip}>
        Skip to content
      </a>
      {/* The window's remaining height, split into the sidebar and the page
          beside it. The bar sits inside the page's own column rather than
          above both, so the navigation is as wide as the content it belongs
          to rather than running on underneath the panel.

          The sidebar is first here because it is first on screen. Down the
          left-hand edge it has to come before the page in the markup too —
          reading order and tab order following the layout rather than
          contradicting it — which is also what puts the panel above the page
          rather than below it once the frame stacks. */}
      <div className={styles.frame}>
        <Sidebar />
        <div className={styles.column}>
          <div className={`${styles.top} ${page}`}>
            <div className={styles.bar}>
              <Link to="/" className={styles.brand}>
                <span className={styles.mark}>C</span> Cylist
              </Link>
              <ProjectTabs />
              <div className={styles.right}>
                <You />
              </div>
            </div>
            <Breadcrumbs />
          </div>
          <main id="content" className={`${styles.wrap} ${page} ${wide ? styles.wide : ''}`}>
            <Outlet />
          </main>
        </div>
      </div>
    </>
  )
}

/**
 * Your own mark in the bar.
 *
 * `/me` is fetched once by the authentication gate and read from the cache
 * here, so this costs nothing. A session with no person behind it — the
 * bootstrap login, on a deployment whose first account has not been opened —
 * gets the plain accent, because there is nobody to name yet.
 */
function You() {
  const identity = useQuery({ queryKey: ['me'], queryFn: api.me })
  const person = identity.data?.person ?? null

  return person ? (
    <Avatar name={person.name} colour={person.colour} />
  ) : (
    <Avatar name="You" colour={OWNER_COLOUR} />
  )
}

/**
 * A project's areas as the breadcrumb names them, checked in this order and
 * the first match wins.
 */
const PROJECT_AREAS = [
  { name: 'Board', route: { to: '/p/$projectKey/board' } },
  // Fuzzy, so a goal's own page is still under Goals rather than nowhere.
  { name: 'Goals', route: { to: '/p/$projectKey/goals', fuzzy: true } },
  { name: 'Docs', route: { to: '/p/$projectKey/docs' } },
  { name: 'Files', route: { to: '/p/$projectKey/files' } },
  { name: 'Vault', route: { to: '/p/$projectKey/vault' } },
  { name: 'People', route: { to: '/p/$projectKey/people' } },
  { name: 'Roles', route: { to: '/p/$projectKey/roles' } },
  { name: 'Agents', route: { to: '/p/$projectKey/agents' } },
] as const

function Breadcrumbs() {
  const matchRoute = useMatchRoute()
  const inProject = matchRoute({ to: '/p/$projectKey', fuzzy: true })
  const area = PROJECT_AREAS.find((candidate) => matchRoute(candidate.route)) ?? null

  if (!inProject) {
    return (
      <div className={styles.crumbs}>
        <b>All projects</b>
      </div>
    )
  }

  const { projectKey } = inProject
  const areaRoute = area?.route.to ?? '/p/$projectKey/board'

  return (
    <div className={styles.crumbs}>
      <Link to="/">All projects</Link>
      <span className={styles.separator}>›</span>
      {area ? (
        <>
          <ProjectSwitcher projectKey={projectKey} areaRoute={areaRoute} withSeparator />
          <b>{area.name}</b>
        </>
      ) : (
        <ProjectSwitcher projectKey={projectKey} areaRoute={areaRoute} emphasized />
      )}
    </div>
  )
}

function useProject(projectKey: string) {
  return useQuery({
    queryKey: ['project', projectKey],
    queryFn: () => api.getProject(projectKey),
  })
}

/** How long the pointer rests on the project before its list drops. */
const SWITCHER_DELAY_MS = 600

/**
 * The project's name in the breadcrumb, doubling as a switcher: hovering (or
 * focusing) it turns the separator after it to point down, and once the
 * pointer has rested there for a moment a list of the projects drops, and picking one jumps straight to the same area of that
 * project rather than forcing a detour through "All projects". With only one
 * project there is nothing to switch to, so it stays a plain crumb.
 */
function ProjectSwitcher({
  projectKey,
  areaRoute,
  emphasized,
  withSeparator,
}: {
  projectKey: string
  areaRoute: (typeof PROJECT_AREAS)[number]['route']['to'] | '/p/$projectKey/board'
  emphasized?: boolean
  withSeparator?: boolean
}) {
  const project = useProject(projectKey)
  const projects = useQuery({ queryKey: ['projects'], queryFn: api.listProjects })
  const [hovered, setHovered] = useState(false)
  const [open, setOpen] = useState(false)
  const timer = useRef<number | undefined>(undefined)
  const name = project.data?.name ?? projectKey
  const all = projects.data ?? []
  const switchable = all.some((candidate) => candidate.key !== projectKey)
  const shown = switchable && open

  useEffect(() => () => window.clearTimeout(timer.current), [])

  const enter = () => {
    setHovered(true)
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setOpen(true), SWITCHER_DELAY_MS)
  }
  const leave = () => {
    window.clearTimeout(timer.current)
    setHovered(false)
    setOpen(false)
  }

  const label = emphasized ? (
    <b>{name}</b>
  ) : (
    <Link to={areaRoute} params={{ projectKey }}>
      {name}
    </Link>
  )

  return (
    <div
      className={styles.switcher}
      onMouseEnter={enter}
      onMouseLeave={leave}
      onFocus={() => {
        // Keyboard users do not hover, so there is nothing to wait out.
        window.clearTimeout(timer.current)
        setHovered(true)
        setOpen(true)
      }}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) leave()
      }}
      onKeyDown={(event) => {
        if (event.key === 'Escape') leave()
      }}
    >
      {label}
      {withSeparator ? (
        <span className={`${styles.separator} ${hovered && switchable ? styles.separatorDown : ''}`}>›</span>
      ) : null}
      {shown ? (
        <div className={styles.switcherMenu} role="menu" aria-label="Switch project">
          <div className={styles.switcherList}>
            {all.map((candidate) => (
              <Link
                key={candidate.id}
                to={areaRoute}
                params={{ projectKey: candidate.key }}
                role="menuitem"
                className={candidate.key === projectKey ? styles.switcherActive : undefined}
                aria-current={candidate.key === projectKey ? 'true' : undefined}
                onClick={leave}
              >
                {candidate.name}
              </Link>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  )
}

export function PageHead({
  eyebrow,
  title,
  children,
  actions,
}: {
  eyebrow?: ReactNode
  title: string
  children?: ReactNode
  actions?: ReactNode
}) {
  return (
    <div className={styles.head}>
      {/* The words in one box and the buttons in another, because on a wide
          page the two are a row rather than a stack — and a row needs the
          eyebrow, the title and the description to travel together. */}
      <div className={styles.headText}>
        {eyebrow}
        <h1>{title}</h1>
        {children ? <p>{children}</p> : null}
      </div>
      {actions ? <div className={styles.actions}>{actions}</div> : null}
    </div>
  )
}
