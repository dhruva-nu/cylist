/** The frame every screen sits in: wordmark, project tabs and breadcrumbs. */

import { Link, Outlet, useMatchRoute } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { api } from '../api/client'
import { Avatar } from './ui'
import styles from './Shell.module.css'

export function Shell() {
  const matchRoute = useMatchRoute()
  // The board scrolls sideways, so it gets the full window rather than the
  // reading-width column every other screen sits in.
  const wide = Boolean(matchRoute({ to: '/p/$projectKey/board' }))

  return (
    <>
      <div className={styles.top}>
        <div className={styles.bar}>
          <Link to="/" className={styles.brand}>
            <span className={styles.mark}>C</span> Cylist
          </Link>
          <ProjectTabs />
          <Avatar name="You" colour="var(--ink-2)" />
        </div>
        <Breadcrumbs />
      </div>
      <div className={`${styles.wrap} ${wide ? styles.wide : ''}`}>
        <Outlet />
      </div>
    </>
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
  const onPeople = matchRoute({ to: '/p/$projectKey/people' })

  if (!inProject) {
    return (
      <div className={styles.crumbs}>
        <b>All projects</b>
      </div>
    )
  }

  const { projectKey } = inProject
  const area = onBoard ? 'Board' : onPeople ? 'People' : null

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
      {eyebrow}
      <h1>{title}</h1>
      {children ? <p>{children}</p> : null}
      {actions ? <div className={styles.actions}>{actions}</div> : null}
    </div>
  )
}
