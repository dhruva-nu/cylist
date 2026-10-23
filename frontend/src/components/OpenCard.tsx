/**
 * One card, opened over whatever you were reading.
 *
 * The sidebar's two panels both list cards — what is due today, and what
 * happened to one yesterday — and a list of cards you cannot open is a list
 * that leaves you to go and find each one by hand, with a search box and the
 * reference typed out. There is no route for a single card, so this is how one
 * is shown from anywhere: the same dialog the board opens, over the top.
 *
 * Its own module because both panels need it and neither should have to import
 * the other to get it. It is also the reason `Modal` keeps a stack — this
 * opens over a dialog as readily as over a page, and only the innermost of
 * them may answer Escape.
 *
 * A card is addressed by reference rather than by id. That is what the panels
 * hold and what `/tasks/{ref}` takes, so nothing has to be looked up first.
 */

import { queryKeys, useBoard, useGoals, useInvalidate, useTemplates } from '../api/queries'
import { TaskDialog } from './TaskDialog'
import { useAnnouncer } from './ui'

export function OpenCard({
  projectKey,
  reference,
  onOpen,
  onClose,
}: {
  projectKey: string
  reference: string
  /** Following a sub-task out of the card above it, without closing first. */
  onOpen: (reference: string) => void
  onClose: () => void
}) {
  const invalidate = useInvalidate()
  const { announce } = useAnnouncer()

  /**
   * The three lists a card dialog needs.
   *
   * Fetched here rather than by whatever opened this, so a panel that lists
   * twenty cards pays for none of it until one is asked for. The keys are the
   * ones the board and a goal's page already use, so on the screens where a
   * card is most likely to be opened they come out of the cache and cost
   * nothing.
   */
  const board = useBoard(projectKey)
  const templates = useTemplates(projectKey)
  const goals = useGoals(projectKey)

  /**
   * What an edit made here has to invalidate.
   *
   * The day report among it, and by prefix rather than by day: moving a card
   * changes the line a report draws for it, and every day a stepper has been
   * through is a cached query that would otherwise keep the sentence it was
   * written with. The board's own keys are here too, because a card edited
   * from the sidebar is a card the page behind it is very likely showing.
   */
  async function refresh() {
    await invalidate(
      queryKeys.dayReports(projectKey),
      queryKeys.board(projectKey),
      queryKeys.tasks(projectKey),
      queryKeys.allTasks,
      queryKeys.goals(projectKey),
      queryKeys.allGoals,
    )
  }

  const columns = board.data?.columns ?? []
  const firstColumn = columns[0]
  // The board is fetched by the Today panel in the same sidebar, so it is all
  // but always in hand by the time anybody clicks; the guard is for the
  // instant before it is, and for a project whose board has no columns at all.
  if (!firstColumn) return null

  return (
    <>
      <TaskDialog
        projectKey={projectKey}
        taskId={reference}
        columns={columns}
        firstColumn={firstColumn}
        templates={templates.data ?? []}
        goals={goals.data ?? []}
        announce={announce}
        onOpenTask={onOpen}
        onDone={refresh}
        onClose={onClose}
      />
    </>
  )
}
