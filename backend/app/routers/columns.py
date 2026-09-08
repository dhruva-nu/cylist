"""Board columns.

A board is created with two columns and can grow to eight. Both limits are
returned with every listing so a client can grey out its "add a column" tile
without hard-coding the numbers.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.db import SessionDependency
from app.models.board import BoardColumn
from app.models.project import Project
from app.routers.projects import resolved_project
from app.schemas.columns import Board, ColumnCreate, ColumnOrder, ColumnRead, ColumnUpdate
from app.schemas.common import Acknowledged
from app.services import activity, columns

router = APIRouter(tags=["board"])


async def resolved_column(
    column_id: UUID,
    session: AsyncSession = SessionDependency,
) -> BoardColumn:
    """Turn the path segment into a column, 404-ing if nothing matches."""
    return await columns.get(session, column_id)


def _read(column: BoardColumn, task_count: int) -> ColumnRead:
    return ColumnRead(
        id=column.id,
        project_id=column.project_id,
        name=column.name,
        description=column.description,
        position=column.position,
        outcomes=list(column.outcomes),
        task_count=task_count,
    )


async def _board(session: AsyncSession, project: Project) -> Board:
    found = await columns.list_for_project(session, project)
    counts = await columns.task_counts(session, project.id)
    return Board(columns=[_read(column, counts.get(column.id, 0)) for column in found])


@router.get(
    "/projects/{project_ref}/columns",
    response_model=Board,
    summary="List a board's columns",
)
async def list_columns(
    project: Project = Depends(resolved_project),
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
) -> Board:
    """The board, left to right, with how many cards each column holds."""
    return await _board(session, project)


@router.post(
    "/projects/{project_ref}/columns",
    response_model=ColumnRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a column",
    responses={409: {"description": "The board already has eight columns."}},
)
async def create_column(
    body: ColumnCreate,
    project: Project = Depends(resolved_project),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> ColumnRead:
    """Add a column to the right of the existing ones.

    The description is required: it is what stops two columns quietly coming to
    mean the same thing.
    """
    column = await columns.create(session, project, body)
    await activity.record(
        session,
        principal,
        "column.created",
        entity_type="column",
        entity_id=column.id,
        project_id=project.id,
        payload={"name": column.name, "position": column.position},
    )
    return _read(column, 0)


@router.put(
    "/projects/{project_ref}/columns/order",
    response_model=Board,
    summary="Reorder a board",
    responses={422: {"description": "The ids are not exactly this board's columns."}},
)
async def reorder_columns(
    body: ColumnOrder,
    project: Project = Depends(resolved_project),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> Board:
    """Set the left-to-right order of the whole board.

    Every column must be named exactly once. Moving the first column moves
    where new tasks land, which is why this is one deliberate call rather than
    a position field on each column.
    """
    await columns.reorder(session, project, body.column_ids)
    await activity.record(
        session,
        principal,
        "column.reordered",
        entity_type="column",
        project_id=project.id,
        payload={"column_ids": [str(column_id) for column_id in body.column_ids]},
    )
    return await _board(session, project)


@router.patch("/columns/{column_id}", response_model=ColumnRead, summary="Rename a column")
async def update_column(
    body: ColumnUpdate,
    column: BoardColumn = Depends(resolved_column),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> ColumnRead:
    """Change a column's name or description. Omitted fields are left alone."""
    updated = await columns.update(session, column, body)
    await activity.record(
        session,
        principal,
        "column.updated",
        entity_type="column",
        entity_id=updated.id,
        project_id=updated.project_id,
        payload={"fields": sorted(body.model_dump(exclude_unset=True))},
    )
    counts = await columns.task_counts(session, updated.project_id)
    return _read(updated, counts.get(updated.id, 0))


@router.delete(
    "/columns/{column_id}",
    response_model=Acknowledged,
    summary="Delete a column",
    responses={409: {"description": "It still holds tasks, or is one of the last two."}},
)
async def delete_column(
    column: BoardColumn = Depends(resolved_column),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> Acknowledged:
    """Delete an empty column.

    Tasks are never deleted as a side effect of tidying a board: move them out
    first. A board also keeps at least two columns, since nothing can move
    across one.
    """
    project_id, name = column.project_id, column.name
    await columns.delete(session, column)
    await activity.record(
        session,
        principal,
        "column.deleted",
        entity_type="column",
        project_id=project_id,
        payload={"name": name},
    )
    return Acknowledged()
