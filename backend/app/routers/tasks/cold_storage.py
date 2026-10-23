"""Cold storage: taking a board's finished cards off it, and putting one back."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.card_deps import WRITE_CARDS, WRITE_THIS_CARD, resolved_task
from app.api.deps import resolved_project
from app.api.paging import PageQuery
from app.api.presenters.files import file_item_read
from app.api.presenters.tasks import read_tasks, task_detail
from app.auth.dependencies import require
from app.auth.permissions import Permission
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.config import Settings, app_settings
from app.db import SessionDependency
from app.models.activity import Verb
from app.models.project import Project
from app.models.task import Task
from app.schemas.tasks import ColdStorageRun, TaskDetail, TaskSummaryPage
from app.services import columns, permissions, tasks
from app.services.paging import PageRequest
from app.services.tasks.recording import record_on_card
from app.storage import BlobStore, get_blob_store

router = APIRouter(tags=["tasks"])


async def may_store_cards(
    project: Project = Depends(resolved_project),
    principal: Principal = Depends(WRITE_CARDS),
    session: AsyncSession = SessionDependency,
) -> Principal:
    """Admit a caller who may both move cards and write files.

    Storing does both: the cards leave the board, and a CSV of them lands in
    the project's files. A role kept out of either is kept out of this, rather
    than getting the half of it they are allowed.
    """
    return await permissions.enforce(session, project, principal, Permission.FILES)


async def may_restore_this_card(
    task: Task = Depends(resolved_task),
    principal: Principal = Depends(WRITE_THIS_CARD),
    session: AsyncSession = SessionDependency,
) -> Principal:
    """Admit a caller whose role may put a card into the board's last column.

    Restoring is arriving there, so it answers to the same column rule a move
    into it does: a role kept out of Done cannot restore a card into it either.
    """
    finished = await columns.last(session, task.project_id)
    await permissions.enforce_column_entry(session, task.project, principal, finished.id)
    return principal


@router.get(
    "/projects/{project_ref}/cold-storage",
    response_model=TaskSummaryPage,
    summary="List the cards in cold storage",
)
async def list_cold_storage(
    project: Project = Depends(resolved_project),
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
    page: PageRequest = PageQuery,
) -> TaskSummaryPage:
    """The cards this board has put in cold storage, most recently finished
    first, a page at a time.

    By `finished_at` rather than `archived_at`: one run stores a whole column
    at the same instant, so when each card's work finished is the order that
    says something.

    These are the cards `GET /projects/{ref}/tasks` no longer lists. Each still
    opens by its reference, with its whole timeline, and carries `archived_at`.
    """
    window = await tasks.stored_page(session, project, page)
    return TaskSummaryPage(items=await read_tasks(session, window.rows), next=window.next)


@router.post(
    "/projects/{project_ref}/cold-storage",
    response_model=ColdStorageRun,
    summary="Put the finished cards in cold storage",
    responses={422: {"description": "The board's last column is empty."}},
)
async def store_finished_cards(
    project: Project = Depends(resolved_project),
    principal: Principal = Depends(may_store_cards),
    session: AsyncSession = SessionDependency,
    store: BlobStore = Depends(get_blob_store),
    settings: Settings = Depends(app_settings),
) -> ColdStorageRun:
    """Take every card in the board's last column off the board, into a CSV.

    The CSV — one row per card, named for the project and the moment — is filed
    in a `Cold storage` folder at the top of the project's files, made the
    first time it is needed. The cards are not deleted: each keeps its
    reference, its history and how it ended, still counts towards its goal, and
    can be put back with `POST /tasks/{ref}/restore`.

    Needs both `tasks` and `files` on this board, since it does something to
    each.
    """
    stored = await tasks.store_finished(
        session,
        store,
        project,
        added_by=principal.person_id,
        max_bytes=settings.max_upload_bytes,
    )
    for card in stored.cards:
        await record_on_card(
            session,
            principal,
            Verb.TASK_ARCHIVED,
            card,
            {"column": stored.column.name, "file": stored.file.name},
        )
    return ColdStorageRun(
        column=stored.column.name,
        references=[card.reference for card in stored.cards],
        file=file_item_read(stored.file),
    )


@router.post(
    "/tasks/{task_ref}/restore",
    response_model=TaskDetail,
    summary="Restore a card from cold storage",
    responses={
        422: {
            "description": (
                "The card is not in cold storage, its template keeps it out of the board's "
                "last column, or something under it is still open."
            )
        }
    },
)
async def restore_card(
    task: Task = Depends(resolved_task),
    principal: Principal = Depends(may_restore_this_card),
    session: AsyncSession = SessionDependency,
) -> TaskDetail:
    """Put a stored card back at the top of the board's last column.

    It comes back finished, and on the outcome it ended on where that column
    still has it. The CSV it was written to is left alone: it is the record of
    what was stored that day, and that stays true.
    """
    restored, changes = await tasks.restore(session, task)
    await record_on_card(
        session,
        principal,
        Verb.TASK_RESTORED,
        restored,
        {
            "column": restored.column.name if restored.column else None,
            "changes": changes,
        },
    )
    return await task_detail(session, restored)
