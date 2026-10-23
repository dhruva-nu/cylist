/**
 * Today: a button in the sidebar, and the plan for the day it opens.
 *
 * It used to be a list of what was due. It is now a to-do list a person
 * writes: on the left every open card on the project, on the right the cards
 * picked up for the day with the to-dos under each — send an email, talk to
 * somebody, read a file. The to-dos are written with the same `@person` and
 * `>file` tags a comment is, so who and what a line means reads back the same
 * everywhere. At the end of the day, whatever is left either moves on to
 * another day or stays behind as not done — see `WrapUpDialog`.
 *
 * The plan is the signed-in person's own and the server keeps it that way;
 * nothing here needs to filter anybody else's out.
 *
 * Which cards are offered, and in what order, is `today.ts`. The due-or-late
 * group at the top of the left pane is what the old Today was, so nothing it
 * used to tell you has gone.
 *
 * The button carries the count of to-dos left today, red while a card of
 * yours that is late has not been put on the day — the old warning, kept.
 *
 * Scoped to the project you are in, as before: a card is picked from this
 * board, and the board's own definition of finished is its last column.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMatchRoute } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'
import {
  api,
  errorMessage,
  type DayPick,
  type FiledItem,
  type Person,
  type Task,
  type Todo,
  type TodayPlan,
} from '../api/client'
import { queryKeys, useBoard, useIdentity, usePeople, useTasks } from '../api/queries'
import { readStored, writeStored } from '../storage'
import {
  dayLong,
  dayShort,
  formatDue,
  formatDueLong,
  localDate,
  localToday,
  localZone,
  nextWorkingDay,
  shiftDay,
  weekdayShort,
} from './dates'
import { MentionBox } from './Mentions'
import { Modal } from './Modal'
import { OpenCard } from './OpenCard'
import { useProjectFiles } from './projectFiles'
import { SegmentedChoice } from './SegmentedChoice'
import { SidebarAction } from './SidebarSection'
import { lateAndUnplanned, openCardGroups } from './today'
import { Button, EmptyState, ErrorNotice, Tagged } from './ui'
import styles from './Today.module.css'
import { handlesOwnErrors } from '../api/mutationErrors'

/** Whether the open-cards pane was last left folded, per browser. */
const CARDS_FOLDED_KEY = 'cylist.today.cardsFolded'

/** The project the page is on, or null off one. */
function useProjectKey(): string | null {
  const matchRoute = useMatchRoute()
  const match = matchRoute({ to: '/p/$projectKey', fuzzy: true })
  return match ? match.projectKey : null
}

function usePlan(projectKey: string | null, day: string) {
  return useQuery({
    queryKey: queryKeys.todayPlan(projectKey ?? '', day),
    queryFn: ({ signal }) => api.getToday(projectKey ?? '', day, localZone(), signal),
    enabled: projectKey !== null,
    // Off by one person's session — the bootstrap login has no plan, and
    // asking again will not give it one.
    retry: false,
    // A plan from today on follows its cards, which change behind it — on
    // the board, on an open card, by an agent — so opening it always reads it
    // again rather than showing the copy from half a minute ago.
    refetchOnMount: 'always',
  })
}

/** The board and its cards, which the left pane and the count are read from. */
function useBoardCards(projectKey: string | null) {
  const enabled = projectKey !== null
  // The board's own readers, so a column read further down on the board is
  // read as far here, and the two never hold different shapes.
  const tasks = useTasks(projectKey ?? '', { enabled })
  const board = useBoard(projectKey ?? '', { enabled })
  return { tasks: tasks.data?.tasks, columns: board.data?.columns, readAt: tasks.dataUpdatedAt }
}

/**
 * After any change to a plan: that plan, and the reports it shows up in.
 *
 * Every day of the project rather than only the one on screen, because a
 * wrap-up writes to two days at once and the count on the sidebar is today's.
 */
function useRefreshPlans(projectKey: string) {
  const queryClient = useQueryClient()
  return () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.todayPlans(projectKey) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.dayReports(projectKey) }),
      // A to-do from the card ticks the card too, so the board and any open
      // card are read again.
      queryClient.invalidateQueries({ queryKey: queryKeys.tasks(projectKey) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.allTasks }),
      queryClient.invalidateQueries({ queryKey: queryKeys.allTaskHistory }),
    ])
}

export function Today() {
  const projectKey = useProjectKey()

  const [showingPlan, setShowingPlan] = useState(false)
  /** The day the planner is on. Lives here so a wrap-up can move it on. */
  const [day, setDay] = useState(localToday)
  /** The day being wrapped up, while its dialog is open. */
  const [wrapping, setWrapping] = useState<string | null>(null)
  /** The card opened over the planner, by reference. */
  const [openCardRef, setOpenCardRef] = useState<string | null>(null)

  return (
    <SidebarAction
      name="Today"
      note={<TodoCount projectKey={projectKey} />}
      disabled={projectKey === null}
      title={projectKey === null ? 'Open a project to plan your day on it' : undefined}
      onOpen={() => {
        setDay(localToday())
        setShowingPlan(true)
      }}
    >
      {showingPlan && projectKey !== null ? (
        <TodayDialog
          projectKey={projectKey}
          day={day}
          onDay={setDay}
          onOpenCard={setOpenCardRef}
          onWrapUp={() => setWrapping(day)}
          onClose={() => setShowingPlan(false)}
        />
      ) : null}

      {/* Siblings of the planner rather than children of it, so each comes out
          on top by being later and closing it leaves the planner as it was. */}
      {wrapping !== null && projectKey !== null ? (
        <WrapUpDialog
          projectKey={projectKey}
          day={wrapping}
          onGoTo={(toDay) => {
            setWrapping(null)
            setDay(toDay)
          }}
          onClose={() => setWrapping(null)}
        />
      ) : null}

      {openCardRef !== null && projectKey !== null ? (
        <OpenCard
          projectKey={projectKey}
          reference={openCardRef}
          onOpen={setOpenCardRef}
          onClose={() => setOpenCardRef(null)}
        />
      ) : null}
    </SidebarAction>
  )
}

/**
 * The same count, for a sidebar that has been collapsed to its rail.
 *
 * Exported for exactly one caller — see the note in `Sidebar.tsx` about a
 * count nobody can see.
 */
export function TodayCount() {
  return <TodoCount projectKey={useProjectKey()} />
}

/**
 * The to-dos left today, on the button — red while something of yours that
 * is late has not been put on the day.
 */
function TodoCount({ projectKey }: { projectKey: string | null }) {
  const today = localToday()
  const plan = usePlan(projectKey, today)
  const { tasks, columns } = useBoardCards(projectKey)
  const identity = useIdentity()

  const planned = new Set(plan.data?.picks.map((pick) => pick.task.id) ?? [])
  const late =
    tasks && columns
      ? lateAndUnplanned(tasks, columns, identity.data?.person ?? null, today, planned)
      : false
  const left = plan.data?.open_count ?? 0
  if (left === 0 && !late) return null

  return (
    <span className={late ? styles.countLate : styles.count}>
      {left}
      <span className="visually-hidden">
        {left === 1 ? ' to-do left today' : ' to-dos left today'}
        {late ? ', and a late card is not on your day' : ''}
      </span>
    </span>
  )
}

function TodayDialog({
  projectKey,
  day,
  onDay,
  onOpenCard,
  onWrapUp,
  onClose,
}: {
  projectKey: string
  day: string
  onDay: (day: string) => void
  onOpenCard: (reference: string) => void
  onWrapUp: () => void
  onClose: () => void
}) {
  const plan = usePlan(projectKey, day)
  const queryClient = useQueryClient()
  const { readAt } = useBoardCards(projectKey)
  // Whenever the board's cards are read again — something changed on one,
  // here or anywhere else — so is the plan, which the server keeps in step
  // with them.
  useEffect(() => {
    if (readAt) void queryClient.invalidateQueries({ queryKey: queryKeys.todayPlans(projectKey) })
  }, [readAt, projectKey, queryClient])
  const [cardsFolded, setCardsFolded] = useState(() => readStored(CARDS_FOLDED_KEY) === 'true')
  /** The pick just made from the left pane, so the plan can bring it into view. */
  const [justPicked, setJustPicked] = useState<string | null>(null)

  function foldCards(folded: boolean) {
    setCardsFolded(folded)
    writeStored(CARDS_FOLDED_KEY, String(folded))
  }

  const loaded = plan.data
  const total = loaded ? loaded.picks.reduce((sum, pick) => sum + pick.todos.length, 0) : 0

  return (
    <Modal
      title="Today"
      widest
      onClose={onClose}
      headerActions={<DayNav day={day} onDay={onDay} />}
      footer={
        <>
          {loaded && total ? (
            <span className={styles.progress}>
              <span>
                <b>
                  {loaded.done_count} of {total}
                </b>{' '}
                done
              </span>
              <span className={styles.meter} aria-hidden="true">
                <i style={{ width: `${Math.round((loaded.done_count / total) * 100)}%` }} />
              </span>
            </span>
          ) : null}
          <Button
            variant="go"
            disabled={!loaded || loaded.open_count === 0}
            title={loaded?.open_count ? undefined : 'Nothing open to move on'}
            onClick={onWrapUp}
          >
            Wrap up the day
          </Button>
          <Button onClick={onClose}>Close</Button>
        </>
      }
    >
      <ErrorNotice error={plan.error} />
      <div className={cardsFolded ? `${styles.planner} ${styles.folded}` : styles.planner}>
        {cardsFolded ? (
          <div className={styles.cardsRail}>
            <Button
              small
              variant="ghost"
              aria-label="Show open cards"
              title="Show open cards"
              onClick={() => foldCards(false)}
            >
              ›
            </Button>
            <button type="button" className={styles.railLabel} onClick={() => foldCards(false)}>
              Add cards
            </button>
          </div>
        ) : (
          <CardsPane
            projectKey={projectKey}
            day={day}
            plan={loaded}
            onFold={() => foldCards(true)}
            onPicked={setJustPicked}
          />
        )}
        <PlanPane
          projectKey={projectKey}
          day={day}
          plan={loaded}
          justPicked={justPicked}
          onOpenCard={onOpenCard}
        />
      </div>
    </Modal>
  )
}

/** Which day is on screen: back, forward, a date, and back to today. */
function DayNav({ day, onDay }: { day: string; onDay: (day: string) => void }) {
  const today = localToday()
  return (
    <div className={styles.dayNav}>
      <Button
        small
        variant="ghost"
        aria-label="The day before"
        onClick={() => onDay(shiftDay(day, -1))}
      >
        ‹
      </Button>
      <label className={styles.dayField}>
        <span className="visually-hidden">Which day to plan</span>
        <input
          type="date"
          value={day}
          onChange={(event) => onDay(event.currentTarget.value || today)}
        />
      </label>
      <Button
        small
        variant="ghost"
        aria-label="The day after"
        onClick={() => onDay(shiftDay(day, 1))}
      >
        ›
      </Button>
      <Button small variant="ghost" disabled={day === today} onClick={() => onDay(today)}>
        Today
      </Button>
    </div>
  )
}

/** The left pane: every open card on the board, to pick the day's from. */
function CardsPane({
  projectKey,
  day,
  plan,
  onFold,
  onPicked,
}: {
  projectKey: string
  day: string
  plan: TodayPlan | undefined
  onFold: () => void
  /** Told the new pick's id, so the plan can scroll to what it brought in. */
  onPicked: (pickId: string) => void
}) {
  const { tasks, columns } = useBoardCards(projectKey)
  const identity = useIdentity()
  const me = identity.data?.person ?? null
  const refresh = useRefreshPlans(projectKey)

  const [whose, setWhose] = useState<'all' | 'mine'>('all')
  const [query, setQuery] = useState('')

  const pickedByTask = new Map(plan?.picks.map((pick) => [pick.task.id, pick]) ?? [])

  const pick = useMutation({
    meta: handlesOwnErrors,
    mutationFn: (task: Task) => api.pickCard(projectKey, task.reference, day),
    onSuccess: (picked) => onPicked(picked.id),
    onSettled: refresh,
  })
  const unpick = useMutation({
    meta: handlesOwnErrors,
    mutationFn: (pickId: string) => api.unpickCard(pickId),
    onSettled: refresh,
  })

  function toggle(task: Task) {
    const picked = pickedByTask.get(task.id)
    if (!picked) {
      pick.mutate(task)
      return
    }
    if (
      picked.todos.length &&
      !window.confirm(`Take ${task.reference} off the day? Its to-dos for the day go with it.`)
    ) {
      return
    }
    unpick.mutate(picked.id)
  }

  const today = localToday()
  const options = { me, today, query }
  const groups =
    tasks && columns ? openCardGroups(tasks, columns, { ...options, mine: whose === 'mine' }) : null
  const count = (mine: boolean) =>
    tasks && columns
      ? openCardGroups(tasks, columns, { ...options, query: '', mine }).reduce(
          (sum, group) => sum + group.tasks.length,
          0,
        )
      : 0

  return (
    <section className={styles.cardsPane} aria-label="Open cards">
      <div className={styles.paneHead}>
        <h3>Open cards</h3>
        <span className={styles.paneNote}>not in the last column</span>
        <Button
          small
          variant="ghost"
          aria-label="Hide open cards"
          title="Hide open cards"
          onClick={onFold}
        >
          ‹
        </Button>
      </div>
      <div className={styles.cardsTools}>
        <label className={styles.find}>
          <span className="visually-hidden">Find a card</span>
          <input
            type="search"
            placeholder="Find a card"
            value={query}
            onChange={(event) => setQuery(event.currentTarget.value)}
          />
        </label>
        <SegmentedChoice
          label="Whose cards"
          value={whose}
          onChange={setWhose}
          options={[
            { value: 'all', label: `All ${count(false)}` },
            { value: 'mine', label: `Mine ${count(true)}` },
          ]}
        />
      </div>
      <ErrorNotice error={pick.error ?? unpick.error} />
      <div className={styles.cardList}>
        {groups === null ? <p className={styles.aside}>Reading the board…</p> : null}
        {groups?.length === 0 ? (
          <p className={styles.aside}>{query ? 'No open card matches that.' : 'No open cards.'}</p>
        ) : null}
        {groups?.map((group) => (
          <div key={group.name} className={styles.group}>
            <h4 className={group.late ? styles.groupNameLate : styles.groupName}>
              <span>{group.name}</span>
              <span>{group.tasks.length}</span>
            </h4>
            <ul className={styles.rows}>
              {group.tasks.map((task) => (
                <li key={task.id}>
                  <CardRow
                    task={task}
                    columnName={columns?.find((column) => column.id === task.column_id)?.name ?? ''}
                    picked={pickedByTask.has(task.id)}
                    today={today}
                    onToggle={() => toggle(task)}
                  />
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  )
}

/** One card to pick: a press puts it on the day, another takes it off. */
function CardRow({
  task,
  columnName,
  picked,
  today,
  onToggle,
}: {
  task: Task
  columnName: string
  picked: boolean
  today: string
  onToggle: () => void
}) {
  const due = task.next_due_date
  let dueLabel: string | null = null
  let dueClass = ''
  if (due !== null && due < today) {
    dueLabel = `${formatDue(due)}, late`
    dueClass = styles.late ?? ''
  } else if (due === today) {
    dueLabel = 'Today'
    dueClass = styles.soon ?? ''
  } else if (due !== null) {
    dueLabel = formatDue(due)
  }

  return (
    <button
      type="button"
      className={picked ? `${styles.pick} ${styles.picked}` : styles.pick}
      aria-pressed={picked}
      title={picked ? `Take ${task.reference} off the day` : `Put ${task.reference} on the day`}
      onClick={onToggle}
    >
      <span className={styles.pickText}>
        <span className={styles.head}>
          <span className={styles.reference}>{task.reference}</span>
          <span className={styles.pickTitle}>{task.title}</span>
        </span>
        <span className={styles.pickMeta}>
          <span>{columnName}</span>
          {dueLabel ? (
            <>
              <span aria-hidden="true">·</span>
              <span className={dueClass} title={due ? formatDueLong(due) : undefined}>
                {dueLabel}
              </span>
            </>
          ) : null}
          <span aria-hidden="true">·</span>
          <span>{task.assignee.name}</span>
        </span>
      </span>
      <span className={styles.pickMark} aria-hidden="true">
        {picked ? '✓' : '+'}
      </span>
    </button>
  )
}

/** The right pane: the cards on the day, each with its to-dos. */
function PlanPane({
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

/**
 * Closing a day: what is still open, ticked to move on unless untold, and the
 * day it goes to — the next working day unless somebody picks another.
 *
 * Everything left unticked stays on the day as not done. Nothing is lost
 * either way: a moved to-do stays on its day as moved, and a not-done one can
 * be reopened.
 */
function WrapUpDialog({
  projectKey,
  day,
  onGoTo,
  onClose,
}: {
  projectKey: string
  day: string
  onGoTo: (day: string) => void
  onClose: () => void
}) {
  const plan = usePlan(projectKey, day)
  const refresh = useRefreshPlans(projectKey)
  const people = (usePeople().data ?? []).filter((person) => person.archived_at === null)
  const files = useProjectFiles(projectKey)

  const working = nextWorkingDay(day)
  const tomorrow = shiftDay(day, 1)
  const [target, setTarget] = useState<'next' | 'tomorrow' | 'other'>('next')
  const [otherDay, setOtherDay] = useState(() => shiftDay(working, 1))
  /** Ticked by default, so what is tracked is what was unticked. */
  const [kept, setKept] = useState<ReadonlySet<string>>(new Set())

  const toDay = target === 'next' ? working : target === 'tomorrow' ? tomorrow : otherDay

  const open = (plan.data?.picks ?? [])
    .map((pick) => ({ pick, todos: pick.todos.filter((todo) => todo.state === 'open') }))
    .filter((entry) => entry.todos.length > 0)
  const openIds = open.flatMap((entry) => entry.todos.map((todo) => todo.id))
  const moving = openIds.filter((id) => !kept.has(id))
  const staying = openIds.length - moving.length

  const wrap = useMutation({
    meta: handlesOwnErrors,
    mutationFn: () => api.wrapUpDay(projectKey, day, toDay, moving, localZone()),
    onSettled: refresh,
  })

  function flip(id: string) {
    const next = new Set(kept)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    setKept(next)
  }

  const dayName = weekdayName(day)
  const toName = weekdayName(toDay)
  const plural = (count: number) => (count === 1 ? 'to-do' : 'to-dos')

  if (wrap.data) {
    const result = wrap.data
    return (
      <Modal
        title={`${dayName} is wrapped up`}
        onClose={onClose}
        footer={
          <>
            {result.moved ? (
              <Button onClick={() => onGoTo(result.to_day)}>
                Go to {weekdayName(result.to_day)}
              </Button>
            ) : null}
            <Button variant="go" onClick={onClose}>
              Done
            </Button>
          </>
        }
      >
        <div className={styles.wrapBody}>
          <p className={styles.lead}>
            {result.moved
              ? `${result.moved} ${plural(result.moved)} moved to ${dayLong(result.to_day)}.`
              : 'Nothing moved on.'}{' '}
            {result.not_done
              ? `${result.not_done} ${plural(result.not_done)} stayed on ${dayName} as not done.`
              : ''}
          </p>
          <p className={styles.aside}>
            {dayName}’s day report now lists what was done, what moved and what did not.
          </p>
        </div>
      </Modal>
    )
  }

  return (
    <Modal
      title={`Wrap up ${dayLong(day)}`}
      onClose={onClose}
      footer={
        <>
          <span className={styles.wrapNote}>
            {staying ? `${staying} ${plural(staying)} will stay on ${dayName} as not done.` : null}
          </span>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="go"
            disabled={!plan.data || wrap.isPending || (target === 'other' && otherDay <= day)}
            onClick={() => wrap.mutate()}
          >
            {moving.length ? `Move ${moving.length} to ${toName}` : `Close ${dayName}`}
          </Button>
        </>
      }
    >
      <div className={styles.wrapBody}>
        <ErrorNotice error={plan.error ?? wrap.error} />
        {plan.data && openIds.length === 0 ? (
          <EmptyState>Nothing is open on {dayName}. There is nothing to move on.</EmptyState>
        ) : null}
        {openIds.length ? (
          <>
            <p className={styles.lead}>
              {openIds.length} {plural(openIds.length)} still open. Untick any that should not move;
              their cards go onto that day with them.
            </p>
            <div className={styles.wrapTarget}>
              <span className={styles.fieldName}>Move them to</span>
              <SegmentedChoice
                label="Which day to move them to"
                value={target}
                onChange={setTarget}
                options={[
                  { value: 'next', label: `Next working day · ${dayShort(working)}` },
                  ...(tomorrow === working
                    ? []
                    : [{ value: 'tomorrow' as const, label: `Tomorrow · ${dayShort(tomorrow)}` }]),
                  { value: 'other', label: 'Another day' },
                ]}
              />
              {target === 'other' ? (
                <label className={styles.dayField}>
                  <span className="visually-hidden">The day to move them to</span>
                  <input
                    type="date"
                    value={otherDay}
                    min={tomorrow}
                    onChange={(event) => setOtherDay(event.currentTarget.value || tomorrow)}
                  />
                </label>
              ) : null}
            </div>
            <label className={styles.wrapAll}>
              <input
                type="checkbox"
                checked={moving.length === openIds.length}
                ref={(box) => {
                  if (box) box.indeterminate = moving.length > 0 && moving.length < openIds.length
                }}
                onChange={() =>
                  setKept(moving.length === openIds.length ? new Set(openIds) : new Set())
                }
              />
              <span>All open to-dos</span>
              <span className={styles.paneNote}>
                {moving.length} of {openIds.length} ticked
              </span>
            </label>
            {open.map(({ pick, todos }) => (
              <div key={pick.id} className={styles.wrapCard}>
                <p className={styles.wrapCardHead}>
                  <span className={styles.reference}>{pick.task.reference}</span> {pick.task.title}
                </p>
                <ul className={styles.todos}>
                  {todos.map((todo) => {
                    const ticked = !kept.has(todo.id)
                    return (
                      <li
                        key={todo.id}
                        className={ticked ? styles.todo : `${styles.todo} ${styles.kept}`}
                      >
                        <input
                          type="checkbox"
                          checked={ticked}
                          aria-label={ticked ? 'Leave this one behind' : 'Move this one on'}
                          onChange={() => flip(todo.id)}
                        />
                        <span className={styles.todoText}>
                          <Tagged text={todo.text} members={people} files={files} />
                        </span>
                        <span className={styles.todoMark}>
                          {ticked ? `→ ${weekdayShort(toDay)}` : 'stays, not done'}
                        </span>
                      </li>
                    )
                  })}
                </ul>
              </div>
            ))}
          </>
        ) : null}
      </div>
    </Modal>
  )
}

/** `Friday`. */
function weekdayName(iso: string): string {
  return localDate(iso).toLocaleDateString('en-GB', { weekday: 'long' })
}
