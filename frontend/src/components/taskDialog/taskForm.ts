/**
 * The task form's arithmetic: what it opens holding, what it sends, what it
 * says about itself — none of it React, so the form component is left with
 * only its state and its markup.
 */

import {
  isMe,
  type BoardColumn,
  type ColumnDueDateInput,
  type Identity,
  type Person,
  type TaskDetail,
  type TaskInput,
  type TaskStatus,
} from '../../api/client'
import { cleanPrRefs } from '../prRefs'
import { STATUS_LABELS } from '../taskLabels'
import type { Tab } from './FormTabs'

/**
 * What the form opens holding: the card's own fields, or a new card's
 * defaults. A new card goes to whoever `identity` says is looking, and to the
 * first member while that is not known yet.
 */
export function initialFormFields(
  task: TaskDetail | null,
  members: Person[],
  identity: Identity | undefined,
  defaultGoalId: string | null | undefined,
): TaskInput {
  return {
    title: task?.title ?? '',
    description: task?.description ?? '',
    type: task?.type ?? 'feature',
    priority: task?.priority ?? 'p3',
    sub_statuses: task?.sub_statuses ?? [],
    // The date input's empty value is '', not null; `taskPayload` turns it back
    // into the null the API reads as "no date".
    due_date: task?.due_date ?? '',
    assignee_id:
      task?.assignee.id ??
      members.find((person) => isMe(person, identity))?.id ??
      members[0]?.id ??
      '',
    template_id: task?.template_id ?? null,
    goal_id: task?.goal_id ?? defaultGoalId ?? null,
    jira_ref: task?.jira_ref ?? '',
    pr_refs: task?.pr_refs ?? [],
  }
}

/**
 * The form as the API takes it: stage labels trimmed, and the blanks an input
 * holds turned back into the nulls the API means by "none".
 *
 * `columnDueDates` is sent whole on a card, and never on a sub-task — pass null
 * for one of those.
 */
export function taskPayload(
  form: TaskInput,
  columnDueDates: ColumnDueDateInput[] | null,
): TaskInput {
  return {
    ...form,
    sub_statuses: form.sub_statuses.map((label) => label.trim()),
    due_date: form.due_date || null,
    ...(columnDueDates ? { column_due_dates: columnDueDates } : {}),
    jira_ref: form.jira_ref?.trim() ? form.jira_ref.trim() : null,
    // A row somebody added and left empty is a box not filled in yet, so it is
    // dropped rather than sent. The server cleans the list too; doing it here
    // as well is what leaves the user looking at what was actually saved.
    pr_refs: cleanPrRefs(form.pr_refs),
  }
}

/**
 * The column-dates tab's boxes as the list the API takes.
 *
 * Empty boxes are dropped rather than sent as nulls: a column with no date is a
 * column with no row, which is also how a date is taken off again.
 */
export function columnDueDatesInput(
  stages: BoardColumn[],
  dates: Record<string, string>,
): ColumnDueDateInput[] {
  return stages.flatMap((column) => {
    const date = dates[column.id]
    return date ? [{ column_id: column.id, due_date: date }] : []
  })
}

/** The form's heading: the card being edited, or what is being written. */
export function formTitle(task: TaskDetail | null, parentRef: string | null | undefined): string {
  if (task) return `${task.reference} · editing`
  if (parentRef) return `New sub-task of ${parentRef}`
  return 'New task'
}

/** What the Save button says it will do. */
export function saveButtonLabel(
  saving: boolean,
  task: TaskDetail | null,
  parentRef: string | null | undefined,
): string {
  if (saving) return 'Saving…'
  if (task) return 'Save changes'
  if (parentRef) return 'Create sub-task'
  return 'Create task'
}

/**
 * One sentence covering everything a save did.
 *
 * A save is up to four requests, and hearing four separate confirmations of
 * one button press is worse than hearing none.
 */
export function summariseSave(
  reference: string,
  created: boolean,
  movedTo: string | null,
  newStatus: TaskStatus | null,
): string {
  const parts = [created ? `Created ${reference}.` : `Saved ${reference}.`]
  if (movedTo) parts.push(`Moved to ${movedTo}.`)

  // Only when it actually changed: "status active" after every save is noise
  // that trains people to stop listening to the region.
  if (newStatus) parts.push(`Status ${STATUS_LABELS[newStatus].toLowerCase()}.`)
  return parts.join(' ')
}

/**
 * Which of the form's two always-present tabs still has something required
 * left blank. Save is ready exactly when neither has.
 */
export function blankRequiredFields(form: TaskInput, reasonMissing: boolean) {
  return {
    basics: !form.title.trim() || !form.description.trim(),
    details:
      !form.assignee_id || !form.sub_statuses.every((label) => label.trim()) || reasonMissing,
  }
}

/**
 * The form's tabs, each marked when something required on it is still blank.
 *
 * Marked on the tab, not only on the Save button: a disabled Save with no
 * reason showing is a dead end when the reason is on a tab you cannot see.
 */
export function formTabs({
  blank,
  showColumnDates,
  existing,
}: {
  blank: { basics: boolean; details: boolean }
  showColumnDates: boolean
  /** Whether the card exists yet, and so has sub-tasks and comments of its own. */
  existing: boolean
}): Tab[] {
  return [
    { id: 'basics', label: 'Basics', incomplete: blank.basics },
    { id: 'details', label: 'Details', incomplete: blank.details },
    // Opened by the + beside the due date, and it stays open once a card has
    // dates on it: a schedule you had to go and ask for once should be in front
    // of you every time afterwards.
    ...(showColumnDates
      ? [{ id: 'dates' as const, label: 'Column dates', incomplete: false }]
      : []),
    ...(existing
      ? [{ id: 'work' as const, label: 'Sub-tasks & comments', incomplete: false }]
      : []),
  ]
}
