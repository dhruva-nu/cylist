/** The project grid — the first thing you see — and the card that says who you are. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import {
  ApiError,
  MIN_PASSWORD_LENGTH,
  api,
  type Person,
  type Project,
  type ProjectInput,
} from '../api/client'
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

/**
 * Who you are, on the screen where you start projects.
 *
 * You are whoever signed in, and you are put on every project you create — so
 * this belongs next to the button that creates them, not buried on a project's
 * People tab where you can only reach it once a project exists.
 *
 * It has one other job, and only ever once per deployment. A session with no
 * person behind it is the bootstrap session: somebody signed in with
 * `CYLIST_PASSWORD_HASH` on a deployment that has no accounts yet. The only
 * useful thing to do from there is open the first account, and this card is
 * where that happens — the empty state of "who are you?" turning out to be
 * exactly the right question.
 */
function MeCard({ onSaved }: { onSaved: (message: string) => void }) {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [changingPassword, setChangingPassword] = useState(false)
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
            <span className={styles.meRole}>{me.title}</span>
            <span className={styles.meNote}>
              On every project you create, and pickable as an assignee from the moment it exists.
            </span>
          </div>
          <Button onClick={() => setChangingPassword(true)}>Password</Button>
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
              You are signed in with this deployment's password, which belongs to nobody. Open an
              account and it stops working.
            </span>
          </span>
          <span className={styles.meAdd}>+ Open my account</span>
        </button>
      )}

      {editing && me ? (
        <PersonDialog
          title="This is me"
          person={me}
          onSaved={(name) => onSaved(`${name} saved.`)}
          onDone={refresh}
          onClose={() => setEditing(false)}
        />
      ) : null}

      {editing && !me ? (
        <FirstAccountDialog onDone={refresh} onClose={() => setEditing(false)} />
      ) : null}

      {changingPassword ? (
        <ChangePasswordDialog
          onSaved={() => onSaved('Password changed. Any other session you had is signed out.')}
          onClose={() => setChangingPassword(false)}
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

/**
 * Opening the first account on a deployment that has none.
 *
 * Three server calls behind one form, and they are three rather than one on
 * purpose: this is the ordinary invite flow — add a person, invite them,
 * accept the invitation — run end to end by the one person who is both the
 * sender and the recipient. Giving the bootstrap session an endpoint of its
 * own that skipped it would be a second way to mint an account, and the
 * second way is the one that does not get audited.
 *
 * The invitation token never leaves this component; it is made and spent in
 * the same submit.
 */
function FirstAccountDialog({
  onDone,
  onClose,
}: {
  onDone: () => Promise<void>
  onClose: () => void
}) {
  const [form, setForm] = useState({ name: '', title: '', email: '', password: '' })

  const openAccount = useMutation({
    mutationFn: async () => {
      const person = await api.createPerson({
        name: form.name.trim(),
        kind: 'team',
        title: form.title.trim(),
        responsibilities: 'Runs this Cylist.',
        email: form.email.trim(),
      })
      const invitation = await api.invitePerson(person.id)
      // Accepting replaces the bootstrap cookie with this person's session,
      // which is why nothing after this point needs the old one.
      await api.acceptInvite(invitation.token, form.password)
    },
    onSuccess: async () => {
      await onDone()
      onClose()
    },
  })

  const ready =
    form.name.trim() &&
    form.title.trim() &&
    form.email.trim() &&
    form.password.length >= MIN_PASSWORD_LENGTH

  return (
    <Modal
      title="Open your account"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="go"
            disabled={openAccount.isPending || !ready}
            onClick={() => openAccount.mutate()}
          >
            {openAccount.isPending ? 'Opening…' : 'Open account'}
          </Button>
        </>
      }
    >
      <ModalBody>
        {openAccount.error ? <ErrorBanner>{openAccount.error.message}</ErrorBanner> : null}
        <Field label="Name" required>
          <input
            value={form.name}
            onChange={(event) => setForm({ ...form, name: event.target.value })}
          />
        </Field>
        <Field label="Who are you?" required>
          <input
            value={form.title}
            onChange={(event) => setForm({ ...form, title: event.target.value })}
            placeholder="Tech lead"
          />
        </Field>
        <Field label="Email" required hint="What you will sign in with from now on.">
          <input
            type="email"
            autoComplete="username"
            value={form.email}
            onChange={(event) => setForm({ ...form, email: event.target.value })}
          />
        </Field>
        <Field label="Password" required hint={`At least ${MIN_PASSWORD_LENGTH} characters.`}>
          <input
            type="password"
            autoComplete="new-password"
            value={form.password}
            onChange={(event) => setForm({ ...form, password: event.target.value })}
          />
        </Field>
      </ModalBody>
    </Modal>
  )
}

/**
 * Changing your own password.
 *
 * Proving the current one is what makes this a password change rather than a
 * password reset, and a reset is not something a session should be able to do
 * — an unattended browser would be one.
 *
 * Every other credential you hold goes with it: other browsers, and the tokens
 * your agents are using. That is the point of changing a password under
 * suspicion, so it is said out loud rather than discovered when a laptop
 * elsewhere stops working. The session doing it is re-issued, so this browser
 * stays signed in.
 */
function ChangePasswordDialog({ onSaved, onClose }: { onSaved: () => void; onClose: () => void }) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [repeated, setRepeated] = useState('')

  const change = useMutation({
    mutationFn: () => api.changePassword(current, next),
    onSuccess: () => {
      onSaved()
      onClose()
    },
  })

  const mismatched = repeated.length > 0 && repeated !== next
  const ready = current.length > 0 && next.length >= MIN_PASSWORD_LENGTH && repeated === next

  return (
    <Modal
      title="Change your password"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="go"
            disabled={change.isPending || !ready}
            onClick={() => change.mutate()}
          >
            {change.isPending ? 'Changing…' : 'Change password'}
          </Button>
        </>
      }
    >
      <ModalBody>
        {change.error ? <ErrorBanner>{change.error.message}</ErrorBanner> : null}
        <Field label="Current password" required>
          <input
            type="password"
            autoComplete="current-password"
            value={current}
            onChange={(event) => setCurrent(event.target.value)}
          />
        </Field>
        <Field
          label="New password"
          required
          hint={`At least ${MIN_PASSWORD_LENGTH} characters. Length, not symbols.`}
        >
          <input
            type="password"
            autoComplete="new-password"
            value={next}
            onChange={(event) => setNext(event.target.value)}
          />
        </Field>
        <Field label="Repeat it" required>
          <input
            type="password"
            autoComplete="new-password"
            value={repeated}
            onChange={(event) => setRepeated(event.target.value)}
          />
        </Field>
        {mismatched ? <ErrorBanner>Those two do not match.</ErrorBanner> : null}
        <p className={styles.meNote}>
          This browser stays signed in. Every other session you have, and every agent token you
          minted, stops working.
        </p>
      </ModalBody>
    </Modal>
  )
}
