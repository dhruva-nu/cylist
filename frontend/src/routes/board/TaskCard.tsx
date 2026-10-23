/**
 * A card on the board.
 *
 * The card under the pointer is drawn twice over: dimmed where it started, and
 * again in a `DragOverlay` that floats above the board — `LiftedTaskCard`. A
 * card moved in place instead would be dragged around inside a column that
 * scrolls its own cards, which clips it at the column's edge the moment it
 * leaves — and grows that column's scrollable area as it goes, so the list
 * shifts under the pointer that is dragging out of it.
 */

import { useDndContext, useDraggable } from '@dnd-kit/core'
import { useCallback, type CSSProperties, type ReactNode } from 'react'
import type { AgentPresence, Person, Task } from '../../api/client'
import { Avatar, TaskRef } from '../../components/ui'
import { agentIndicator, isAnswerable, type AgentIndicator } from '../agentState'
import { CardPromptPopover, useCardPopover } from '../CardPromptPopover'
import { useViewerId } from '../useViewerId'
import { useCardActions } from './cardActions'
import { AgentLine, AssigneePicker, CardFacts, DueMark, owedDateOf, PriorityMark } from './CardFace'
import { PrMark, StageMark, SubtaskMark } from './CardMarks'
import { PrList, StageList, SubtaskList } from './CardSlotLists'
import { useCardFocus } from './useCardFocus'
import styles from './TaskCard.module.css'
import layout from './CardLayout.module.css'

const TYPE_LABELS: Record<Task['type'], string> = {
  feature: 'Feature',
  bug: 'Bug',
  chore: 'Chore',
}

/** How a session on a card reads to the person looking at it, or null for none. */
function useAgentIndicator(session: AgentPresence | null): AgentIndicator | null {
  const viewerId = useViewerId()
  return agentIndicator(session, new Date(), viewerId)
}

/**
 * The classes every drawing of a card wears: what it is, and how it stands.
 *
 * Filtered rather than joined as they come: an active task has no status
 * class, and a template string would put the word "undefined" in the class
 * list instead of nothing.
 */
function cardClassName(task: Task, ...extra: (string | false | null | undefined)[]): string {
  return [styles.task, styles[`type_${task.type}`], styles[task.status], ...extra]
    .filter(Boolean)
    .join(' ')
}

/**
 * The rail's colour, when the card is on a goal. Read by `.task::before`
 * through a fallback, so a card on no goal keeps the status colour the rail
 * has always been — see TaskCard.module.css.
 */
function railStyle(task: Task): CSSProperties | undefined {
  return task.goal_colour ? ({ '--card-rail': task.goal_colour } as CSSProperties) : undefined
}

export function TaskCard({ task }: { task: Task }) {
  const { members, openTask, moveSubStatus, setAssignee } = useCardActions()
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({ id: task.id })
  const agent = useAgentIndicator(task.agent_session)
  // A question or permission waiting on this card opens a popover under it on
  // hover (CYLIST-86) — never while anything on the board is being dragged.
  const prompt = task.agent_session?.prompt ?? null
  const answerable = isAnswerable(prompt, new Date())
  const { active } = useDndContext()
  const popover = useCardPopover({ answerable, dragging: active !== null })
  const setCardRef = useCallback(
    (element: HTMLElement | null) => {
      setNodeRef(element)
      popover.cardRef.current = element
    },
    [setNodeRef, popover.cardRef],
  )

  const open = () => openTask(task.id)

  return (
    <>
      <article
        ref={setCardRef}
        {...attributes}
        {...listeners}
        {...popover.card}
        onClick={open}
        onKeyDown={(event) => {
          // Enter belongs to the card, every other key to the drag sensor —
          // whose own onKeyDown this handler has just replaced, so it has to be
          // called on rather than dropped. Not while the card is in the air,
          // though: opening a dialog over a drag in progress strands the drag.
          if (event.key === 'Enter' && !isDragging) {
            event.preventDefault()
            open()
            return
          }
          listeners?.onKeyDown?.(event)
        }}
        aria-label={[
          // The type leads, and is said at all, because the rail that carries
          // it cannot be: a colour down the edge of a card is the one thing on
          // it a screen reader has no way to reach. With the tile gone this is
          // the only place on the card the word appears — the goal below it has
          // the same problem and the same answer.
          `${TYPE_LABELS[task.type]} ${task.reference}: ${task.title}`,
          task.parent_reference ? `a sub-task of ${task.parent_reference}` : null,
          task.goal_name ? `on ${task.goal_name}` : null,
          // The border is colour and motion alone, so the words go here too.
          agent ? [agent.label, agent.detail].filter(Boolean).join(', ') : null,
          // Said, because the popover that opens on focus is otherwise news.
          answerable ? 'its choices follow the card' : null,
        ]
          .filter(Boolean)
          .join(', ')}
        style={railStyle(task)}
        // No transform of its own: while this card is in the air the DragOverlay
        // is the copy that follows the pointer, and this one stays put and dims.
        className={cardClassName(
          task,
          // The agent channel is the outer border, which nothing else on the
          // card uses — the rail is already contested three ways.
          agent && styles[agent.className],
          isDragging && styles.dragging,
        )}
      >
        <TaskCardBody
          task={task}
          members={members}
          agent={agent}
          onMoveSubStatus={(index) => moveSubStatus(task.id, index)}
          onSetAssignee={(assigneeId) => setAssignee(task.id, task.reference, assigneeId)}
          onOpenTask={openTask}
        />
      </article>
      {popover.open && answerable ? (
        <CardPromptPopover
          taskRef={task.reference}
          prompt={prompt}
          cardRef={popover.cardRef}
          popoverRef={popover.popoverRef}
          handlers={popover.popover}
          onAnswered={popover.onAnswered}
        />
      ) : null}
    </>
  )
}

/** The copy of a card that follows the pointer while it is being dragged. */
export function LiftedTaskCard({ task }: { task: Task }) {
  const { members } = useCardActions()
  const agent = useAgentIndicator(task.agent_session)

  return (
    <article className={cardClassName(task, styles.lifted)} style={railStyle(task)}>
      <TaskCardBody task={task} members={members} agent={agent} />
    </article>
  )
}

/**
 * What a card says, without any of what a card does.
 *
 * Drawn twice while a card is being dragged — dimmed in the column it came
 * from, and solid in the overlay — so it lives apart from the drag wiring that
 * only the one in the column has. The callbacks are optional because the
 * overlay's copy has nothing to click: it is a picture of a card moving.
 *
 * The face is what every card in a column is compared on, and nothing else:
 * the title and its priority, how far through its stages and sub-tasks it is,
 * and one line of which card it is, when it is owed, where it is tracked and
 * whose it is. Under that is one slot, and only one: the card's other facts
 * after a short rest on the card, or — after a short rest on a mark — the
 * stages, the sub-tasks or the pull requests. Nothing that opens ever takes
 * the place of a row, so nothing the pointer is aiming at moves. The goal is
 * the rail down the card's left edge, named when the rail is pointed at.
 */
function TaskCardBody({
  task,
  members,
  agent,
  onMoveSubStatus,
  onSetAssignee,
  onOpenTask,
}: {
  task: Task
  members: Person[]
  agent: AgentIndicator | null
  onMoveSubStatus?: (index: number) => void
  onSetAssignee?: (assigneeId: string) => void
  /** Opens a sub-task's own dialog. */
  onOpenTask?: (taskId: string) => void
}) {
  const { owed, stage } = owedDateOf(task)
  const { focus, arming, opened, mark, slot, letGo } = useCardFocus()
  const staged = task.sub_statuses.length > 0
  // While an agent is on the card, the line saying what it is doing takes the
  // place of the stages: that is the progress worth reading right then, and
  // the two together were a card made of status lines.
  const progress = !agent && (staged || task.subtask_count > 0)

  return (
    <div
      className={[styles.cardBody, focus && styles[`focus_${focus}`]].filter(Boolean).join(' ')}
      // Leaving the card lets go too, not only leaving a mark or the slot: a
      // row that re-renders under the pointer — a tick that just landed — can
      // swallow the leave its own list was waiting for.
      onPointerLeave={letGo}
    >
      {/* Named on the rail rather than written on the card. The rail is the
          goal already, and one card that says so teaches the column. */}
      {task.goal_name && !agent ? (
        <span className={layout.goalHit} aria-hidden="true">
          <span className={layout.goalTab}>{task.goal_name}</span>
        </span>
      ) : null}

      <div className={layout.headLine}>
        <div className={styles.title}>{task.title}</div>
        <PriorityMark priority={task.priority} />
      </div>

      {agent ? <AgentLine agent={agent} sessionCount={task.agent_session?.count ?? 0} /> : null}

      {progress ? (
        <div className={layout.progress}>
          {staged ? (
            <StageMark
              task={task}
              onMove={onMoveSubStatus}
              open={focus === 'stages'}
              arming={arming === 'stages'}
              handlers={mark('stages')}
            />
          ) : null}
          {task.subtask_count ? (
            <SubtaskMark
              task={task}
              open={focus === 'subs'}
              arming={arming === 'subs'}
              handlers={mark('subs')}
            />
          ) : null}
        </div>
      ) : null}

      <div className={layout.faceLine}>
        <span className={layout.reference}>{task.reference}</span>
        <DueMark iso={owed} column={stage} />
        {task.jira_ref ? <TaskRef kind="jira" value={task.jira_ref} compact /> : null}
        {task.pr_refs.length ? (
          <PrMark
            refs={task.pr_refs}
            open={focus === 'prs'}
            arming={arming === 'prs'}
            handlers={mark('prs')}
          />
        ) : null}
        <span className={layout.faceGap} />
        {onSetAssignee ? (
          <AssigneePicker task={task} members={members} onSetAssignee={onSetAssignee} />
        ) : (
          <Avatar name={task.assignee.name} colour={task.assignee.colour} small />
        )}
      </div>

      {/* The one slot. Opened by a short rest on the card rather than the
          moment the pointer crosses it — a column you are only moving across
          should not ripple — or at once by a mark that has opened. Every list
          shares the one cell, in the order of the marks, so switching from
          one to another slides the way the pointer moved. */}
      <div className={styles.more} {...slot}>
        <div className={layout.slotStack}>
          <SlotPane className={styles.factsPane}>
            <CardFacts task={task} />
          </SlotPane>
          {staged && progress ? (
            <SlotPane className={styles.stagesPane}>
              <StageList task={task} members={members} onMove={onMoveSubStatus} />
            </SlotPane>
          ) : null}
          {task.subtask_count && progress && opened.has('subs') ? (
            <SlotPane className={styles.subsPane}>
              <SubtaskList task={task} onOpenTask={onOpenTask} />
            </SlotPane>
          ) : null}
          {task.pr_refs.length ? (
            <SlotPane className={styles.prsPane}>
              <PrList refs={task.pr_refs} />
            </SlotPane>
          ) : null}
        </div>
      </div>
    </div>
  )
}

/** One of the lists that take turns in the card's slot. */
function SlotPane({ className, children }: { className: string | undefined; children: ReactNode }) {
  return (
    <div className={`${layout.slotPane} ${className}`}>
      <div className={layout.paneInner}>{children}</div>
    </div>
  )
}
