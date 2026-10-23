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

import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { queryKeys, useIdentity } from '../api/queries'
import { readStored, writeStored } from '../storage'
import { localToday } from './dates'
import { Modal } from './Modal'
import { OpenCard } from './OpenCard'
import { SidebarAction } from './SidebarSection'
import { lateAndUnplanned } from './today'
import { Button, ErrorNotice } from './ui'
import styles from './Today.module.css'
import { CARDS_FOLDED_KEY, useBoardCards, usePlan, useProjectKey } from './today/hooks'
import { DayNav } from './today/DayNav'
import { CardsPane } from './today/CardsPane'
import { PlanPane } from './today/PlanPane'
import { WrapUpDialog } from './today/WrapUpDialog'

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
