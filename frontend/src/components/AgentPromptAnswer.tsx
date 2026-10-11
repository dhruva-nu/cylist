/**
 * Answering an agent: the question or the permission, its choices as
 * buttons, and — for a question — a reply typed in your own words.
 *
 * One component for both places a person can answer from: the popover under
 * a waiting card on the board (CYLIST-86) and the agent panel on the task
 * (CYLIST-87). Both read the same `AgentPrompt` off the same session data, so
 * what can be done from a hover can be done from the card, in the same words.
 *
 * The first answer wins on the server. A second one — from another tab, or
 * the terminal getting there first — comes back as a conflict saying who
 * answered what, which is shown as it came and refreshes the card.
 */

import { useId, useState, type FormEvent } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import {
  api,
  type AgentPrompt,
  type AgentPromptAnswer,
  type Task,
  type TaskDetail,
} from '../api/client'
import { Button, ErrorBanner } from './ui'
import {
  answerFailure,
  choiceTone,
  detailWithPromptAnswered,
  promptClosedUnderUs,
  promptHeading,
  replyAnswer,
  tasksWithPromptAnswered,
} from './promptAnswer'
import styles from './AgentPromptAnswer.module.css'

/**
 * Send an answer, and draw the card answered the moment the server agrees —
 * on the board and in any open task — rather than when the socket next rings.
 */
export function useAnswerPrompt(taskRef: string, onAnswered?: (answered: AgentPrompt) => void) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ prompt, answer }: { prompt: AgentPrompt; answer: AgentPromptAnswer }) =>
      api.answerAgentPrompt(taskRef, prompt.id, answer),
    onSuccess: (answered) => {
      queryClient.setQueriesData<Task[]>({ queryKey: ['tasks'] }, (old) =>
        old ? tasksWithPromptAnswered(old, answered) : old,
      )
      queryClient.setQueriesData<TaskDetail>({ queryKey: ['task'] }, (old) =>
        old ? detailWithPromptAnswered(old, answered) : old,
      )
      void queryClient.invalidateQueries({ queryKey: ['tasks'] })
      void queryClient.invalidateQueries({ queryKey: ['task'] })
      onAnswered?.(answered)
    },
    onError: (error) => {
      // Closed before we got there: the card on screen is out of date.
      if (promptClosedUnderUs(error)) {
        void queryClient.invalidateQueries({ queryKey: ['tasks'] })
        void queryClient.invalidateQueries({ queryKey: ['task'] })
      }
    },
  })
}

export function AgentPromptAnswer({
  taskRef,
  prompt,
  now,
  variant,
  onAnswered,
}: {
  taskRef: string
  prompt: AgentPrompt
  now: Date
  /** `popover`: tight, for under a board card. `panel`: the task's own room. */
  variant: 'popover' | 'panel'
  onAnswered?: (answered: AgentPrompt) => void
}) {
  const [reply, setReply] = useState('')
  const answer = useAnswerPrompt(taskRef, onAnswered)
  const headingId = useId()
  const replyId = useId()
  const typed = replyAnswer(prompt, reply)
  const busy = answer.isPending

  const send = (chosen: AgentPromptAnswer) => {
    if (!busy) answer.mutate({ prompt, answer: chosen })
  }
  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (typed) send(typed)
  }

  return (
    <section
      className={`${styles.prompt} ${styles[variant]}`}
      aria-labelledby={headingId}
      aria-busy={busy}
    >
      <div className={styles.heading} id={headingId}>
        {promptHeading(prompt, now)}
      </div>

      {prompt.kind === 'permission' ? (
        <div className={styles.permission}>
          {prompt.tool ? <span className={styles.tool}>{prompt.tool}</span> : null}
          <code className={styles.command}>{prompt.text}</code>
        </div>
      ) : (
        <p className={styles.question}>{prompt.text}</p>
      )}

      {prompt.options.length ? (
        <div
          className={prompt.kind === 'permission' ? styles.choicesRow : styles.choices}
          role="group"
          aria-label="Choices"
        >
          {prompt.options.map((option) => {
            const tone = choiceTone(prompt, option.key)
            return (
              <Button
                key={option.key}
                variant={tone === 'go' ? 'go' : 'plain'}
                danger={tone === 'danger'}
                className={prompt.kind === 'permission' ? styles.permissionChoice : styles.choice}
                disabled={busy}
                onClick={() => send({ key: option.key })}
              >
                {option.label}
              </Button>
            )
          })}
        </div>
      ) : null}

      {prompt.allows_reply ? (
        <form className={styles.reply} onSubmit={onSubmit}>
          <label htmlFor={replyId} className="visually-hidden">
            Or write a reply
          </label>
          <input
            id={replyId}
            className={styles.replyInput}
            value={reply}
            onChange={(event) => setReply(event.target.value)}
            placeholder={prompt.options.length ? 'Or write a reply…' : 'Write a reply…'}
            disabled={busy}
            autoComplete="off"
          />
          <Button type="submit" variant="go" disabled={busy || !typed}>
            {busy ? 'Sending…' : variant === 'panel' ? 'Send answer' : 'Send'}
          </Button>
        </form>
      ) : null}

      {answer.error ? <ErrorBanner>{answerFailure(answer.error)}</ErrorBanner> : null}

      {variant === 'panel' ? (
        <div className={styles.footnote}>
          {prompt.kind === 'question'
            ? 'The agent carries on in the same turn once you answer.'
            : 'The terminal is asking too; whichever answers first wins.'}
        </div>
      ) : null}
    </section>
  )
}
