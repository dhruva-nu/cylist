/**
 * The pull requests a card names: read on the detail view, edited on the form.
 */

import { useMutation } from '@tanstack/react-query'
import { api, type PrRef, type PrState, type TaskDetail } from '../../api/client'
import { addPrRef, removePrRefAt, setPrRefAt, setPrStateAt } from '../prRefs'
import { Button, ErrorNotice, prRefLabel, TaskRef } from '../ui'
import { useRefreshTask } from './useRefreshTask'
import styles from './PullRequests.module.css'
import { handlesOwnErrors } from '../../api/mutationErrors'

/**
 * How far a pull request got, in the order it gets there (CYLIST-76).
 *
 * One list for both places a state is picked — the detail view's row and the
 * editor's — so the two cannot offer different words for the same thing.
 */
const PR_STATES: { value: PrState; label: string }[] = [
  { value: 'raised', label: 'Raised' },
  { value: 'approved', label: 'Approved' },
  { value: 'merged', label: 'Merged' },
]

/**
 * The state a `<select>` just reported, as one of the three.
 *
 * A lookup rather than a cast: the value can only be one of the options this
 * file rendered, and saying so by finding it costs nothing and cannot go stale
 * if the list gains a member.
 */
function prStateNamed(value: string): PrState {
  return PR_STATES.find((option) => option.value === value)?.value ?? 'raised'
}

/** The state in lower case, to finish a spoken sentence about one. */
function prStateWord(state: PrState): string {
  return PR_STATES.find((option) => option.value === state)?.label.toLowerCase() ?? state
}

/** The three states as a select's options. */
function PrStateOptions() {
  return (
    <>
      {PR_STATES.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </>
  )
}

/**
 * The card's pull requests, read: each one a link, and how far it got.
 *
 * The state is a select rather than a word, which makes this the second
 * control on a view whose whole discipline is that a card's fields are text
 * here. It sits on the right side of the same line the sub-task tick box sits
 * on — *what* a control changes, not whether one exists. A pull request is
 * somebody else's object: it is raised, reviewed and merged on a forge, and
 * what this says is what happened over there, the same way a tick box settles
 * a different item. Putting it behind the pencil would mean opening a form
 * over every field of the card to record that a reviewer said yes.
 *
 * Saved on choosing, with no Save button, because a select that only takes
 * effect when you remember to press Save is a select that lies — the reasoning
 * the tick box is built on, and the assignee badge on a board card.
 *
 * Keyed by the URL: the server stores no duplicates, so it is the stable
 * identity an index is not. The index is still what the edit is made by, since
 * two rows could hold the same address for as long as one is being typed.
 */
export function PrRefsRead({
  task,
  announce,
  onDone,
}: {
  task: TaskDetail
  announce: (message: string) => void
  onDone: () => Promise<void>
}) {
  const refreshTask = useRefreshTask(onDone)

  const move = useMutation({
    meta: handlesOwnErrors,
    mutationFn: ({ refs }: { refs: PrRef[]; label: string; state: PrState }) =>
      api.updateTask(task.id, { pr_refs: refs }),
    onSuccess: async (_updated, { label, state }) => {
      announce(`Pull request ${label} is ${prStateWord(state)}.`)
      // The board behind the dialog draws a mark per pull request, in the
      // state's own colour, so it hears about this through the refresh too.
      await refreshTask(task.id)
    },
  })

  return (
    <>
      <ErrorNotice error={move.error} />
      <ul className={styles.prRefs}>
        {task.pr_refs.map((ref, index) => (
          <li key={ref.url} className={styles.prRefRow}>
            <TaskRef kind="pr" value={ref.url} state={ref.state} />
            <select
              className={styles.prStateSelect}
              // Named by the reference rather than by its position, because
              // the row above it has just been read out as "#212, merged" and
              // "pull request 1" is a different way of counting.
              aria-label={`State of pull request ${prRefLabel(ref.url)}`}
              value={ref.state}
              disabled={move.isPending}
              onChange={(event) => {
                const state = prStateNamed(event.target.value)
                if (state === ref.state) return
                move.mutate({
                  refs: setPrStateAt(task.pr_refs, index, state),
                  label: prRefLabel(ref.url),
                  state,
                })
              }}
            >
              <PrStateOptions />
            </select>
          </li>
        ))}
      </ul>
    </>
  )
}

/**
 * The pull requests a card names, edited by hand: one row each, added and
 * removed.
 *
 * A plain list rather than the stage editor's numbered one: the order is kept,
 * but nothing points at a current entry and nothing downstream reads position
 * as meaning, so the up and down buttons would be three more controls buying
 * one cosmetic choice. Removing and retyping is the rare case; adding is the
 * common one, and it is the button at the bottom.
 *
 * An empty list draws no rows at all, only the button — a card with no pull
 * request yet, which most cards are for most of their lives, should not look
 * like a card with an empty one.
 */
export function PrRefListEditor({
  value,
  onChange,
}: {
  value: PrRef[]
  onChange: (value: PrRef[]) => void
}) {
  return (
    <div className={styles.prRefEditor}>
      {value.map((ref, index) => (
        // Keyed by position, not by the reference: the reference is what is
        // being typed, so keying by it would remount the input on every
        // keystroke and lose the cursor.
        <div key={index} className={styles.prRefEditorRow}>
          <input
            className={styles.grow}
            value={ref.url}
            maxLength={200}
            placeholder="#000 or a URL"
            aria-label={`Pull request ${index + 1}`}
            onChange={(event) => onChange(setPrRefAt(value, index, event.target.value))}
          />
          {/* Counted rather than named here, unlike the detail view's: the
              address beside it is being typed, so there may not be one to
              name yet. */}
          <select
            className={styles.prStateSelect}
            aria-label={`State of pull request ${index + 1}`}
            value={ref.state}
            onChange={(event) =>
              onChange(setPrStateAt(value, index, prStateNamed(event.target.value)))
            }
          >
            <PrStateOptions />
          </select>
          <Button
            variant="ghost"
            small
            aria-label={`Remove pull request ${index + 1}`}
            title="Remove"
            onClick={() => onChange(removePrRefAt(value, index))}
          >
            ×
          </Button>
        </div>
      ))}
      <div>
        <Button variant="ghost" small onClick={() => onChange(addPrRef(value))}>
          + Add a pull request
        </Button>
      </div>
    </div>
  )
}
