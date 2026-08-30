/** The project grid — the first thing you see — and the card that says who you are. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import { ApiError, api, type Person, type ProjectInput } from '../api/client'
import { Field, Modal, ModalBody } from '../components/Modal'
import { PersonDialog } from '../components/PersonDialog'
import { PageHead } from '../components/Shell'
import {
  Avatar,
  Button,
  EmptyState,
  ErrorBanner,
  Eyebrow,
  LiveRegion,
  cardStyles,
  readableInkOn,
  useAnnouncer,
} from '../components/ui'
import styles from './Home.module.css'

export function Home() {
  const [creating, setCreating] = useState(false)
  const { message, announce } = useAnnouncer()
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

      <MeCard onSaved={announce} />

      {projects.isPending ? <EmptyState>Loading projects…</EmptyState> : null}

      {projects.error ? <ErrorBanner>{projects.error.message}</ErrorBanner> : null}
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
            <Link
              key={project.id}
              to="/p/$projectKey"
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

/**
 * Who you are, on the screen where you start projects.
 *
 * One entry in the people directory is you, and it is put on every project you
 * create — so it belongs next to the button that creates them, not buried on a
 * project's People tab where you can only reach it once a project exists.
 */
function MeCard({ onSaved }: { onSaved: (message: string) => void }) {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState(false)
  const identity = useQuery({ queryKey: ['me'], queryFn: api.me })
  const me: Person | null = identity.data?.person ?? null

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['me'] }),
      queryClient.invalidateQueries({ queryKey: ['people'] }),
    ])
  }

  return (
    <>
      {me ? (
        <div className={`${cardStyles.card} ${styles.me}`}>
          <Avatar name={me.name} colour={me.colour} large />
          <div className={styles.meText}>
            <div className={styles.meName}>
              <b>{me.name}</b>
              <span className={styles.youTag}>you</span>
            </div>
            <span className={styles.meRole}>{me.role}</span>
            <span className={styles.meNote}>
              On every project you create, and pickable as an assignee from the moment it exists.
            </span>
          </div>
          <Button onClick={() => setEditing(true)}>Edit</Button>
        </div>
      ) : (
        <button
          className={`${cardStyles.card} ${styles.me} ${styles.meEmpty}`}
          onClick={() => setEditing(true)}
        >
          <span className={styles.meText}>
            <b>Who are you?</b>
            <span className={styles.meNote}>
              Add yourself once and you are put on every project you create.
            </span>
          </span>
          <span className={styles.meAdd}>+ Add me</span>
        </button>
      )}

      {editing ? (
        <PersonDialog
          title={me ? 'This is me' : 'Add yourself'}
          // Spread rather than `person={me ?? undefined}`: under
          // exactOptionalPropertyTypes an absent prop and an undefined one are
          // different things, and this dialog means "creating" by absence.
          {...(me ? { person: me } : {})}
          claimingMe
          onSaved={(name) => onSaved(`${name} is you.`)}
          onDone={refresh}
          onClose={() => setEditing(false)}
        />
      ) : null}
    </>
  )
}

function NewProjectDialog({
  onCreated,
  onClose,
}: {
  onCreated: (name: string) => void
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<ProjectInput>({ key: '', name: '', description: '' })

  const create = useMutation({
    mutationFn: (input: ProjectInput) => api.createProject(input),
    onSuccess: async (project) => {
      await queryClient.invalidateQueries({ queryKey: ['projects'] })
      onCreated(project.name)
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
