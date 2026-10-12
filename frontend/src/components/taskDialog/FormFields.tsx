/**
 * The fields on the form's tabs: Basics, the top of Details, and Column dates.
 *
 * The form is long enough that the fields that decide what the card *is* —
 * its name, what done looks like, how urgent, what kind — were being scrolled
 * past on the way to the ones that decide how it is tracked. Splitting them
 * puts the first decision on the first screen.
 */

import type {
  BoardColumn,
  FiledItem,
  Goal,
  Person,
  TaskInput,
  TaskPriority,
  TaskType,
  Template,
} from '../../api/client'
import { Field, FieldPair } from '../Modal'
import { MentionBox } from '../Mentions'
import { PRIORITIES } from '../taskLabels'
import { Button } from '../ui'
import { PrRefListEditor } from './PullRequests'
import { SubStatusListEditor } from './SubStatusListEditor'
import styles from './FormFields.module.css'

const TYPES: TaskType[] = ['feature', 'bug', 'chore']

/** A select's value as one of the options it was drawn with — a lookup rather than a cast. */
function oneOf<T extends string>(options: readonly T[], value: string, fallback: T): T {
  return options.find((option) => option === value) ?? fallback
}

const PRIORITY_VALUES: TaskPriority[] = PRIORITIES.map((option) => option.value)

/**
 * The Basics tab: what the card *is* — its name, what done looks like, how
 * urgent, what kind, the template it follows and the goal it is written under.
 */
export function BasicsFields({
  form,
  onChange,
  members,
  files,
  templates,
  columns,
  goals,
  offerGoal,
}: {
  form: TaskInput
  onChange: (changes: Partial<TaskInput>) => void
  members: Person[]
  files: readonly FiledItem[]
  templates: Template[]
  columns: BoardColumn[]
  goals: Goal[]
  offerGoal: boolean
}) {
  return (
    <>
      <Field label="Task name" required>
        <input
          value={form.title}
          maxLength={200}
          onChange={(event) => onChange({ title: event.target.value })}
          placeholder="Short, verb-first"
        />
      </Field>

      <Field
        label="Description"
        required
        hint="Type @ to tag someone on the project, > to tag one of its files."
      >
        <MentionBox
          multiline
          value={form.description}
          onChange={(description) => onChange({ description })}
          members={members}
          files={files}
          placeholder="What done looks like"
        />
      </Field>

      <FieldPair>
        <Field label="Priority" required>
          <select
            value={form.priority}
            onChange={(event) =>
              onChange({ priority: oneOf(PRIORITY_VALUES, event.target.value, form.priority) })
            }
          >
            {PRIORITIES.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label} — {option.means}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Type" required>
          <select
            value={form.type}
            onChange={(event) => onChange({ type: oneOf(TYPES, event.target.value, form.type) })}
          >
            {TYPES.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>
        </Field>
      </FieldPair>

      <Field label="Template" hint={templateHint(templates, form.template_id, columns)}>
        <select
          value={form.template_id ?? ''}
          onChange={(event) => onChange({ template_id: event.target.value || null })}
        >
          <option value="">No template — goes anywhere on the board</option>
          {templates.map((template) => (
            <option key={template.id} value={template.id}>
              {template.name}
            </option>
          ))}
        </select>
      </Field>

      {/* Not offered on a sub-task: a sub-task belongs to its card, and its
          card is what belongs to a goal. The API refuses one either way —
          this is only the form agreeing with it. */}
      {offerGoal ? (
        <Field
          label="Goal"
          hint="The epic this card is work towards. Its colour becomes the card's rail on the board."
        >
          <select
            value={form.goal_id ?? ''}
            onChange={(event) => onChange({ goal_id: event.target.value || null })}
          >
            <option value="">No goal — this card stands on its own</option>
            {goals.map((goal) => (
              <option key={goal.id} value={goal.id}>
                {goal.name}
                {goal.status === 'open' ? '' : ` (${goal.status})`}
              </option>
            ))}
          </select>
        </Field>
      ) : null}
    </>
  )
}

/**
 * What choosing a template commits the card to, in words.
 *
 * A template's stages are invisible until the card is dragged somewhere they
 * refuse, or stuck short of a sub-stage they demand. Saying it here is the
 * difference between a dropdown and a decision.
 */
function templateHint(
  templates: Template[],
  templateId: string | null,
  columns: BoardColumn[],
): string {
  if (!templateId) return 'Optional. A card with no template may sit in any column.'

  const template = templates.find((candidate) => candidate.id === templateId)
  if (!template) return ''
  if (!template.stages.length) {
    return `${template.name} has no columns set up yet, so its cards may sit anywhere.`
  }

  const named = template.allowed_column_ids
    .map((id) => columns.find((column) => column.id === id)?.name)
    .filter(Boolean)
  const staged = template.stages.some((stage) => stage.sub_stage_labels.length)
  return (
    `${template.name} cards go to ${named.join(' → ')}, and nowhere else.` +
    (staged ? ' Each column may set sub-stages a card must reach before leaving it.' : '')
  )
}

/**
 * The top of the Details tab: how the card is tracked — when it is wanted,
 * who has it, the stages it passes through, and its ticket and pull request.
 */
export function TrackingFields({
  form,
  onChange,
  members,
  subStatusIndex,
  onSubStatusIndex,
  onBoard,
  lastColumnName,
  onAskForColumnDates,
}: {
  form: TaskInput
  onChange: (changes: Partial<TaskInput>) => void
  members: Person[]
  subStatusIndex: number
  onSubStatusIndex: (index: number) => void
  /** Whether the card passes through columns, and so can be dated in them. */
  onBoard: boolean
  lastColumnName: string | undefined
  /** Opens the column-dates tab, from the + beside the due date. */
  onAskForColumnDates: () => void
}) {
  return (
    <>
      <FieldPair>
        <Field
          label="Due date"
          {...(onBoard ? { hint: `When it is wanted done — in ${lastColumnName}.` } : {})}
        >
          <div className={styles.dueRow}>
            <input
              type="date"
              value={form.due_date ?? ''}
              onChange={(event) => onChange({ due_date: event.target.value })}
            />
            {/* The way in to the dates before this one. A card is dated at
                the end of the board by default and the columns on the way
                are asked for, not offered: most cards want one date, and a
                form that opens with six is a form that reads as six
                questions. */}
            {onBoard ? (
              <Button
                variant="ghost"
                small
                title="Set a date per column"
                aria-label="Set a date per column"
                onClick={onAskForColumnDates}
              >
                +
              </Button>
            ) : null}
          </div>
        </Field>
        <Field label="Assignee" required>
          <select
            value={form.assignee_id}
            onChange={(event) => onChange({ assignee_id: event.target.value })}
          >
            {members.map((person) => (
              <option key={person.id} value={person.id}>
                {person.name} — {person.title}
              </option>
            ))}
          </select>
        </Field>
      </FieldPair>

      <Field
        label="Sub-status"
        hint="Up to 4 stages, in order. Moving the card between them happens on the board. Type @ to tag someone."
      >
        <SubStatusListEditor
          value={form.sub_statuses}
          current={subStatusIndex}
          members={members}
          onChange={(sub_statuses, current) => {
            onChange({ sub_statuses })
            onSubStatusIndex(current)
          }}
        />
      </Field>

      <Field label="Jira">
        <input
          value={form.jira_ref ?? ''}
          onChange={(event) => onChange({ jira_ref: event.target.value })}
          placeholder="ATL-00 or a URL"
        />
      </Field>

      <Field
        label="Pull requests"
        hint="One line each. A change that took a backend pull request and a frontend one is still one card."
      >
        <PrRefListEditor value={form.pr_refs} onChange={(pr_refs) => onChange({ pr_refs })} />
      </Field>
    </>
  )
}

/** The Column dates tab: a date box for every column before the last. */
export function ColumnDatesFields({
  stages,
  reachedColumnIndex,
  dates,
  onChange,
}: {
  stages: BoardColumn[]
  /** How far along the board the card has got, so a date behind it says so. */
  reachedColumnIndex: number
  dates: Record<string, string>
  onChange: (dates: Record<string, string>) => void
}) {
  return (
    <>
      <p className={styles.note}>
        The day this card is wanted in each column. Any of them, none of them — a column left empty
        simply has no date. The last column is the card&rsquo;s due date, on Details.
      </p>
      {stages.map((column, index) => (
        <Field
          key={column.id}
          label={column.name}
          {...(index <= reachedColumnIndex ? { hint: 'The card has already been here.' } : {})}
        >
          <input
            type="date"
            value={dates[column.id] ?? ''}
            onChange={(event) => onChange({ ...dates, [column.id]: event.target.value })}
          />
        </Field>
      ))}
    </>
  )
}
