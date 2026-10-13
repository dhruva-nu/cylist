/** The project grid — the first thing you see — and the card that says who you are. */

import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import type { Project } from '../../api/client'
import { useProjects } from '../../api/queries'
import { PageHead } from '../../components/Shell'
import {
  Button,
  EmptyState,
  ErrorNotice,
  Eyebrow,
  LiveRegion,
  cardStyles,
  readableInkOn,
  useAnnouncer,
} from '../../components/ui'
import styles from './Home.module.css'
import { MeCard } from './MeCard'
import { NewProjectDialog } from './NewProjectDialog'

export function Home() {
  const [creating, setCreating] = useState(false)
  const { message, announce } = useAnnouncer()
  const projects = useProjects()

  return (
    <>
      <PageHead
        eyebrow={<Eyebrow>Cylist</Eyebrow>}
        title="Your projects"
        actions={
          <Button variant="go" onClick={() => setCreating(true)}>
            + New project
          </Button>
        }
      >
        Every project has a board, a file store, a vault and its people. Pick one to open it.
      </PageHead>

      <MeCard onSaved={announce} />

      {projects.isPending ? <EmptyState>Loading projects…</EmptyState> : null}

      <ErrorNotice error={projects.error} />
      <LiveRegion message={message} />

      {projects.data?.length === 0 ? (
        <EmptyState>
          No projects yet.
          <br />
          Start one and it will appear here.
        </EmptyState>
      ) : null}

      {projects.data?.length ? (
        <div className={styles.grid}>
          {projects.data.map((project) => (
            <ProjectCard key={project.id} project={project} />
          ))}
          <button className={styles.new} onClick={() => setCreating(true)}>
            + New project
          </button>
        </div>
      ) : null}

      {creating ? (
        <NewProjectDialog
          onCreated={(name) => announce(`${name} created. It is now in your project list.`)}
          onClose={() => setCreating(false)}
        />
      ) : null}
    </>
  )
}

/** One project on the grid, opening onto its board. */
function ProjectCard({ project }: { project: Project }) {
  return (
    <Link
      to="/p/$projectKey/board"
      params={{ projectKey: project.key }}
      className={`${cardStyles.card} ${cardStyles.clickable} ${styles.project}`}
    >
      <span
        className={styles.mark}
        style={{ background: project.colour, color: readableInkOn(project.colour) }}
      >
        {project.key[0]}
      </span>
      <h3>{project.name}</h3>
      <p>{project.description || 'No description yet.'}</p>
      <div className={styles.meta}>
        <span>{project.key}</span>
        <span>
          {project.member_count} {project.member_count === 1 ? 'person' : 'people'}
        </span>
      </div>
    </Link>
  )
}
