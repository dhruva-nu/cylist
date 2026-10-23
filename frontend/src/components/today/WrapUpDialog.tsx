import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../../api/client'
import { usePeople } from '../../api/queries'
import {
  dayLong,
  dayShort,
  localDate,
  localZone,
  nextWorkingDay,
  shiftDay,
  weekdayShort,
} from '../dates'
import { Modal } from '../Modal'
import { useProjectFiles } from '../projectFiles'
import { SegmentedChoice } from '../SegmentedChoice'
import { Button, EmptyState, ErrorNotice, Tagged } from '../ui'
import styles from '../Today.module.css'
import { handlesOwnErrors } from '../../api/mutationErrors'
import { usePlan, useRefreshPlans } from './hooks'

/**
 * Closing a day: what is still open, ticked to move on unless untold, and the
 * day it goes to — the next working day unless somebody picks another.
 *
 * Everything left unticked stays on the day as not done. Nothing is lost
 * either way: a moved to-do stays on its day as moved, and a not-done one can
 * be reopened.
 */
export function WrapUpDialog({
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
