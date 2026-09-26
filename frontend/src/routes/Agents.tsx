/**
 * What an agent works from on a project: its skills.
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
 * What an agent learns goes the other way, into the project's **Docs**: a line
 * on the `learned.md` of whichever topic it belongs to. That used to be a
 * scratchpad on this page; it moved so that what agents learn sits beside
 * everything else written about the project, and is handed to the next agent
 * with the rest of the docs its card needs.
 *
 * A project's screen rather than the platform's, because that is where an
 * agent's authority is decided: a token is scoped per project, so what an
 * agent may do on one board is not what it may do on the next.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from '@tanstack/react-router'
import { useRef, useState, type ReactNode } from 'react'
import { api, type Skill, type TokenIssued } from '../api/client'
import { formatSize, formatStamp } from '../components/format'
import { PageHead } from '../components/Shell'
import {
  Button,
  EmptyState,
  ErrorBanner,
  LiveRegion,
  cardStyles,
  useAnnouncer,
} from '../components/ui'
import styles from './Agents.module.css'
import { MCP_NAME, connectLine } from './connectLine'
import { usePermissions } from './usePermissions'

export function Agents() {
  const { projectKey } = useParams({ from: '/p/$projectKey/agents' })
  const { message, announce } = useAnnouncer()

  return (
    <>
      <PageHead title="Agents">
        What an agent works from on this project. <b>Skills</b> are the procedures you have written
        down for it. What it learns, it writes back into the project's{' '}
        <Link to="/p/$projectKey/docs" params={{ projectKey }} className={styles.docsLink}>
          Docs
        </Link>{' '}
        — a line on the <code>learned.md</code> of the topic it belongs to.
      </PageHead>

      <LiveRegion message={message} />
      <Connect announce={announce} />
      <Skills projectKey={projectKey} announce={announce} />
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

/**
 * The line that connects Claude Code on another machine to this deployment.
 *
 * The MCP tools are served at `/mcp` by the same process as this page (see
 * `backend/app/mcp.py`), so the address in the line is this page's own origin:
 * whatever name the browser reached Cylist by, the machine can use too. The
 * machine installs nothing — no CLI, no uv, no Python. It needs Claude Code
 * and this one line.
 *
 * The token is minted here, `read,write` only, and acts as whoever pressed the
 * button, exactly as `cylist setup` would have made it. It is shown once,
 * because the server never shows it again.
 */
function Connect({ announce }: { announce: (message: string) => void }) {
  const [name, setName] = useState('Claude Code')
  const [issued, setIssued] = useState<TokenIssued | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  const mint = useMutation({
    mutationFn: (label: string) => api.createToken(label, ['read', 'write']),
    onMutate: () => setProblem(null),
    onSuccess: (token) => {
      setIssued(token)
      setCopied(false)
      announce('Your connection line is ready. Copy it now; the token in it is shown only once.')
    },
    onError: (error: Error) => setProblem(error.message),
  })

  const line = issued ? connectLine(window.location.origin, issued.token) : ''

  async function copy() {
    try {
      await navigator.clipboard.writeText(line)
      setCopied(true)
      announce('Copied.')
    } catch {
      // No clipboard over plain HTTP, or permission refused: the line is
      // on screen and selectable, which is the fallback that always works.
      setProblem('Could not reach the clipboard. Select the line and copy it by hand.')
    }
  }

  const trimmed = name.trim()

  return (
    <Section
      title="Connect Claude Code"
      blurb={
        <>
          One line, run on the machine that should use this board. It needs Claude Code and nothing
          else: no CLI, no install. The token it carries can read and change the board as you, and
          cannot reveal vault secrets.
        </>
      }
    >
      {problem ? <ErrorBanner>{problem}</ErrorBanner> : null}

      {issued ? (
        <div className={`${cardStyles.card} ${styles.connect}`}>
          <pre className={styles.line} tabIndex={0} aria-label="The command to run">
            {line}
          </pre>
          <div className={styles.connectActions}>
            <Button variant="go" onClick={() => void copy()}>
              {copied ? 'Copied' : 'Copy'}
            </Button>
            <Button onClick={() => setIssued(null)}>Done</Button>
          </div>
          <p className={styles.connectNote}>
            Shown once; copy it now. Run it in a terminal on the machine, then start Claude Code
            there. If that machine was set up with <code>cylist setup</code> before, run{' '}
            <code>claude mcp remove {MCP_NAME} --scope user</code> first.
          </p>
        </div>
      ) : (
        <form
          className={`${cardStyles.card} ${styles.compose}`}
          onSubmit={(event) => {
            event.preventDefault()
            if (trimmed) mint.mutate(trimmed)
          }}
        >
          <label className="visually-hidden" htmlFor="connect-name">
            Which machine this is for
          </label>
          <input
            id="connect-name"
            value={name}
            maxLength={120}
            placeholder="Which machine, e.g. Claude Code on the Windows laptop"
            onChange={(event) => setName(event.target.value)}
          />
          <Button variant="go" type="submit" disabled={!trimmed || mint.isPending}>
            {mint.isPending ? 'Minting…' : 'Get the line'}
          </Button>
        </form>
      )}
    </Section>
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
  const may = usePermissions(projectKey)
  const [problem, setProblem] = useState<string | null>(null)
  const filePicker = useRef<HTMLInputElement>(null)

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
        const alreadyThere = skills.data?.some((skill) => skill.name === file.name) ?? false
        await api.uploadSkill(projectKey, file)
        if (alreadyThere) replaced.push(file.name)
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

  const deleteSkill = useMutation({
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
        may('agents') ? (
          <>
            {/* The real input, driven by the button beside it: a bare file
                input cannot be styled to match anything else on the page. */}
            <input
              ref={filePicker}
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
            <Button
              variant="go"
              disabled={upload.isPending}
              onClick={() => filePicker.current?.click()}
            >
              {upload.isPending ? 'Uploading…' : '+ Upload a skill'}
            </Button>
          </>
        ) : null
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
              busy={deleteSkill.isPending}
              onDelete={() => deleteSkill.mutate({ id: skill.id, name: skill.name })}
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
        {fileTypeMark(skill.name)}
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
function fileTypeMark(name: string): string {
  const extension = name.includes('.') ? (name.split('.').pop() ?? '') : ''
  return extension ? extension.slice(0, 4).toUpperCase() : 'FILE'
}
