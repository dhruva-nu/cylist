/**
 * What an agent works from on a project: its skills.
 *
 * Cylist is meant to be driven by an agent as readily as by a person — the
 * MCP server and the CLI already expose the whole surface — but an agent
 * pointed at a board still had to be told what it could do by whoever was
 * holding it. These are the two halves of telling it once:
 *
 * **Skills** are what you upload: the procedures somebody has written down
 * for this project. Uploads work as the file store's do, and share its blob
 * store, with one difference — a name that already exists is *replaced*
 * rather than refused, because the name is the skill and uploading it again
 * means you have a newer version of it.
 *
 * A skill is usually more than one file, so there are two buttons rather than
 * one. *Upload a skill* takes files and makes a skill of each, which is right
 * for a page of markdown. *Upload a folder* takes a directory and makes **one**
 * skill of the whole of it — the `SKILL.md` and every script, reference and
 * asset beside it — because splitting that into a skill per file would lose
 * the only thing that made it a folder.
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

import { Link, useParams } from '@tanstack/react-router'
import { PageHead } from '../../components/Shell'
import { LiveRegion, useAnnouncer } from '../../components/ui'
import styles from './Agents.module.css'
import { Connect } from './ConnectSection'
import { Skills } from './SkillsSection'

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
