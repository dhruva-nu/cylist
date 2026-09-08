/** The frame every screen sits in: wordmark, project tabs and breadcrumbs. */

import { Link, Outlet, useMatchRoute } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { type ReactNode } from 'react'
import { api } from '../api/client'
import { Sidebar } from './Sidebar'
import { Avatar } from './ui'
import styles from './Shell.module.css'

/** The mark in the bar before anyone has said who they are. A fixed accent,
 * not a token that flips. */
const OWNER_COLOUR = '#4a7b8c'

/**
 * How wide the screen you are on is allowed to be.
 *
 * Three answers rather than the two there used to be. The board is its own
 * container and takes the window; People, Files and Vault are a directory, a
 * table and two trees, none of which is reading matter, and the 1180px column
 * they used to sit in spent a quarter of a wide display on empty margin while
 * squeezing the content into more, thinner pieces than it wanted; everything
 * else is prose and stays where prose belongs.
 *
 * The value lands on the frame as a custom property, so the bar and the
 * breadcrumbs line up with the page rather than each carrying a width of their
 * own — see `--page-max` in the stylesheet.
 */
function usePageWidth(): string | undefined {
  const matchRoute = useMatchRoute()

  if (matchRoute({ to: '/p/$projectKey/board' })) return styles.pageBoard
  if (
    matchRoute({ to: '/p/$projectKey' }) ||
    matchRoute({ to: '/p/$projectKey/people' }) ||
    matchRoute({ to: '/p/$projectKey/files' }) ||
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
  // The board scrolls sideways, so it gets the full window rather than the
  // reading-width column every other screen sits in.
  const wide = Boolean(matchRoute({ to: '/p/$projectKey/board' }))
  const page = usePageWidth() ?? ''

  return (
    <>
      {/*
        Eight tab stops sit between the top of the page and the content —
        wordmark, six tabs, the breadcrumb. Tabbing past them on every
        navigation is the sort of thing that makes a keyboard unusable, so
        there is a way over them.
      */}
      <a href="#content" className={styles.skip}>
        Skip to content
      </a>
      {/* The window's remaining height, split into the page and the sidebar
          beside it. The bar sits inside the page's own column rather than
          above both, so the navigation is as wide as the content it belongs
          to rather than running on underneath the panel. */}
      <div className={styles.frame}>
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
        <Sidebar />
      </div>
    </>
  )
}

/**
 * Your own mark in the bar.
 *
 * `/me` is fetched once by the authentication gate and read from the cache
 * here, so this costs nothing. Until somebody is marked as you in the
 * directory it is the plain accent — there is nobody to name yet.
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

/** Tabs across a project's four areas. Hidden outside a project. */
function ProjectTabs() {
  const matchRoute = useMatchRoute()
  const match = matchRoute({ to: '/p/$projectKey', fuzzy: true })
  if (!match) return <div />

  const { projectKey } = match

  return (
    <nav className={styles.nav}>
      <Link to="/p/$projectKey" params={{ projectKey }} activeProps={{ className: 'active' }}>
        Overview
      </Link>
      <Link to="/p/$projectKey/board" params={{ projectKey }} activeProps={{ className: 'active' }}>
        Board
      </Link>
      <Link to="/p/$projectKey/goals" params={{ projectKey }} activeProps={{ className: 'active' }}>
        Goals
      </Link>
      <Link to="/p/$projectKey/files" params={{ projectKey }} activeProps={{ className: 'active' }}>
        Files
      </Link>
      <Link to="/p/$projectKey/vault" params={{ projectKey }} activeProps={{ className: 'active' }}>
        Vault
      </Link>
      <Link
        to="/p/$projectKey/people"
        params={{ projectKey }}
        activeProps={{ className: 'active' }}
      >
        People
      </Link>
    </nav>
  )
}

function Breadcrumbs() {
  const matchRoute = useMatchRoute()
  const inProject = matchRoute({ to: '/p/$projectKey', fuzzy: true })
  const onBoard = matchRoute({ to: '/p/$projectKey/board' })
  const onFiles = matchRoute({ to: '/p/$projectKey/files' })
  const onPeople = matchRoute({ to: '/p/$projectKey/people' })
  const onVault = matchRoute({ to: '/p/$projectKey/vault' })
  // Fuzzy, so a goal's own page is still under Goals rather than nowhere.
  const onGoals = matchRoute({ to: '/p/$projectKey/goals', fuzzy: true })
  const area = onBoard
    ? 'Board'
    : onGoals
      ? 'Goals'
      : onFiles
        ? 'Files'
        : onVault
          ? 'Vault'
          : onPeople
            ? 'People'
            : null

  if (!inProject) {
    return (
      <div className={styles.crumbs}>
        <b>All projects</b>
      </div>
    )
  }

  const { projectKey } = inProject

  return (
    <div className={styles.crumbs}>
      <Link to="/">All projects</Link>
      <span className={styles.separator}>›</span>
      {area ? (
        <>
          <ProjectCrumbLink projectKey={projectKey} />
          <span className={styles.separator}>›</span>
          <b>{area}</b>
        </>
      ) : (
        <ProjectName projectKey={projectKey} />
      )}
    </div>
  )
}

function useProjectName(projectKey: string) {
  return useQuery({
    queryKey: ['project', projectKey],
    queryFn: () => api.getProject(projectKey),
  })
}

function ProjectName({ projectKey }: { projectKey: string }) {
  const project = useProjectName(projectKey)
  return <b>{project.data?.name ?? projectKey}</b>
}

function ProjectCrumbLink({ projectKey }: { projectKey: string }) {
  const project = useProjectName(projectKey)
  return (
    <Link to="/p/$projectKey" params={{ projectKey }}>
      {project.data?.name ?? projectKey}
    </Link>
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
