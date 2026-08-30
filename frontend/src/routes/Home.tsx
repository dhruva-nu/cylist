/** The project grid — the first thing you see. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import { ApiError, api, type ProjectInput } from '../api/client'
import { Field, Modal, ModalBody } from '../components/Modal'
import { PageHead } from '../components/Shell'
import { Button, EmptyState, ErrorBanner, Eyebrow, cardStyles } from '../components/ui'
import styles from './Home.module.css'

export function Home() {
  const [creating, setCreating] = useState(false)
  const projects = useQuery({ queryKey: ['projects'], queryFn: api.listProjects })

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

      {projects.isPending ? <EmptyState>Loading projects…</EmptyState> : null}

      {projects.error ? <ErrorBanner>{projects.error.message}</ErrorBanner> : null}

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
            <Link
              key={project.id}
              to="/p/$projectKey"
              params={{ projectKey: project.key }}
              className={`${cardStyles.card} ${cardStyles.clickable} ${styles.project}`}
            >
              <span className={styles.mark} style={{ background: project.colour }}>
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
          ))}
          <button className={styles.new} onClick={() => setCreating(true)}>
            + New project
          </button>
        </div>
      ) : null}

      {creating ? <NewProjectDialog onClose={() => setCreating(false)} /> : null}
    </>
  )
}

function NewProjectDialog({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<ProjectInput>({ key: '', name: '', description: '' })

  const create = useMutation({
    mutationFn: (input: ProjectInput) => api.createProject(input),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['projects'] })
      onClose()
    },
  })

  const keyTaken = create.error instanceof ApiError && create.error.code === 'conflict'

  return (
    <Modal
      title="New project"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="go"
            disabled={create.isPending || !form.key || !form.name}
            onClick={() => create.mutate(form)}
          >
            {create.isPending ? 'Creating…' : 'Create project'}
          </Button>
        </>
      }
    >
      <ModalBody>
        {create.error ? <ErrorBanner>{create.error.message}</ErrorBanner> : null}
        <Field
          label="Key"
          required
          hint="2–6 letters and digits. Prefixes every task number, e.g. ATL-41."
        >
          <input
            value={form.key}
            maxLength={6}
            aria-invalid={keyTaken}
            onChange={(event) =>
              setForm({ ...form, key: event.target.value.toUpperCase().replace(/[^A-Z0-9]/g, '') })
            }
            placeholder="ATL"
          />
        </Field>
        <Field label="Name" required>
          <input
            value={form.name}
            onChange={(event) => setForm({ ...form, name: event.target.value })}
            placeholder="Atlas Billing Migration"
          />
        </Field>
        <Field label="Description">
          <textarea
            value={form.description}
            onChange={(event) => setForm({ ...form, description: event.target.value })}
            placeholder="What is this project for?"
          />
        </Field>
      </ModalBody>
    </Modal>
  )
}
