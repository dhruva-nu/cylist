import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import {
  api,
  errorMessage,
  type DayPick,
  type FiledItem,
  type Person,
  type Todo,
  type TodayPlan,
} from '../../api/client'
import { queryKeys, usePeople } from '../../api/queries'
import { dayLong, dayShort, localToday, weekdayShort } from '../dates'
import { MentionBox } from '../Mentions'
import { useProjectFiles } from '../projectFiles'
import { Button, EmptyState, ErrorNotice, Tagged } from '../ui'
import styles from '../Today.module.css'
import { handlesOwnErrors } from '../../api/mutationErrors'
import { useRefreshPlans } from './hooks'

/** The right pane: the cards on the day, each with its to-dos. */
export function PlanPane({
  projectKey,
  day,
  plan,
  justPicked,
  onOpenCard,
}: {
  projectKey: string
  day: string
  plan: TodayPlan | undefined
  justPicked: string | null
  onOpenCard: (reference: string) => void
}) {
  const people = (usePeople().data ?? []).filter((person) => person.archived_at === null)
  const files = useProjectFiles(projectKey)
  const total = plan?.picks.reduce((sum, pick) => sum + pick.todos.length, 0) ?? 0

  return (
    <section className={styles.planPane} aria-label="Plan for the day">
      <div className={styles.paneHead}>
        <h3>{day === localToday() ? 'Plan for today' : `Plan for ${dayLong(day)}`}</h3>
        {plan?.picks.length ? (
          <span className={styles.paneNote}>
            {plan.picks.length} {plan.picks.length === 1 ? 'card' : 'cards'}, {total}{' '}
            {total === 1 ? 'to-do' : 'to-dos'}
          </span>
        ) : null}
      </div>
      <div className={styles.planList}>
        {plan === undefined ? null : plan.picks.length === 0 ? (
          <EmptyState>
            Pick a card from the open cards to plan the day around it. Its to-dos go under it here:
            type @ to tag a person, &gt; to tag a file.
          </EmptyState>
        ) : (
          plan.picks.map((pick) => (
            <PickBlock
              key={pick.id}
              projectKey={projectKey}
              pick={pick}
              fresh={pick.id === justPicked}
              people={people}
              files={files}
              onOpenCard={onOpenCard}
            />
          ))
        )}
      </div>
    </section>
  )
}

/** One card on the day: its name, its to-dos, and the box to add another. */
function PickBlock({
  projectKey,
  pick,
  fresh,
  people,
  files,
  onOpenCard,
}: {
  projectKey: string
  pick: DayPick
  /** Just picked: scrolled into view, since a new card lands at the bottom. */
  fresh: boolean
  people: Person[]
  files: readonly FiledItem[]
  onOpenCard: (reference: string) => void
}) {
  const refresh = useRefreshPlans(projectKey)
  const [draft, setDraft] = useState('')
  const block = useRef<HTMLElement>(null)

  useEffect(() => {
    if (fresh) block.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [fresh, pick.todos.length])

  const add = useMutation({
    meta: handlesOwnErrors,
    mutationFn: (text: string) => api.addTodo(pick.id, text),
    onSuccess: () => setDraft(''),
    onSettled: refresh,
  })
  const unpick = useMutation({
    meta: handlesOwnErrors,
    mutationFn: () => api.unpickCard(pick.id),
    onSettled: refresh,
  })

  function submit() {
    const text = draft.trim()
    if (text && !add.isPending) add.mutate(text)
  }

  function remove() {
    if (
      pick.todos.length &&
      !window.confirm(`Take ${pick.task.reference} off the day? Its to-dos for the day go with it.`)
    ) {
      return
    }
    unpick.mutate()
  }

  return (
    <section ref={block} className={styles.pickBlock} aria-label={pick.task.reference}>
      <div className={styles.pickHead}>
        {pick.task.column !== null ? (
          <button
            type="button"
            className={styles.cardOpen}
            title={`Open ${pick.task.reference}`}
            onClick={() => onOpenCard(pick.task.reference)}
          >
            <span className={styles.reference}>{pick.task.reference}</span>
            <span className={styles.blockTitle}>{pick.task.title}</span>
          </button>
        ) : (
          <span className={styles.cardOpen}>
            <span className={styles.reference}>{pick.task.reference}</span>
            <span className={styles.blockTitle}>{pick.task.title}</span>
          </span>
        )}
        <span className={styles.paneNote}>{pick.task.column ?? 'Cold storage'}</span>
        <Button
          small
          variant="ghost"
          aria-label={`Take ${pick.task.reference} off the day`}
          title="Take off the day"
          onClick={remove}
        >
          ✕
        </Button>
      </div>
      <ul className={styles.todos}>
        {pick.todos.map((todo) => (
          <TodoRow
            key={todo.id}
            projectKey={projectKey}
            todo={todo}
            people={people}
            files={files}
          />
        ))}
      </ul>
      <div className={styles.compose}>
        <MentionBox
          className={styles.composeBox}
          value={draft}
          onChange={setDraft}
          members={people}
          files={files}
          maxLength={500}
          placeholder="Add a to-do — @ for a person, > for a file"
          aria-label={`Add a to-do to ${pick.task.reference}`}
          onEnter={submit}
        />
        {draft.trim() ? (
          <Button small onClick={submit} disabled={add.isPending}>
            Add
          </Button>
        ) : null}
      </div>
      <ErrorNotice error={add.error ?? unpick.error} />
    </section>
  )
}

/** One to-do: the tick, the words with their tags, and what became of it. */
function TodoRow({
  projectKey,
  todo,
  people,
  files,
}: {
  projectKey: string
  todo: Todo
  people: Person[]
  files: readonly FiledItem[]
}) {
  const refresh = useRefreshPlans(projectKey)
  const queryClient = useQueryClient()

  const tick = useMutation({
    meta: handlesOwnErrors,
    mutationFn: (done: boolean) => api.updateTodo(todo.id, { state: done ? 'done' : 'open' }),
    // The box answers the click at once rather than after a round trip: a tick
    // that lags reads as a click that missed.
    onMutate: (done) => {
      queryClient.setQueriesData<TodayPlan>(
        { queryKey: queryKeys.todayPlans(projectKey) },
        (plan) => (plan ? withTodoState(plan, todo.id, done ? 'done' : 'open') : plan),
      )
    },
    onSettled: refresh,
  })
  const remove = useMutation({
    mutationFn: () => api.deleteTodo(todo.id),
    onSettled: refresh,
  })

  const moved = todo.state === 'moved'
  const done = todo.state === 'done'

  return (
    <li
      className={[styles.todo, done && styles.done, moved && styles.moved]
        .filter(Boolean)
        .join(' ')}
    >
      <input
        type="checkbox"
        checked={done}
        disabled={moved}
        aria-label={done ? 'Mark not done' : 'Mark done'}
        title={todo.source_kind ? ON_THE_CARD[todo.source_kind] : undefined}
        onChange={(event) => tick.mutate(event.currentTarget.checked)}
      />
      <span className={styles.todoText}>
        <Tagged text={todo.text} members={people} files={files} />
      </span>
      {todo.carried_from ? (
        <span className={styles.todoMark} title={`Moved here from ${dayLong(todo.carried_from)}`}>
          from {weekdayShort(todo.carried_from)}
        </span>
      ) : null}
      {moved && todo.moved_to ? (
        <span className={styles.todoMark}>→ {dayShort(todo.moved_to)}</span>
      ) : null}
      {todo.state === 'not_done' ? <span className={styles.todoMark}>not done</span> : null}
      {/* A tick on the card can be refused — a role kept out of staging a
          column, say — and the box springs back; this says why. */}
      {tick.error ? <span className={styles.todoError}>{errorMessage(tick.error)}</span> : null}
      {moved ? null : (
        <button
          type="button"
          className={styles.todoDelete}
          aria-label="Delete this to-do"
          title="Delete"
          onClick={() => remove.mutate()}
        >
          ✕
        </button>
      )}
    </li>
  )
}

/** What ticking a to-do from the card does to the card, for its tooltip. */
const ON_THE_CARD = {
  stage: 'Ticking this moves the card on to its next stage',
  subtask: 'Ticking this finishes the sub-task on the card',
  checklist: 'Ticking this ticks the box on the card',
} as const

function withTodoState(plan: TodayPlan, todoId: string, state: 'open' | 'done'): TodayPlan {
  return {
    ...plan,
    picks: plan.picks.map((pick) => ({
      ...pick,
      todos: pick.todos.map((todo) => (todo.id === todoId ? { ...todo, state } : todo)),
    })),
  }
}
