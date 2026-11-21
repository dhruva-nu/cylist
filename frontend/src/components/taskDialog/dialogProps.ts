import type { BoardColumn, FiledItem, Goal, Person, Template } from '../../api/client'

/** What whoever opens a task dialog hands it — the board, a goal's page, the sidebar. */
export interface TaskDialogProps {
  projectKey: string
  /** Null opens the dialog for a new task, which is locked to the first column. */
  taskId: string | null
  /**
   * Set on a new task to make it a sub-task of that reference. Ignored when
   * `taskId` is set: a card's parentage is decided when it is created.
   */
  parentRef?: string | null
  columns: BoardColumn[]
  firstColumn: BoardColumn
  /**
   * The project's task templates, for the picker on the form.
   *
   * Passed in rather than fetched here because the board needs them too: it
   * greys out the columns a card being dragged is not allowed in, which is the
   * same rule read from the other end.
   */
  templates: Template[]
  /**
   * The project's goals, for the picker on the form.
   *
   * Open ones and settled ones alike: a card can be moved onto a goal that has
   * already been achieved — that is how a straggler gets counted — and a
   * picker that hid the goal a card is already on would look like the card had
   * lost it.
   */
  goals: Goal[]
  /** A new card starts on this goal. The board's lanes pass their own. */
  defaultGoalId?: string | null
  /** Opens another card in this dialog's place — a sub-task, from the list. */
  onOpenTask?: ((taskRef: string) => void) | undefined
  /** Asks the board to open a new-sub-task dialog under this reference. */
  onSplit?: ((parentRef: string) => void) | undefined
  /**
   * Where to say what happened. The dialog closes on success, taking any live
   * region inside it with it before a screen reader could read one, so the
   * board keeps the region and the dialog only supplies the words.
   */
  announce: (message: string) => void
  onDone: () => Promise<void>
  onClose: () => void
}

/**
 * What the dialog has loaded by the time either half of it is drawn: the
 * project's people, for the pickers and the `@` tags, and its files, for the
 * `>` tags. The files are empty while they load, which leaves a `>` an
 * ordinary character until they arrive.
 */
export interface DialogData {
  members: Person[]
  files: readonly FiledItem[]
}
