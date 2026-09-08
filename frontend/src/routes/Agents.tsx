/**
 * What an agent needs to work on this project, in one place.
 *
 * Cylist is meant to be driven by an agent as readily as by a person — the
 * MCP server and the CLI already expose the whole surface — but nothing on
 * screen says so, and an agent pointed at a board has to be told what it can
 * do by whoever is holding it. This is where that gets written down: the
 * skills an agent can be given, the documentation of the platform, and the
 * standing instructions it operates under.
 *
 * A project's area rather than the platform's, because that is where the
 * token an agent is minted lives: scopes are granted per project, and what an
 * agent may do on one board is not what it may do on the next.
 *
 * Placeholders for now. Each section names what belongs in it and says so on
 * the card, so the shape of the screen is settled before the content that
 * fills it is — and so nothing here reads as a feature that is merely broken.
 */

import { useParams } from '@tanstack/react-router'
import { type ReactNode } from 'react'
import { PageHead } from '../components/Shell'
import { cardStyles } from '../components/ui'
import styles from './Agents.module.css'

const ICONS = {
  skills: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
      <path d="M12 3l2.5 5.2 5.5.8-4 3.9 1 5.6-5-2.7-5 2.7 1-5.6-4-3.9 5.5-.8z" />
    </svg>
  ),
  docs: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
      <path d="M5 4h9l5 5v11a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z" />
      <path d="M14 4v5h5" />
      <path d="M8 13h7M8 17h5" />
    </svg>
  ),
  instructions: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
      <rect x="4" y="3" width="16" height="18" rx="2" />
      <path d="M8 8.5l1.6 1.6L12.5 7" />
      <path d="M8 15.5l1.6 1.6L12.5 14" />
      <path d="M15 9.5h2M15 16.5h2" />
    </svg>
  ),
}

export function Agents() {
  const { projectKey } = useParams({ from: '/p/$projectKey/agents' })

  return (
    <>
      <PageHead title="Agents">
        Everything an agent needs to operate and understand Cylist on {projectKey}: the skills it
        can be given, the documentation of the platform, and the instructions it works under.
        Nothing is written yet — these are the places it will go.
      </PageHead>

      <div className={styles.grid}>
        <Resource icon={ICONS.skills} title="Skills">
          The packaged jobs an agent can be handed — tidy a board, write a day report, take a card
          from open to done — each with the tools it needs and the scopes to mint a token for.
        </Resource>

        <Resource icon={ICONS.docs} title="Documentation">
          How the platform is put together: projects and their keys, the board and its columns,
          goals, sub-tasks, files and the vault, and the API and MCP surface over all of it.
        </Resource>

        <Resource icon={ICONS.instructions} title="Instructions">
          The standing rules an agent works under here — a status that needs a reason, a name used
          where a human would use one, and what an agent is never to be given.
        </Resource>
      </div>
    </>
  )
}

/**
 * One place agent material will live.
 *
 * A card rather than a link: there is nothing behind it yet. It says
 * "placeholder" on its face for the same reason it is not clickable — an
 * empty section a reader can open is a bug, and one that says it is empty is
 * a plan.
 *
 * The badge carries that on its own, and the card is drawn at full strength:
 * dimming the whole tile the way a `soon` card does takes the description
 * with it, and the description is the part that says what will be here.
 */
function Resource({
  icon,
  title,
  children,
}: {
  icon: ReactNode
  title: string
  children: ReactNode
}) {
  return (
    <div className={`${cardStyles.card} ${styles.resource}`}>
      <div className={styles.illustration}>{icon}</div>
      <h3>{title}</h3>
      <p>{children}</p>
      <div className={styles.meta}>
        <span className={styles.badge}>Placeholder</span>
      </div>
    </div>
  )
}
