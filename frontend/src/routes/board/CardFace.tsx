/**
 * The pieces of a card's face that only say something: the priority, the
 * date owed, an agent's line, the owner, and the facts kept off the face until
 * the card is rested on.
 */

import type { Person, Task } from '../../api/client'
import { dueBucket, formatDue, formatDueLong } from '../../components/dates'
import { priorityInFull, STATUS_LABELS } from '../../components/taskLabels'
import {
  AlertIcon,
  Avatar,
  CalendarIcon,
  ClockIcon,
  CommentIcon,
  JiraIcon,
  jiraRefLabel,
  PriorityIcon,
} from '../../components/ui'
import type { AgentIndicator } from '../agentState'
import { stopAtCard } from './keepFromCard'
import styles from './TaskCard.module.css'

/**
 * The date a card is being asked for now, and the column it is wanted in by
 * then — nulls for a card that owes nothing.
 */
export function owedDateOf(task: Task) {
  /* Cancelling the work is what stops it owing anybody a date, and finishing
     it does the same — a card already in the board's last column has nothing
     left to be late for. A red "12d late" shouting from a card that is done,
     same as one nobody is going to do, is the loudest wrong thing the board
     could say. */
  const dated = task.status !== 'cancelled' && task.finished_at === null
  /* The next date owed rather than the last column's: a card wanted in Review
     by Friday is late on Saturday, whatever the end of the board says. The
     server settles a date once the card reaches the column it was for, so a
     card that has arrived stops being asked for it — and a card in the last
     column, which is done, owes nothing at all. */
  const owed = dated ? task.next_due_date : null
  const stage =
    task.column_due_dates.find((entry) => !entry.met && entry.due_date === owed)?.column_name ??
    null
  return { owed, stage }
}

/**
 * The priority, as its glyph and its name. P3 is the baseline every card
 * starts on, so flagging it too would be noise on every single card.
 */
export function PriorityMark({ priority }: { priority: Task['priority'] }) {
  if (priority === 'p3') return null

  return (
    <span
      className={`${styles.prio} ${styles[`priority_${priority}`]}`}
      title={priorityInFull(priority)}
    >
      <PriorityIcon priority={priority} size={12} />
      <span aria-hidden="true">{priority.toUpperCase()}</span>
      <span className="visually-hidden">{priorityInFull(priority)}</span>
    </span>
  )
}

/**
 * A due date on a card, drawn as near or as far as it is.
 *
 * The two ends of the ramp are the design: a date that has passed or is
 * passing today says what it means in words and in colour, because it is
 * asking for something; a date further out is the date itself in quiet ink,
 * because it is only telling you. Nothing between them changes size, so a
 * column of cards keeps one edge down its right-hand side.
 *
 * The visible text is abbreviated in four of the five states, so each mark
 * carries the whole sentence as well — spoken instead of the abbreviation, and
 * shown on hover. `column` is named in that sentence when the date is a stage's
 * rather than the card's own: the mark itself is the same either way, because
 * what it is asking for is the same.
 */
export function DueMark({ iso, column }: { iso: string | null; column: string | null }) {
  const bucket = dueBucket(iso)
  if (iso === null || bucket.kind === 'none') return null

  const when = `${formatDueLong(iso)}${column ? ` in ${column}` : ''}`
  const daysLate = 'days' in bucket ? bucket.days : 0
  const { className, icon, text, said } = {
    late: {
      className: styles.dueLate,
      icon: <AlertIcon size={13} />,
      text: `${daysLate}d late`,
      said: `${daysLate} days late, due ${when}`,
    },
    today: {
      className: styles.dueToday,
      icon: <ClockIcon size={13} />,
      text: 'Today',
      said: `Due today, ${when}`,
    },
    tomorrow: {
      className: styles.dueTomorrow,
      icon: <CalendarIcon size={13} />,
      text: 'Tomorrow',
      said: `Due tomorrow, ${when}`,
    },
    soon: {
      className: styles.dueSoon,
      icon: <CalendarIcon size={13} />,
      text: formatDue(iso),
      said: `Due ${when}`,
    },
    later: {
      className: styles.dueLater,
      icon: <CalendarIcon size={13} />,
      text: formatDue(iso),
      said: `Due ${when}`,
    },
  }[bucket.kind]

  return (
    <span className={`${styles.due} ${className}`} title={said}>
      {icon}
      <span className={styles.dueText} aria-hidden="true">
        {text}
      </span>
      <span className="visually-hidden">{said}</span>
    </span>
  )
}

/**
 * What an agent on the card is doing. Drawn in the slot the stages take, behind
 * a rule in the colour the card's border is in, so the line and the border can
 * never disagree about what state it is.
 */
export function AgentLine({
  agent,
  sessionCount,
}: {
  agent: AgentIndicator
  sessionCount: number
}) {
  return (
    <div
      className={styles.agentLine}
      // The line is cut to the card's width; the whole of it is a hover away.
      title={agent.detail ? `${agent.label} · ${agent.detail}` : agent.label}
    >
      <span className={styles.agentLabel}>{agent.label}</span>
      {agent.detail ? <span className={styles.agentDetail}>{agent.detail}</span> : null}
      {sessionCount > 1 ? (
        <span className={styles.agentCount} title={`${sessionCount} sessions`}>
          ×{sessionCount}
        </span>
      ) : null}
    </div>
  )
}

/**
 * The owner's badge, made a control: a native select sized over the disc so
 * clicking it reassigns the card without leaving the board for the dialog —
 * the same disguise `ProjectSwitcher` wears on the breadcrumb (Shell.tsx).
 *
 * Every event the select can fire is stopped here before it reaches the
 * card underneath — see `keepFromCard` — and `StageMark` wears the same
 * guard around the stage slider.
 */
export function AssigneePicker({
  task,
  members,
  onSetAssignee,
}: {
  task: Task
  members: Person[]
  onSetAssignee: (assigneeId: string) => void
}) {
  return (
    <span className={styles.assigneePicker} onPointerDown={stopAtCard}>
      <Avatar name={task.assignee.name} colour={task.assignee.colour} small />
      <select
        className={styles.assigneeSelect}
        aria-label={`Reassign ${task.reference}, now ${task.assignee.name}`}
        value={task.assignee.id}
        onClick={stopAtCard}
        onKeyDown={stopAtCard}
        onChange={(event) => {
          event.stopPropagation()
          const next = event.target.value
          if (next !== task.assignee.id) onSetAssignee(next)
        }}
      >
        {members.map((person) => (
          <option key={person.id} value={person.id}>
            {person.name}
          </option>
        ))}
      </select>
    </span>
  )
}

/**
 * The facts a card keeps off its face: how much has been said, how it stands,
 * who it waits on, what it is part of, who owns its parts and the Jira key the
 * face only drew the mark for. Icon and value, one line, nothing labelled —
 * the icon is the label.
 */
export function CardFacts({ task }: { task: Task }) {
  return (
    <div className={styles.facts}>
      {task.comment_count ? (
        <span className={styles.fact} title={`${task.comment_count} comments`}>
          <CommentIcon size={13} />
          <span aria-hidden="true">{task.comment_count}</span>
          <span className="visually-hidden">{task.comment_count} comments</span>
        </span>
      ) : null}
      {task.status === 'active' ? null : (
        <span className={`${styles.fact} ${styles.status} ${styles[`status_${task.status}`]}`}>
          {STATUS_LABELS[task.status]}
        </span>
      )}
      {task.waiting_on.length ? (
        <span className={styles.fact} title="Waiting on">
          <ClockIcon size={13} />
          {task.waiting_on.map((person) => `@${person.name.split(' ')[0]}`).join(' ')}
          <span className="visually-hidden">waiting on them</span>
        </span>
      ) : null}
      {task.parent_reference ? (
        <span className={styles.fact} title={`Sub-task of ${task.parent_reference}`}>
          <span aria-hidden="true">↳</span>
          <span className={styles.factMono}>{task.parent_reference}</span>
        </span>
      ) : null}
      {task.subtask_assignees.length ? (
        <span
          className={`${styles.fact} ${styles.owners}`}
          title={`Sub-tasks: ${task.subtask_assignees.map((person) => person.name).join(', ')}`}
        >
          {task.subtask_assignees.map((person) => (
            <Avatar key={person.id} name={person.name} colour={person.colour} small />
          ))}
        </span>
      ) : null}
      {task.jira_ref ? (
        <span className={styles.fact}>
          <JiraIcon size={13} />
          <span className={styles.factMono}>{jiraRefLabel(task.jira_ref)}</span>
        </span>
      ) : null}
    </div>
  )
}
