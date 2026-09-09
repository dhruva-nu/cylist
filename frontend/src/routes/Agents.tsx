/**
 * What an agent works from on a project: its skills, and its scratchpad.
 *
 * Cylist is meant to be driven by an agent as readily as by a person — the
 * MCP server and the CLI already expose the whole surface — but an agent
 * pointed at a board still had to be told what it could do by whoever was
 * holding it. These are the two halves of telling it once:
 *
 * **Skills** are files you upload: the procedures somebody has written down
 * for this project. Uploads work as the file store's do, and share its blob
 * store, with one difference — a name that already exists is *replaced*
 * rather than refused, because the name is the skill and uploading it again
 * means you have a newer version of it.
 *
 * **The scratchpad** is the other direction: short lines an agent writes when
 * it works something out that the next agent would otherwise work out again.
 * Capped at 280 characters by the server, which is the point rather than a
 * limitation — see `NOTE_MAX_LENGTH`.
 *
 * A project's screen rather than the platform's, because that is where an
 * agent's authority is decided: a token is scoped per project, so what an
 * agent may do on one board is not what it may do on the next.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams } from '@tanstack/react-router'
import { useRef, useState, type ReactNode } from 'react'
import { NOTE_MAX_LENGTH, api, type AgentNote, type Person, type Skill } from '../api/client'
import { formatSize, formatStamp } from '../components/format'
import { PageHead } from '../components/Shell'
import {
  Avatar,
  Button,
  EmptyState,
  ErrorBanner,
  LiveRegion,
  cardStyles,
  useAnnouncer,
} from '../components/ui'
import styles from './Agents.module.css'

export function Agents() {
  const { projectKey } = useParams({ from: '/p/$projectKey/agents' })
  const { message, announce } = useAnnouncer()

  return (
    <>
      <PageHead title="Agents">
        What an agent works from on this project. <b>Skills</b> are the procedures you have written
        down for it; the <b>scratchpad</b> is where it writes back what it learned.
      </PageHead>

      <LiveRegion message={message} />
      <Skills projectKey={projectKey} announce={announce} />
      <Scratchpad projectKey={projectKey} announce={announce} />
    </>
  )
}

/** A heading over one half of the screen, with its count and its action. */
function Section({
  title,
  blurb,
  count,
  actions,
  children,
}: {
  title: string
  blurb: ReactNode
  count?: ReactNode
  actions?: ReactNode
  children: ReactNode
}) {
  return (
    <section className={styles.section}>
      <div className={styles.sectionHead}>
        <div className={styles.sectionText}>
          <h2>
            {title}
            {count !== undefined ? <span className={styles.count}>{count}</span> : null}
          </h2>
          <p>{blurb}</p>
        </div>
        {actions ? <div className={styles.sectionActions}>{actions}</div> : null}
      </div>
      {children}
    </section>
  )
}

function Skills({
  projectKey,
  announce,
}: {
  projectKey: string
  announce: (message: string) => void
}) {
  const queryClient = useQueryClient()
  const [problem, setProblem] = useState<string | null>(null)
  const picker = useRef<HTMLInputElement>(null)

  const skills = useQuery({
    queryKey: ['skills', projectKey],
    queryFn: () => api.listSkills(projectKey),
  })

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['skills', projectKey] }),
      // The hub card counts them.
      queryClient.invalidateQueries({ queryKey: ['project-summary', projectKey] }),
    ])
  }

  const upload = useMutation({
    // One at a time rather than in parallel: two uploads of the same name
    // would race to replace each other, and the survivor would be whichever
    // request the server happened to finish second.
    mutationFn: async (files: File[]) => {
      const replaced: string[] = []
      for (const file of files) {
        const before = skills.data?.some((skill) => skill.name === file.name) ?? false
        await api.uploadSkill(projectKey, file)
        if (before) replaced.push(file.name)
      }
      return { count: files.length, replaced }
    },
    onMutate: () => setProblem(null),
    onSuccess: async ({ count, replaced }) => {
      await refresh()
      const noun = count === 1 ? 'skill' : 'skills'
      announce(
        replaced.length
          ? `${count} ${noun} uploaded. ${replaced.join(', ')} replaced an earlier version.`
          : `${count} ${noun} uploaded.`,
      )
    },
    onError: (error: Error) => setProblem(error.message),
  })

  const remove = useMutation({
    mutationFn: ({ id }: { id: string; name: string }) => api.deleteSkill(id),
    onSuccess: async (_gone, { name }) => {
      await refresh()
      announce(`${name} deleted.`)
    },
    onError: (error: Error) => setProblem(error.message),
  })

  return (
    <Section
      title="Skills"
      blurb="Packaged jobs an agent can be handed. Uploading a name that already exists replaces it — the name is the skill, so a second upload is a new version of it."
      count={skills.data?.length}
      actions={
        <>
          {/* The real input, driven by the button beside it: a bare file input
              cannot be styled to match anything else on the page. */}
          <input
            ref={picker}
            type="file"
            multiple
            className="visually-hidden"
            onChange={(event) => {
              const chosen = Array.from(event.target.files ?? [])
              if (chosen.length) upload.mutate(chosen)
              // Cleared so that picking the same file twice fires twice —
              // which is exactly what re-uploading a corrected skill is.
              event.target.value = ''
            }}
          />
          <Button variant="go" disabled={upload.isPending} onClick={() => picker.current?.click()}>
            {upload.isPending ? 'Uploading…' : '+ Upload a skill'}
          </Button>
        </>
      }
    >
      {problem ? <ErrorBanner>{problem}</ErrorBanner> : null}
      {skills.error ? <ErrorBanner>{skills.error.message}</ErrorBanner> : null}

      {skills.isPending ? <EmptyState>Loading skills…</EmptyState> : null}

      {skills.data?.length === 0 ? (
        <EmptyState>
          No skills yet.
          <br />
          Upload the markdown an agent should follow here — a board to tidy, a report to write.
        </EmptyState>
      ) : null}

      {skills.data?.length ? (
        <div className={styles.skills}>
          {skills.data.map((skill) => (
            <SkillRow
              key={skill.id}
              skill={skill}
              busy={remove.isPending}
              onDelete={() => remove.mutate({ id: skill.id, name: skill.name })}
            />
          ))}
        </div>
      ) : null}
    </Section>
  )
}

function SkillRow({
  skill,
  busy,
  onDelete,
}: {
  skill: Skill
  busy: boolean
  onDelete: () => void
}) {
  const [confirming, setConfirming] = useState(false)

  return (
    <div className={`${cardStyles.card} ${styles.skill}`}>
      <span className={styles.skillMark} aria-hidden="true">
        {extensionOf(skill.name)}
      </span>
      <div className={styles.skillText}>
        <a className={styles.skillName} href={api.skillDownloadUrl(skill.id)} download={skill.name}>
          {skill.name}
        </a>
        {skill.description ? <span className={styles.skillNote}>{skill.description}</span> : null}
        <span className={styles.skillMeta}>
          {formatSize(skill.size)} · {formatStamp(skill.created_at)}
          {skill.added_by ? <> · {skill.added_by.name}</> : null}
        </span>
      </div>
      {confirming ? (
        <div className={styles.confirm}>
          <span>Delete it?</span>
          <Button small danger disabled={busy} onClick={onDelete}>
            Delete
          </Button>
          <Button small onClick={() => setConfirming(false)}>
            Keep
          </Button>
        </div>
      ) : (
        <Button small onClick={() => setConfirming(true)}>
          Delete
        </Button>
      )}
    </div>
  )
}

/** The four-or-fewer characters that stand in for a file's type. */
function extensionOf(name: string): string {
  const extension = name.includes('.') ? (name.split('.').pop() ?? '') : ''
  return extension ? extension.slice(0, 4).toUpperCase() : 'FILE'
}

function Scratchpad({
  projectKey,
  announce,
}: {
  projectKey: string
  announce: (message: string) => void
}) {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState('')
  const [problem, setProblem] = useState<string | null>(null)

  const notes = useQuery({
    queryKey: ['agent-notes', projectKey],
    queryFn: () => api.listAgentNotes(projectKey),
  })

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['agent-notes', projectKey] }),
      queryClient.invalidateQueries({ queryKey: ['project-summary', projectKey] }),
    ])
  }

  const add = useMutation({
    mutationFn: (body: string) => api.addAgentNote(projectKey, body),
    onMutate: () => setProblem(null),
    onSuccess: async () => {
      setDraft('')
      await refresh()
      announce('Noted on the scratchpad.')
    },
    onError: (error: Error) => setProblem(error.message),
  })

  const remove = useMutation({
    mutationFn: (id: string) => api.deleteAgentNote(id),
    onSuccess: async () => {
      await refresh()
      announce('Note rubbed off.')
    },
    onError: (error: Error) => setProblem(error.message),
  })

  const trimmed = draft.trim()
  const left = NOTE_MAX_LENGTH - draft.length

  return (
    <Section
      title="Agent's scratchpad"
      blurb="One line each, newest first — what an agent learned about this project that it would otherwise have to work out again. Agents write here over MCP with note_learned; you can add and remove lines yourself."
      count={notes.data?.length}
    >
      {problem ? <ErrorBanner>{problem}</ErrorBanner> : null}
      {notes.error ? <ErrorBanner>{notes.error.message}</ErrorBanner> : null}

      <form
        className={`${cardStyles.card} ${styles.compose}`}
        onSubmit={(event) => {
          event.preventDefault()
          if (trimmed) add.mutate(trimmed)
        }}
      >
        <label className="visually-hidden" htmlFor="new-note">
          Something learned
        </label>
        <input
          id="new-note"
          value={draft}
          maxLength={NOTE_MAX_LENGTH}
          placeholder="Something you had to work out — in a sentence."
          onChange={(event) => setDraft(event.target.value)}
        />
        {/* Only once it is close enough to matter: a counter that is always on
            reads as a limit you are working against rather than a cap you
            will not meet. */}
        <span className={`${styles.left} ${left < 0 ? styles.leftOver : ''}`}>
          {left <= 60 ? left : ''}
        </span>
        <Button variant="go" type="submit" disabled={!trimmed || add.isPending}>
          {add.isPending ? 'Noting…' : 'Note it'}
        </Button>
      </form>

      {notes.isPending ? <EmptyState>Loading the scratchpad…</EmptyState> : null}

      {notes.data?.length === 0 ? (
        <EmptyState>
          Nothing learned here yet.
          <br />
          An agent adds a line when it works something out; so can you.
        </EmptyState>
      ) : null}

      {notes.data?.length ? (
        <ol className={styles.notes}>
          {notes.data.map((note) => (
            <NoteRow
              key={note.id}
              note={note}
              busy={remove.isPending}
              onDelete={() => remove.mutate(note.id)}
            />
          ))}
        </ol>
      ) : null}
    </Section>
  )
}

function NoteRow({
  note,
  busy,
  onDelete,
}: {
  note: AgentNote
  busy: boolean
  onDelete: () => void
}) {
  const person: Person | null = note.added_by

  return (
    <li className={styles.note}>
      <p className={styles.noteBody}>{note.body}</p>
      <div className={styles.noteMeta}>
        {person ? (
          <Avatar name={person.name} colour={person.colour} small />
        ) : (
          <span className={styles.botMark} aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
              <rect x="4" y="7" width="16" height="12" rx="3" />
              <path d="M12 7V4" />
              <circle cx="9.5" cy="13" r="1.2" fill="currentColor" stroke="none" />
              <circle cx="14.5" cy="13" r="1.2" fill="currentColor" stroke="none" />
            </svg>
          </span>
        )}
        <span className={styles.noteWho}>{person ? person.name : note.author_label}</span>
        <span className={styles.noteWhen}>{formatStamp(note.created_at)}</span>
        <button
          className={styles.rub}
          disabled={busy}
          onClick={onDelete}
          title="Rub this line off — it has stopped being true"
        >
          ×
        </button>
      </div>
    </li>
  )
}
