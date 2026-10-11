/**
 * The agent panel on a task (CYLIST-87): every Claude Code session that has
 * been on this card, what each is doing now, what it is waiting on you for —
 * answerable right here — and the live feed of what they read, edited, ran
 * and said.
 *
 * Everything the board's hover popover can do is done here too, with the same
 * component (`AgentPromptAnswer`) reading the same `prompt` off the same
 * session data. This is where a phone gets to it: a tap on a waiting card
 * opens the card rather than the popover.
 *
 * The card's border says one thing about all of the sessions — whichever needs
 * a human first. This is where the rest is: which session, its state and
 * since when, how long ago it last said anything. A card worked on from two
 * terminals shows two, which is the whole reason the border carries a count.
 *
 * **No Pause or Stop.** The design draws them, but nothing can carry them:
 * the server's only message down an agent's socket is the answer to a
 * permission, and Claude Code offers a hook no way to pause a session, only
 * to refuse its next tool call. A button that cannot do what it says is worse
 * than none, so there is none until there is a channel for it.
 *
 * Dismiss clears the finished sessions. It appears only once there is
 * something to clear: a session still running cannot be dismissed, and a
 * button that would refuse is worse than one that is not there. Editing the
 * card does the same thing without the button — the server treats a person
 * touching the card as having seen what the agent left.
 */

import { useId } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api, type AgentPrompt, type AgentSessionRead } from '../api/client'
import { isAnswerable, nowLine, promptLine, sessionPill, silentFor } from '../routes/agentState'
import { feedClock, feedLabel, isCodeLike } from './agentFeed'
import { AgentPromptAnswer } from './AgentPromptAnswer'
import { Button, ErrorBanner } from './ui'
import styles from './AgentPanel.module.css'

export function AgentPanel({
  taskRef,
  taskId,
  sessions,
}: {
  taskRef: string
  taskId: string
  sessions: AgentSessionRead[]
}) {
  const queryClient = useQueryClient()
  const headingId = useId()
  const now = new Date()
  const finished = sessions.filter((session) => session.ended_at !== null)
  const live = sessions.length > finished.length

  const dismiss = useMutation({
    mutationFn: () => api.dismissAgentSessions(taskRef),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['task', taskId] }),
        queryClient.invalidateQueries({ queryKey: ['tasks'] }),
        queryClient.invalidateQueries({ queryKey: ['board'] }),
      ])
    },
  })

  return (
    <section className={styles.panel} aria-labelledby={headingId}>
      <h4 className={styles.title} id={headingId}>
        {sessions.length > 1 ? `Agents · ${sessions.length}` : 'Agent'}
      </h4>

      {sessions.map((session) => (
        <SessionBlock key={session.id} taskRef={taskRef} session={session} now={now} />
      ))}

      {dismiss.error ? <ErrorBanner>{dismiss.error.message}</ErrorBanner> : null}
      {finished.length ? (
        <div className={styles.actions}>
          <Button small onClick={() => dismiss.mutate()} disabled={dismiss.isPending}>
            {dismiss.isPending ? 'Dismissing…' : 'Dismiss'}
          </Button>
          <span className={styles.quiet}>
            {finished.length === 1
              ? 'Takes the finished session off the board.'
              : `Takes the ${finished.length} finished sessions off the board.`}
          </span>
        </div>
      ) : null}

      <div className={styles.feedHead}>{live ? 'Live feed' : 'Feed'}</div>
      <AgentFeed taskRef={taskRef} taskId={taskId} live={live} />
    </section>
  )
}

/** One session: who, its state since when, what it is doing, what it asks. */
function SessionBlock({
  taskRef,
  session,
  now,
}: {
  taskRef: string
  session: AgentSessionRead
  now: Date
}) {
  const pill = sessionPill(session)
  const prompt = session.prompt

  return (
    <div className={styles.session}>
      <div className={styles.sessionHead}>
        <div className={styles.who}>
          <span className={styles.name}>{session.client_name ?? session.actor_label}</span>
          <span className={styles.quiet}>
            {/* The record is what says a person did not do this — the same
                wording the history uses. */}
            <span className={styles.agentTag}>agent</span> {session.actor_label} · {pill.sinceVerb}{' '}
            <time dateTime={pill.sinceAt}>{clock(pill.sinceAt, now)}</time> · last seen{' '}
            {silentFor(session.last_seen_at, now)} ago
          </span>
        </div>
        <span className={`${styles.pill} ${styles[`pill_${pill.tone}`]}`}>
          <span className={styles.pillDot} aria-hidden="true" />
          {pill.label}
        </span>
      </div>

      {/* While it is asking, the box below says what it is waiting on — in
          full, with the buttons — and a Now line repeating it is noise. */}
      {isAnswerable(prompt, now) ? null : <NowLine session={session} />}

      {isAnswerable(prompt, now) ? (
        <AgentPromptAnswer taskRef={taskRef} prompt={prompt} now={now} variant="panel" />
      ) : prompt ? (
        <ClosedPrompt prompt={prompt} working={session.state === 'working' && !session.ended_at} />
      ) : null}
    </div>
  )
}

/**
 * What an open session is doing this moment — "Running pytest
 * test_pr_states.py" — which changes with every tool call it makes. Nothing
 * for a session that has ended: it is not doing anything now.
 */
function NowLine({ session }: { session: AgentSessionRead }) {
  const line = nowLine(session)
  if (line === null) return null

  return (
    <div className={styles.now}>
      <span className={styles.nowLabel}>Now</span>
      <span className={styles.nowText}>{line}</span>
    </div>
  )
}

/**
 * The last prompt, once it closed: what was answered and by whom, or why it
 * closed without an answer — kept until the session next changes state, the
 * same as the "You: …" on the card.
 */
function ClosedPrompt({ prompt, working }: { prompt: AgentPrompt; working: boolean }) {
  if (prompt.status === 'answered') {
    return (
      <div className={styles.answered} role="status">
        <span className={styles.answeredLabel}>
          {prompt.answered_by ? `${prompt.answered_by} answered:` : 'Answered:'}
        </span>{' '}
        {prompt.answer}
        <div className={styles.answeredNote}>
          {working ? 'Delivered. The agent is working again.' : 'Delivered.'}
        </div>
      </div>
    )
  }
  const line = promptLine(prompt)
  return line ? <div className={styles.quiet}>{line}</div> : null
}

const FEED_POLL_MS = 10_000

/**
 * What the agents on this card read, edited, ran and said, newest first.
 *
 * Each line was written by a session's presence daemon as it followed the
 * Claude Code transcript, and the board socket says `agent.feed` for this card
 * when there is a new one — `useBoardSocket` turns that into a refetch of this
 * query, so the feed moves while the agent works without this dialog polling.
 * Kept on the server, so a card opened halfway through a session shows how it
 * got there.
 */
function AgentFeed({ taskRef, taskId, live }: { taskRef: string; taskId: string; live: boolean }) {
  const feed = useQuery({
    queryKey: ['agent-feed', taskId],
    queryFn: () => api.getAgentFeed(taskRef),
    // Opened from Today or a goal's page there is no board socket to ring,
    // so while an agent is still on the card the feed asks for itself — at
    // the board's own fallback pace, and not at all once every session is over.
    refetchInterval: live ? FEED_POLL_MS : false,
  })

  if (feed.error) return <ErrorBanner>{feed.error.message}</ErrorBanner>
  if (!feed.data) return <span className={styles.quiet}>Loading…</span>
  if (!feed.data.length) {
    return (
      <span className={styles.quiet}>
        Nothing yet. Lines appear here as the agent reads, edits, runs and says things.
      </span>
    )
  }

  return (
    <ol className={styles.feed}>
      {feed.data.map((entry) => (
        <li key={entry.id} className={`${styles.feedLine} ${styles[`feed_${entry.kind}`]}`}>
          <time className={styles.feedTime} dateTime={entry.at}>
            {feedClock(entry.at)}
          </time>
          <span className={styles.feedKind}>{feedLabel(entry.kind)}</span>
          <span className={isCodeLike(entry.kind) ? styles.feedCode : styles.feedText}>
            {entry.text}
          </span>
        </li>
      ))}
    </ol>
  )
}

/** "14:29" today, "11 Oct, 14:29" on any other day. */
function clock(iso: string, now: Date): string {
  const at = new Date(iso)
  const time = at.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
  if (at.toDateString() === now.toDateString()) return time
  return `${at.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}, ${time}`
}
