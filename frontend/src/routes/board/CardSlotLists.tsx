/**
 * What the marks on a card open into, in the one slot under its bottom line:
 * the stages, the sub-tasks, the pull requests.
 */

import { useMutation, useQuery } from '@tanstack/react-query'
import { useContext } from 'react'
import { api, type Person, type PrRef, type Task } from '../../api/client'
import { queryKeys, useInvalidate } from '../../api/queries'
import { Avatar, PrIcon, prRefLabel, prRefRepo, Tagged } from '../../components/ui'
import { BoardAnnounce } from '../CardPromptPopover'
import { useCardActions } from './cardActions'
import { ChevronIcon, OutIcon, TickIcon } from './icons'
import { keepFromCard, stopAtCard } from './keepFromCard'
import styles from './CardSlotLists.module.css'
import shared from './TaskCard.module.css'

/**
 * Every stage on one line, where the bar was a picture of them: done, here,
 * and still to come. Each is a button while the card can be moved, so a click
 * on a stage is a move to it.
 */
export function StageList({
  task,
  members,
  onMove,
}: {
  task: Task
  members: Person[]
  /** Undefined for the overlay's copy, whose stages are only a picture. */
  onMove: ((index: number) => void) | undefined
}) {
  const index = task.sub_status_index ?? 0
  const last = task.sub_statuses.length - 1

  return (
    <div className={styles.stageList}>
      {task.sub_statuses.map((label, position) => {
        const key = `${position}-${label}`
        const className = `${styles.stage} ${stageState(position, index, last)}`
        const text = <Tagged text={label} members={members} />
        if (!onMove) {
          return (
            <span key={key} className={className}>
              {text}
            </span>
          )
        }
        return (
          <button
            key={key}
            type="button"
            className={className}
            aria-current={position === index ? 'step' : undefined}
            title={position === index ? 'You are here' : `Move to ${label}`}
            {...keepFromCard}
            onClick={(event) => {
              event.stopPropagation()
              if (position !== index) onMove(position)
            }}
          >
            {text}
          </button>
        )
      })}
    </div>
  )
}

/** A stage's class by where it stands against the card: behind it, on it, or ahead. */
function stageState(position: number, index: number, last: number): string {
  // The last stage, once reached, reads as done rather than as "here": there
  // is nowhere further for the card to go.
  if (position < index || (position === index && index === last)) return styles.stageDone ?? ''
  if (position === index) return styles.stageHere ?? ''
  return ''
}

/**
 * Finishing a card's parts from the board: a sub-task, or a tick box.
 *
 * Refused moves are said aloud rather than shown, because the list they came
 * from is a small slot on a card with no room for an error banner.
 */
function useCardParts(task: Task) {
  const invalidate = useInvalidate()
  const announce = useContext(BoardAnnounce)
  const { projectKey } = useCardActions()

  // The count on the card is the board's, so the board is out of date too.
  const refresh = () => invalidate(queryKeys.task(task.id), queryKeys.tasks(projectKey))

  const finish = useMutation({
    mutationFn: ({ part, finished }: { part: Task; finished: boolean }) =>
      api.finishTask(part.id, finished),
    onSuccess: async (part, { finished }) => {
      announce(`${part.reference} is ${finished ? 'finished' : 'open again'}.`)
      await refresh()
    },
    onError: (error) => announce(error.message),
  })
  const tick = useMutation({
    mutationFn: ({ id, done }: { id: string; done: boolean }) =>
      api.updateChecklistItem(id, { state: done ? 'done' : 'open' }),
    onSuccess: async (item) => {
      announce(`${item.title} is ${item.state === 'done' ? 'done' : 'open again'}.`)
      await refresh()
    },
    onError: (error) => announce(error.message),
  })

  return {
    finish: (part: Task, finished: boolean) => finish.mutate({ part, finished }),
    tick: (id: string, done: boolean) => tick.mutate({ id, done }),
    busy: finish.isPending || tick.isPending,
  }
}

/**
 * The card's parts, one short row each: the sub-tasks with a reference of
 * their own, then the tick boxes. Fetched when first asked for, since the
 * board itself only carries the counts — and kept, under the same key the
 * card's dialog reads, so opening the card after looking costs nothing.
 *
 * Every row is something to act on, not a picture of one. The tick finishes
 * the part, or opens it again; the rest of a sub-task's row opens that
 * sub-task. A tick box has no dialog of its own, so its whole row is the tick.
 * Every click stops at the row: the card underneath would otherwise open the
 * parent as well.
 */
export function SubtaskList({
  task,
  onOpenTask,
}: {
  task: Task
  /** Opens a sub-task's own dialog. Undefined for the overlay's copy. */
  onOpenTask: ((taskId: string) => void) | undefined
}) {
  const detail = useQuery({
    queryKey: queryKeys.task(task.id),
    queryFn: ({ signal }) => api.getTask(task.reference, signal),
  })
  const parts = useCardParts(task)

  if (!detail.data) {
    return <div className={styles.partNote}>{detail.error ? 'Could not load' : 'Loading…'}</div>
  }

  const subtasks = detail.data.subtasks.filter((subtask) => subtask.status !== 'cancelled')
  const ticks = detail.data.checklist.filter((item) => item.state !== 'cancelled')

  return (
    <ul className={styles.parts}>
      {subtasks.map((subtask) => {
        const finished = subtask.finished_at !== null
        return (
          <li key={subtask.id} className={`${styles.part} ${finished ? styles.partDone : ''}`}>
            <button
              type="button"
              className={styles.partTick}
              aria-pressed={finished}
              aria-label={
                finished ? `Open ${subtask.reference} again` : `Finish ${subtask.reference}`
              }
              title={finished ? 'Open it again' : 'Finish it'}
              aria-disabled={parts.busy}
              {...keepFromCard}
              onClick={(event) => {
                event.stopPropagation()
                if (!parts.busy) parts.finish(subtask, !finished)
              }}
            >
              <TickIcon />
            </button>
            {onOpenTask ? (
              <button
                type="button"
                className={styles.partOpen}
                title={`Open ${subtask.reference}`}
                {...keepFromCard}
                onClick={(event) => {
                  event.stopPropagation()
                  onOpenTask(subtask.id)
                }}
              >
                <PartLabel subtask={subtask} />
                <ChevronIcon />
              </button>
            ) : (
              <span className={styles.partOpen}>
                <PartLabel subtask={subtask} />
              </span>
            )}
          </li>
        )
      })}
      {ticks.map((item) => {
        const done = item.state === 'done'
        return (
          <li key={item.id} className={`${styles.part} ${done ? styles.partDone : ''}`}>
            <button
              type="button"
              className={styles.partWhole}
              aria-pressed={done}
              aria-disabled={parts.busy}
              {...keepFromCard}
              onClick={(event) => {
                event.stopPropagation()
                if (!parts.busy) parts.tick(item.id, !done)
              }}
            >
              <span className={styles.partTick} aria-hidden="true">
                <TickIcon />
              </span>
              <span className={styles.partTitle}>{item.title}</span>
            </button>
          </li>
        )
      })}
    </ul>
  )
}

/** A sub-task's reference, title and owner, as its row says them. */
function PartLabel({ subtask }: { subtask: Task }) {
  return (
    <>
      <span className={styles.partRef}>{subtask.reference}</span>
      <span className={styles.partTitle}>{subtask.title}</span>
      <Avatar name={subtask.assignee.name} colour={subtask.assignee.colour} small />
    </>
  )
}

/**
 * Each pull request on its own line: where it is, which one, how far it got.
 * The whole line is the link, so it opens wherever on it you click; one that
 * is not a URL has nowhere to go and is only the line.
 */
export function PrList({ refs }: { refs: PrRef[] }) {
  return (
    <ul className={styles.prList}>
      {refs.map((ref) => {
        const repo = prRefRepo(ref.url)
        const href = /^https?:\/\//i.test(ref.url) ? ref.url : null
        const line = (
          <>
            <PrIcon state={ref.state} size={12} />
            {repo ? <span className={styles.prRepo}>{repo}</span> : null}
            <span className={styles.prNumber}>{prRefLabel(ref.url)}</span>
            <span className={`${shared.prState} ${shared[`pr_${ref.state}`]}`}>{ref.state}</span>
          </>
        )
        return (
          <li key={ref.url}>
            {href ? (
              <a
                className={`${styles.prRow} ${styles.prRowLink}`}
                href={href}
                target="_blank"
                rel="noreferrer"
                title={`Open ${repo ? `${repo} ` : ''}${prRefLabel(ref.url)} in a new tab`}
                onClick={stopAtCard}
                {...keepFromCard}
              >
                {line}
                <OutIcon />
              </a>
            ) : (
              <span className={styles.prRow}>{line}</span>
            )}
          </li>
        )
      })}
    </ul>
  )
}
