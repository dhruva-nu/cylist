/**
 * A project's goals — the epics its cards are written under.
 *
 * A goal is not a card and this is not a board: there is nothing to drag, no
 * column to be in, and no order but the one the server sends (open first, the
 * nearest deadline at the top). What each one shows is how much of it is left,
 * because that is the only question a list of epics is ever asked.
 *
 * There are two ways to read the same goals. The list answers "how far along
 * is each of these"; the calendar answers "what is coming, and when" — the
 * question the list can only answer by making you compare a column of dates in
 * your head. Neither is a different page, because they are the same goals.
 */

import { useParams } from '@tanstack/react-router'
import { useState } from 'react'
import type { Goal } from '../../api/client'
import { queryKeys, useGoals, useInvalidate, useMembers } from '../../api/queries'
import { GoalDialog } from '../../components/GoalDialog'
import { useProjectFiles } from '../../components/projectFiles'
import { PageHead } from '../../components/Shell'
import { SegmentedChoice } from '../../components/SegmentedChoice'
import { Button, EmptyState, ErrorBanner, useAnnouncer } from '../../components/ui'
import { readStored, writeStored } from '../../storage'
import { usePermissions } from '../usePermissions'
import { GoalCalendar } from './GoalCalendar'
import { GoalGrid } from './GoalGrid'
import styles from './ProjectGoals.module.css'

type View = 'list' | 'calendar'

const VIEWS: { value: View; label: string }[] = [
  { value: 'list', label: 'List' },
  { value: 'calendar', label: 'Calendar' },
]

function viewKey(projectKey: string): string {
  return `cylist.goals.view.${projectKey}`
}

function readView(projectKey: string): View {
  return readStored(viewKey(projectKey)) === 'calendar' ? 'calendar' : 'list'
}

/**
 * Which of the two the reader is on, remembered per project.
 *
 * Per project because it is a fact about the project rather than about the
 * reader: a handful of goals with no dates on them is a list, and a roadmap of
 * twenty with a target each is a calendar.
 *
 * Written on the press rather than by an effect watching the view, and read
 * afresh when the project changes. The effects this replaces wrote the old
 * project's view under the new project's key on the way across, overwriting
 * whatever the reader had chosen there.
 */
function useGoalView(projectKey: string) {
  const [chosen, setChosen] = useState(() => ({ projectKey, view: readView(projectKey) }))

  // Another project's choice is not this one's: caught up during render, so
  // the page never draws the old project's view first.
  if (chosen.projectKey !== projectKey) {
    setChosen({ projectKey, view: readView(projectKey) })
  }

  function setView(view: View) {
    setChosen({ projectKey, view })
    writeStored(viewKey(projectKey), view)
  }

  return { view: chosen.view, setView }
}

export function ProjectGoals() {
  const { projectKey } = useParams({ from: '/p/$projectKey/goals' })
  const may = usePermissions(projectKey)
  const invalidate = useInvalidate()
  const { announce } = useAnnouncer()
  const [editing, setEditing] = useState<Goal | 'new' | null>(null)
  const { view, setView } = useGoalView(projectKey)

  const goals = useGoals(projectKey)
  /** For the `>` tags in a goal's description, drawn as links to the files. */
  const files = useProjectFiles(projectKey)
  const members = useMembers(projectKey)

  async function refresh() {
    await invalidate(queryKeys.goals(projectKey), queryKeys.allGoals, queryKeys.tasks(projectKey))
  }

  if (goals.isPending) return <EmptyState>Loading goals…</EmptyState>
  if (goals.error) return <ErrorBanner>{goals.error.message}</ErrorBanner>

  const allGoals = goals.data
  const openGoals = allGoals.filter((goal) => goal.status === 'open')
  const settledGoals = allGoals.filter((goal) => goal.status !== 'open')
  const memberList = members.data?.members ?? []

  return (
    <>
      <PageHead
        title="Goals"
        actions={
          <>
            {/* List or calendar: the same control the vault uses for its kinds. */}
            <SegmentedChoice
              label="How to read the goals"
              options={VIEWS}
              value={view}
              onChange={setView}
            />
            {/* Not drawn for a role that is not allowed goals: a button that
                always refuses is worse than one that is not there. */}
            {may('goals') ? (
              <Button variant="go" onClick={() => setEditing('new')}>
                + New goal
              </Button>
            ) : null}
          </>
        }
      >
        What the board's cards are work towards. A card wears its goal's colour on the rail down its
        left-hand side, so a column shows which goals it is made of before a word of it is read.
      </PageHead>

      {allGoals.length === 0 ? (
        <EmptyState>
          No goals yet. A goal is a heading — “Search revamp”, “SOC 2 readiness” — that a board's
          cards are written under.
        </EmptyState>
      ) : null}

      {view === 'calendar' && allGoals.length ? (
        <GoalCalendar goals={allGoals} projectKey={projectKey} />
      ) : null}

      {view === 'list' && openGoals.length ? (
        <GoalGrid goals={openGoals} projectKey={projectKey} members={memberList} files={files} />
      ) : null}

      {view === 'list' && settledGoals.length ? (
        <>
          {/* Kept on the page rather than hidden behind a filter: a goal that
              was reached is the most useful thing a goals page holds three
              months later, and one that was dropped is the second. */}
          <h2 className={styles.settledHead}>Settled</h2>
          <GoalGrid
            goals={settledGoals}
            projectKey={projectKey}
            members={memberList}
            files={files}
          />
        </>
      ) : null}

      {editing ? (
        <GoalDialog
          projectKey={projectKey}
          goal={editing === 'new' ? null : editing}
          members={memberList}
          announce={announce}
          onDone={refresh}
          onClose={() => setEditing(null)}
        />
      ) : null}
    </>
  )
}
