/**
 * A project's four areas.
 *
 * All four are live: the board, the file store, the vault and the people on
 * the project, each summarised by the counts behind its card.
 */

import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from '@tanstack/react-router'
import type { ReactNode } from 'react'
import { api } from '../api/client'
import { PageHead } from '../components/Shell'
import { EmptyState, ErrorBanner, Eyebrow, cardStyles } from '../components/ui'
import styles from './ProjectHub.module.css'

const ICONS = {
  board: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
      <rect x="3" y="4" width="5" height="16" rx="1.5" />
      <rect x="10" y="4" width="5" height="10" rx="1.5" />
      <rect x="17" y="4" width="4" height="13" rx="1.5" />
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
  people: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
      <circle cx="9" cy="8" r="3.2" />
      <path d="M3.5 19a5.5 5.5 0 0 1 11 0" />
      <circle cx="17" cy="9" r="2.5" />
      <path d="M15.5 14.5A4.5 4.5 0 0 1 21 19" />
    </svg>
  ),
}

export function ProjectHub() {
  const { projectKey } = useParams({ from: '/p/$projectKey' })
  const summary = useQuery({
    queryKey: ['project-summary', projectKey],
    queryFn: () => api.getProjectSummary(projectKey),
  })

  if (summary.isPending) return <EmptyState>Loading project…</EmptyState>
  if (summary.error) return <ErrorBanner>{summary.error.message}</ErrorBanner>

  const project = summary.data

  return (
    <>
      <PageHead eyebrow={<Eyebrow>{project.key}</Eyebrow>} title={project.name}>
        {project.description || 'No description yet.'}
      </PageHead>

      <div className={styles.grid}>
        <Tool
          icon={ICONS.people}
          title="People"
          to="/p/$projectKey/people"
          projectKey={projectKey}
          meta={
            <>
              <span>{project.team_count} team</span>
              <span>
                {project.client_count} {project.client_count === 1 ? 'client' : 'clients'}
              </span>
            </>
          }
        >
          Who is on the team, who the clients are, and what each person is responsible for.
        </Tool>

        <Tool
          icon={ICONS.board}
          title="Kanban board"
          to="/p/$projectKey/board"
          projectKey={projectKey}
          meta={
            <>
              <span>
                {project.task_count} {project.task_count === 1 ? 'task' : 'tasks'}
              </span>
              {project.on_hold_count ? (
                <span className={styles.hold}>{project.on_hold_count} on hold</span>
              ) : null}
              {project.blocked_count ? (
                <span className={styles.blocked}>{project.blocked_count} blocked</span>
              ) : null}
            </>
          }
        >
          Tasks as cards across up to 8 columns. Colour flags anything on hold or blocked.
        </Tool>

        <Tool
          icon={ICONS.files}
          title="Files"
          to="/p/$projectKey/files"
          projectKey={projectKey}
          meta={
            <>
              <span>
                {project.file_count} {project.file_count === 1 ? 'item' : 'items'}
              </span>
              <span>
                {project.folder_count} {project.folder_count === 1 ? 'folder' : 'folders'}
              </span>
            </>
          }
        >
          Folders and files on the server, with SharePoint and Google Drive links beside them.
        </Tool>

        <Tool
          icon={ICONS.vault}
          title="Vault"
          to="/p/$projectKey/vault"
          projectKey={projectKey}
          meta={
            <>
              <span>
                {project.vault_tree_count} {project.vault_tree_count === 1 ? 'tree' : 'trees'}
              </span>
              <span>
                {project.vault_secret_count}{' '}
                {project.vault_secret_count === 1 ? 'secret' : 'secrets'}
              </span>
            </>
          }
        >
          Logins, keys and links in trees you shape yourself.
        </Tool>
      </div>
    </>
  )
}

function Tool({
  icon,
  title,
  children,
  meta,
  to,
  projectKey,
  phase,
}: {
  icon: ReactNode
  title: string
  children: ReactNode
  meta?: ReactNode
  to?:
    | '/p/$projectKey/board'
    | '/p/$projectKey/files'
    | '/p/$projectKey/vault'
    | '/p/$projectKey/people'
  projectKey?: string
  phase?: string
}) {
  const inner = (
    <>
      <div className={styles.illustration}>{icon}</div>
      <h3>{title}</h3>
      <p>{children}</p>
      <div className={styles.meta}>
        {phase ? <span className={styles.badge}>{phase}</span> : meta}
      </div>
    </>
  )

  if (to && projectKey) {
    return (
      <Link
        to={to}
        params={{ projectKey }}
        className={`${cardStyles.card} ${cardStyles.clickable} ${styles.tool}`}
      >
        {inner}
      </Link>
    )
  }

  return <div className={`${cardStyles.card} ${styles.tool} ${styles.soon}`}>{inner}</div>
}
