"""How a card, its timeline and its tick boxes are drawn in a response.

A card should read identically wherever it is listed — on its board, on a
goal's page, under its parent — which it only does if one function is drawing
it. :func:`read_tasks` is that function, and the goals router borrows it.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.board import BoardColumn
from app.models.person import Person
from app.models.task import Task, TaskChecklistItem, TaskComment
from app.schemas.agent_sessions import AgentPresence
from app.schemas.people import PersonRead
from app.schemas.tasks import (
    ChecklistItemRead,
    ColumnDueDateRead,
    CommentRead,
    PrRef,
    TaskDetail,
    TaskRead,
)
from app.services import agent_prompts, agent_sessions, columns, tasks


def comment_read(entry: TaskComment) -> CommentRead:
    return CommentRead(
        id=entry.id,
        task_id=entry.task_id,
        author=PersonRead.model_validate(entry.author) if entry.author else None,
        body=entry.body,
        kind=entry.kind,
        meta=entry.meta,
        created_at=entry.created_at,
    )


def checklist_item_read(item: TaskChecklistItem) -> ChecklistItemRead:
    return ChecklistItemRead(
        id=item.id,
        task_id=item.task_id,
        title=item.title,
        state=item.state,
        position=item.position,
        created_at=item.created_at,
    )


def _task_read(
    task: Task,
    *,
    comment_count: int,
    split: tasks.Split,
    owners: list[Person],
    board: list[BoardColumn],
    presence: AgentPresence | None,
) -> TaskRead:
    dates = tasks.board_dates(task, board)
    return TaskRead(
        id=task.id,
        project_id=task.project_id,
        reference=task.reference,
        number=task.number,
        parent_id=task.parent_id,
        parent_reference=task.parent.reference if task.parent else None,
        sub_number=task.sub_number,
        column_id=task.column_id,
        position=task.position,
        title=task.title,
        description=task.description,
        type=task.type,
        priority=task.priority,
        sub_statuses=task.sub_statuses,
        sub_status_index=task.sub_status_index,
        due_date=task.due_date,
        column_due_dates=[
            ColumnDueDateRead(
                column_id=entry.column_id,
                column_name=entry.column_name,
                due_date=entry.due_date,
                met=entry.met,
            )
            for entry in dates.per_column
        ],
        next_due_date=dates.next_due,
        assignee=PersonRead.model_validate(task.assignee),
        status=task.status,
        template_id=task.template_id,
        template_name=task.template.name if task.template else None,
        goal_id=task.goal_id,
        goal_reference=task.goal.reference if task.goal else None,
        goal_name=task.goal.name if task.goal else None,
        goal_colour=task.goal.colour if task.goal else None,
        jira_ref=task.jira_ref,
        pr_refs=[PrRef.model_validate(ref) for ref in task.pr_refs],
        waiting_on=[PersonRead.model_validate(person) for person in task.waiting_on],
        comment_count=comment_count,
        checklist=[checklist_item_read(item) for item in task.checklist],
        outcome=task.outcome,
        outcome_index=task.outcome_index,
        finished_at=task.finished_at,
        archived_at=task.archived_at,
        open_subtask_count=split.open,
        subtask_count=split.total,
        subtask_assignees=[PersonRead.model_validate(person) for person in owners],
        agent_session=presence,
        created_at=task.created_at,
    )


async def read_tasks(session: AsyncSession, found: list[Task]) -> list[TaskRead]:
    """Render a set of cards, with the counts a card cannot answer alone.

    Comment counts, sub-task progress, sub-task owners and the boards the cards
    sit on each take one query for the whole set rather than one per card.
    Public because the goals router draws the same cards on a goal's page: a
    card should read identically wherever it is listed, which it only does if
    one function is drawing it.
    """
    ids = [task.id for task in found]
    comment_counts = await tasks.comment_counts(session, ids)
    splits = await tasks.subtask_counts(session, ids)
    owners = await tasks.subtask_owners(session, ids)
    boards = await columns.boards_by_project(session, {task.project_id for task in found})
    # One query for the agents on every card, and one for what they are asking.
    agents = await agent_sessions.for_tasks(session, ids)
    shown = await agent_prompts.shown_for(
        session, [row for rows in agents.values() for row in rows]
    )
    return [
        _task_read(
            task,
            comment_count=comment_counts.get(task.id, 0),
            split=splits.get(task.id, tasks.NOT_SPLIT),
            owners=owners.get(task.id, []),
            board=boards.get(task.project_id, []),
            presence=agent_sessions.presence(agents.get(task.id, []), shown),
        )
        for task in found
    ]


async def task_detail(session: AsyncSession, task: Task) -> TaskDetail:
    """One card in full: itself, its timeline, its agents and its sub-tasks."""
    timeline = await tasks.comments(session, task)
    splits = await tasks.subtask_counts(session, [task.id])
    owners = await tasks.subtask_owners(session, [task.id])
    board = await columns.list_for_project(session, task.project)
    agents = await agent_sessions.list_for_task(session, task)
    shown = await agent_prompts.shown_for(session, agents)
    card = _task_read(
        task,
        comment_count=len(timeline),
        split=splits.get(task.id, tasks.NOT_SPLIT),
        owners=owners.get(task.id, []),
        board=board,
        presence=agent_sessions.presence(agents, shown),
    )
    return TaskDetail(
        **card.model_dump(),
        comments=[comment_read(entry) for entry in timeline],
        agent_sessions=[agent_sessions.read(row, shown.get(row.id)) for row in agents],
        subtasks=await read_tasks(session, await tasks.subtasks(session, task)),
    )
