import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type Task, type TodayPlan } from '../../api/client'
import { useIdentity } from '../../api/queries'
import { formatDue, formatDueLong, localToday } from '../dates'
import { SegmentedChoice } from '../SegmentedChoice'
import { openCardGroups } from '../today'
import { Button, ErrorNotice } from '../ui'
import styles from '../Today.module.css'
import { handlesOwnErrors } from '../../api/mutationErrors'
import { useBoardCards, useRefreshPlans } from './hooks'

/** The left pane: every open card on the board, to pick the day's from. */
export function CardsPane({
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
