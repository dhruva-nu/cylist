/**
 * One goal: what it is, how far along it is, and every card written under it.
 *
 * The page is deliberately not a small board. A goal's cards are listed under
 * the column each is in, in the board's own left-to-right order, because the
 * question this page answers is "what is left" rather than "what is where" —
 * and a second thing that looks like a board is a second thing to drag cards
 * around on, with two answers to where they went. Nothing here moves: the way
 * to move a card is the board, and "On the board" in the actions goes straight
 * there with this goal already in the search box.
 *
 * The cards take the page and the goal itself takes a column down the side.
 * That is the right way round: the description, the progress bar and the
 * counts are read once on arrival and then referred to, while the list is what
 * somebody came to work through — and a summary across the top pushed the
 * first card below the fold on every visit.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from '@tanstack/react-router'
import { useState } from 'react'
import { api, type BoardColumn, type Task } from '../api/client'
import { GOAL_STATUS_LABELS, GoalProgressBar, GoalTargetMark } from '../components/GoalMarks'
import { GoalDialog } from '../components/GoalDialog'
import { useProjectFiles } from '../components/projectFiles'
import { PageHead } from '../components/Shell'
import { TaskDialog } from '../components/TaskDialog'
import { Field, Modal, ModalBody } from '../components/Modal'
import {
  Avatar,
  Button,
  EmptyState,
  ErrorBanner,
  Eyebrow,
  LiveRegion,
  StatusIcon,
  Tagged,
  TypeIcon,
  useAnnouncer,
} from '../components/ui'
import styles from './GoalPage.module.css'

export function GoalPage() {
  const { projectKey, goalRef } = useParams({ from: '/p/$projectKey/goals/$goalRef' })
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { message, announce } = useAnnouncer()
  const [editing, setEditing] = useState(false)
  const [linking, setLinking] = useState(false)
  const [openTaskId, setOpenTaskId] = useState<string | null>(null)
  /** What the list is narrowed to: a word, and a column. Neither is remembered
      between visits — a filter you cannot see the state of is a page that
      looks broken when you come back to it, and both are visible here. */
  const [needle, setNeedle] = useState('')
  const [onlyColumn, setOnlyColumn] = useState<string | null>(null)

  const goal = useQuery({
    queryKey: ['goal', goalRef],
    queryFn: () => api.getGoal(goalRef),
  })
  const board = useQuery({
    queryKey: ['board', projectKey],
    queryFn: () => api.listColumns(projectKey),
  })
  /** For the `>` tags in the goal's description. */
  const files = useProjectFiles(projectKey)
  const members = useQuery({
    queryKey: ['members', projectKey],
    queryFn: () => api.listMembers(projectKey),
  })
  const templates = useQuery({
    queryKey: ['templates', projectKey],
    queryFn: () => api.listTemplates(projectKey),
  })

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['goal'] }),
      queryClient.invalidateQueries({ queryKey: ['goals', projectKey] }),
      queryClient.invalidateQueries({ queryKey: ['tasks', projectKey] }),
      queryClient.invalidateQueries({ queryKey: ['task'] }),
      queryClient.invalidateQueries({ queryKey: ['project-summary', projectKey] }),
    ])
  }

  const unlink = useMutation({
    mutationFn: (task: Task) => api.updateTask(task.reference, { goal_id: null }),
    onSuccess: async (task) => {
      await refresh()
      announce(`${task.reference} taken off this goal. It is still on the board.`)
    },
  })

  if (goal.isPending || board.isPending) return <EmptyState>Loading the goal…</EmptyState>
  if (goal.error) return <ErrorBanner>{goal.error.message}</ErrorBanner>
  if (board.error) return <ErrorBanner>{board.error.message}</ErrorBanner>

  const found = goal.data
  const columns = board.data.columns
  const { total, done, open, blocked, on_hold: onHold, cancelled } = found.progress

  const word = needle.trim().toLowerCase()
  const matches = (task: Task) =>
    !word || `${task.reference} ${task.title}`.toLowerCase().includes(word)
  // The word narrows what the column chips count, and the column narrows what
  // the list shows. In that order: a chip that says 3 and then shows nothing
  // because a word was typed is a chip that lies.
  const wordMatched = found.tasks.filter(matches)
  const shown = wordMatched.filter((task) => onlyColumn === null || task.column_id === onlyColumn)
  const byColumn = columns
    .map((column) => ({
      column,
      tasks: shown.filter((task) => task.column_id === column.id),
    }))
    .filter((group) => group.tasks.length > 0)

  return (
    <>
      <PageHead
        eyebrow={
          <Eyebrow>
            <span className={styles.rail} style={{ background: found.colour }} aria-hidden="true" />
            {found.reference}
            {found.status === 'open' ? null : (
              <span className={`${styles.pill} ${styles[found.status]}`}>
                {GOAL_STATUS_LABELS[found.status]}
              </span>
            )}
          </Eyebrow>
        }
        title={found.name}
        actions={
          <>
            {/* Straight to the board with the goal already in the search box,
                which is the one thing this page cannot do: move cards. */}
            <Link
              to="/p/$projectKey/board"
              params={{ projectKey }}
              search={{ q: `goal:"${found.name}"` }}
              className={styles.boardLink}
            >
              On the board
            </Link>
            <Button onClick={() => setLinking(true)}>+ Link cards</Button>
            <Button variant="go" onClick={() => setEditing(true)}>
              Edit
            </Button>
          </>
        }
      />

      <LiveRegion message={message} />
      {unlink.error ? <ErrorBanner>{unlink.error.message}</ErrorBanner> : null}

      {/* The goal down one side, its cards down the other. The aside comes
          first in the source and is placed on the right by the grid, so that
          on a narrow window — where there is only one column — what the goal
          is still arrives before the list of what is on it. */}
      <div className={styles.detail}>
        <aside className={styles.side} aria-label="About this goal">
          <section className={styles.summary}>
            <GoalProgressBar goal={found} large />
            <div className={styles.stats}>
              <Stat label="Cards" value={total} />
              <Stat label="Done" value={done} />
              <Stat label="Left" value={open} />
              {blocked ? <Stat label="Blocked" value={blocked} tone="blocked" /> : null}
              {onHold ? <Stat label="On hold" value={onHold} tone="hold" /> : null}
              {cancelled ? <Stat label="Cancelled" value={cancelled} /> : null}
            </div>
            <div className={styles.facts}>
              <div className={styles.owner}>
                <Avatar name={found.owner.name} colour={found.owner.colour} />
                <div>
                  <span className={styles.statLabel}>Owner</span>
                  <b>{found.owner.name}</b>
                </div>
              </div>
              <GoalTargetMark goal={found} />
            </div>
          </section>

          <section className={styles.about}>
            <h2>What it is</h2>
            <p>
              {found.description ? (
                <Tagged
                  text={found.description}
                  members={members.data?.members ?? []}
                  files={files}
                />
              ) : (
                'No description yet.'
              )}
            </p>
          </section>
        </aside>

        <div className={styles.list}>
          {found.tasks.length === 0 ? (
            <EmptyState>
              No cards on this goal yet. Link the ones already on the board, or pick this goal when
              you write a new card.
            </EmptyState>
          ) : (
            <Filters
              columns={columns}
              tasks={wordMatched}
              needle={needle}
              onNeedle={setNeedle}
              onlyColumn={onlyColumn}
              onColumn={setOnlyColumn}
              total={found.tasks.length}
              showing={shown.length}
            />
          )}

          {found.tasks.length > 0 && shown.length === 0 ? (
            <EmptyState>
              No card on this goal matches that.{' '}
              <button
                type="button"
                className={styles.clear}
                onClick={() => {
                  setNeedle('')
                  setOnlyColumn(null)
                }}
              >
                Clear the filters
              </button>
            </EmptyState>
          ) : null}

          {byColumn.map(({ column, tasks }) => (
            <section key={column.id} className={styles.group}>
              <h2>
                {column.name}
                <span className={styles.count}>{tasks.length}</span>
              </h2>
              <ul className={styles.cards}>
                {tasks.map((task) => (
                  <li key={task.id} className={styles.card}>
                    <button
                      type="button"
                      className={styles.cardOpen}
                      onClick={() => setOpenTaskId(task.id)}
                    >
                      <span className={styles.cardMark} title={task.status}>
                        {task.status === 'active' ? (
                          <TypeIcon type={task.type} size={15} />
                        ) : (
                          <StatusIcon status={task.status} size={15} />
                        )}
                      </span>
                      <span className={styles.cardRef}>{task.reference}</span>
                      <span
                        className={`${styles.cardTitle} ${task.status === 'cancelled' ? styles.struck : ''}`}
                      >
                        {task.title}
                      </span>
                      <Avatar name={task.assignee.name} colour={task.assignee.colour} />
                    </button>
                    <Button
                      variant="ghost"
                      small
                      disabled={unlink.isPending}
                      aria-label={`Take ${task.reference} off this goal`}
                      onClick={() => unlink.mutate(task)}
                    >
                      ×
                    </Button>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      </div>

      {editing ? (
        <GoalDialog
          projectKey={projectKey}
          goal={found}
          members={members.data?.members ?? []}
          announce={announce}
          onDone={refresh}
          onClose={() => setEditing(false)}
          onDeleted={() => {
            // Deliberately not awaited: the dialog has already closed, and the
            // navigation is the last thing this page does before it unmounts.
            void navigate({ to: '/p/$projectKey/goals', params: { projectKey } })
          }}
        />
      ) : null}

      {linking ? (
        <LinkCardsDialog
          projectKey={projectKey}
          goalId={found.id}
          goalName={found.name}
          columns={columns}
          announce={announce}
          onDone={refresh}
          onClose={() => setLinking(false)}
        />
      ) : null}

      {openTaskId && columns[0] ? (
        <TaskDialog
          projectKey={projectKey}
          taskId={openTaskId}
          columns={columns}
          firstColumn={columns[0]}
          templates={templates.data ?? []}
          goals={[found]}
          announce={announce}
          onOpenTask={setOpenTaskId}
          onDone={refresh}
          onClose={() => setOpenTaskId(null)}
        />
      ) : null}
    </>
  )
}

/**
 * What the list is narrowed to: a word, and a column.
 *
 * Two filters rather than the board's one search box. The board's box has to
 * parse `col:` and `who:` because it is filtering a grid you cannot see the
 * whole of; a goal's cards are a list of a dozen under three headings, and a
 * row of column chips answers "just show me what is in review" in one click
 * with the count already on it.
 *
 * The chips are toggles rather than a radio group on purpose: "All" is one of
 * them, and pressing the chip you are already on turns it off — which is the
 * gesture people try first and the one a radio group refuses.
 */
function Filters({
  columns,
  tasks,
  needle,
  onNeedle,
  onlyColumn,
  onColumn,
  total,
  showing,
}: {
  columns: BoardColumn[]
  /** The cards the word matched — what the column chips count. */
  tasks: Task[]
  needle: string
  onNeedle: (needle: string) => void
  /** The column the list is narrowed to, or null for all of them. */
  onlyColumn: string | null
  onColumn: (columnId: string | null) => void
  total: number
  showing: number
}) {
  const counted = (columnId: string) => tasks.filter((task) => task.column_id === columnId).length

  return (
    <div className={styles.filters}>
      <div className={styles.search}>
        <input
          type="search"
          value={needle}
          onChange={(event) => onNeedle(event.target.value)}
          placeholder="Find a card — ATL-41, or part of a title"
          aria-label="Find a card on this goal"
        />
        <span className={styles.showing} role="status">
          {showing === total ? `${total} cards` : `${showing} of ${total}`}
        </span>
      </div>

      <div className={styles.chips}>
        <button
          type="button"
          aria-pressed={onlyColumn === null}
          className={onlyColumn === null ? styles.chipOn : undefined}
          onClick={() => onColumn(null)}
        >
          All columns
        </button>
        {columns.map((column) => {
          const here = counted(column.id)
          const on = onlyColumn === column.id
          return (
            <button
              key={column.id}
              type="button"
              aria-pressed={on}
              className={[on ? styles.chipOn : '', here === 0 ? styles.chipEmpty : '']
                .filter(Boolean)
                .join(' ')}
              // Pressing the chip you are on takes the filter off again.
              onClick={() => onColumn(on ? null : column.id)}
            >
              {column.name}
              <span className={styles.chipCount}>{here}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: 'blocked' | 'hold' }) {
  return (
    <div className={`${styles.stat} ${tone ? styles[tone] : ''}`}>
      <span className={styles.statLabel}>{label}</span>
      <b>{value}</b>
    </div>
  )
}

/**
 * Puts cards that are already on the board onto this goal.
 *
 * Only cards on no goal at all are offered. Moving a card between goals is a
 * thing you do on the card, where you can see what you are taking it off; a
 * list that silently stole cards from another epic would be a list nobody
 * could use quickly, which is the only reason to have it.
 */
function LinkCardsDialog({
  projectKey,
  goalId,
  goalName,
  columns,
  announce,
  onDone,
  onClose,
}: {
  projectKey: string
  goalId: string
  goalName: string
  columns: BoardColumn[]
  announce: (message: string) => void
  onDone: () => Promise<void>
  onClose: () => void
}) {
  const [filter, setFilter] = useState('')
  const tasks = useQuery({
    queryKey: ['tasks', projectKey],
    queryFn: () => api.listTasks(projectKey),
  })

  const link = useMutation({
    mutationFn: (task: Task) => api.updateTask(task.reference, { goal_id: goalId }),
    onSuccess: async (task) => {
      await onDone()
      announce(`${task.reference} put on ${goalName}.`)
    },
  })

  const needle = filter.trim().toLowerCase()
  const named = (id: string | null) => columns.find((column) => column.id === id)?.name ?? ''
  const offered = (tasks.data ?? [])
    .filter((task) => task.goal_id === null)
    .filter((task) => !needle || `${task.reference} ${task.title}`.toLowerCase().includes(needle))

  return (
    <Modal
      title={`Link cards to ${goalName}`}
      onClose={onClose}
      footer={<Button onClick={onClose}>Done</Button>}
    >
      <ModalBody>
        {link.error ? <ErrorBanner>{link.error.message}</ErrorBanner> : null}
        <Field label="Find a card" hint="Cards already on another goal are not offered here.">
          <input
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="ATL-41, or part of a title"
          />
        </Field>

        {tasks.isPending ? <EmptyState>Loading the board…</EmptyState> : null}
        {tasks.data && offered.length === 0 ? (
          <EmptyState>
            {needle ? 'No card matches that.' : 'Every card on the board is already on a goal.'}
          </EmptyState>
        ) : null}

        <ul className={styles.offered}>
          {offered.map((task) => (
            <li key={task.id}>
              <button
                type="button"
                disabled={link.isPending}
                onClick={() => link.mutate(task)}
                className={styles.offer}
              >
                <span className={styles.cardRef}>{task.reference}</span>
                <span className={styles.cardTitle}>{task.title}</span>
                <span className={styles.offerColumn}>{named(task.column_id)}</span>
                <span aria-hidden="true">+</span>
              </button>
            </li>
          ))}
        </ul>
      </ModalBody>
    </Modal>
  )
}
