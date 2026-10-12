import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { BASE_PATH } from '../../api/basePath'
import { api, type TokenIssued } from '../../api/client'
import { useSetup } from '../../api/queries'
import { Button, ErrorBanner, ErrorNotice, cardStyles } from '../../components/ui'
import styles from './Agents.module.css'
import { Section } from './AgentsSection'
import { MCP_NAME, agentScopes, connectLine } from './connectLine'

/**
 * The line that connects Claude Code on another machine to this deployment.
 *
 * The MCP tools are served at `/mcp` by the same process as this page (see
 * `backend/app/mcp.py`), so the address in the line is this page's own origin
 * and base path — `https://host:9443/dev_1/mcp` in a dev slot:
 * whatever name the browser reached Cylist by, the machine can use too. The
 * machine installs nothing — no CLI, no uv, no Python. It needs Claude Code
 * and this one line.
 *
 * The token is minted here with the scopes `GET /setup` names for an agent —
 * the board and the vault — and acts as whoever pressed the button, exactly as
 * `cylist setup` would have made it. It is shown once, because the server never
 * shows it again.
 */
export function Connect({ announce }: { announce: (message: string) => void }) {
  const [name, setName] = useState('Claude Code')
  const [issued, setIssued] = useState<TokenIssued | null>(null)
  const [clipboardProblem, setClipboardProblem] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  const setup = useSetup()
  const mint = useMutation({
    mutationFn: (label: string) => api.createToken(label, agentScopes(setup.data)),
    // The token is in the line on screen, shown once; react-query has no
    // reason to keep a copy of it once that line is put away.
    gcTime: 0,
    onMutate: () => setClipboardProblem(null),
    onSuccess: (token) => {
      setIssued(token)
      setCopied(false)
      announce('Your connection line is ready. Copy it now; the token in it is shown only once.')
    },
  })

  const line = issued ? connectLine(window.location.origin + BASE_PATH, issued.token) : ''

  async function copy() {
    try {
      await navigator.clipboard.writeText(line)
      setCopied(true)
      announce('Copied.')
    } catch {
      // No clipboard over plain HTTP, or permission refused: the line is
      // on screen and selectable, which is the fallback that always works.
      setClipboardProblem('Could not reach the clipboard. Select the line and copy it by hand.')
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
      <ErrorNotice error={mint.error} />
      {clipboardProblem ? <ErrorBanner>{clipboardProblem}</ErrorBanner> : null}

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
