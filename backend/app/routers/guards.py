"""The dependency that turns a permission into a refusal.

Every write endpoint a role can fence depends on one of these instead of on
``require(Scope.WRITE)`` directly. Both checks still happen — the scope one is
inside — because they answer different questions: a scope is what a credential
may do anywhere, a permission is what this person is on this board.

Two shapes, because Cylist's paths come in two shapes. Most project-scoped
endpoints carry ``{project_ref}`` and can use :func:`on_project`. The rest are
addressed by the thing itself — ``/tasks/ATL-41``, ``/folders/{id}`` — and
their routers build their own guard out of :func:`for_entity`, resolving the
row on the way in and taking the project off it.

Each guard returns the :class:`~app.auth.principal.Principal`, so swapping one
in is a one-line change at the call site and the handler below it is untouched.

**Three kinds of write are deliberately not fenced by a role.** Creating a
project, and editing the people directory, because neither is *on* a project —
a directory entry belongs to every board the person works on, so no single
board's role can answer for it. And an agent session's own state, which is a
harness saying which card it is working on: it is bookkeeping about the caller
rather than a change to the project, and a role that could not report itself
would be a role whose holder's agents vanish off the board.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require
from app.auth.permissions import Permission
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.db import SessionDependency
from app.models.project import Project
from app.services import permissions

Guard = Callable[..., Awaitable[Principal]]


def on_project(permission: Permission, *scopes: Scope) -> Guard:
    """A guard for an endpoint whose path names the project.

    Args:
        permission: What the caller's role has to allow.
        scopes: What the credential has to carry. ``write`` unless given —
            the vault asks for more, and says so at its own call sites.
    """
    # Imported here rather than at the top because `app.routers.projects`
    # owns the resolver and wants a guard of its own: at module scope the two
    # would be a cycle, and this is called after both modules have their
    # definitions.
    from app.routers.projects import resolved_project

    required = scopes or (Scope.WRITE,)

    async def dependency(
        project: Project = Depends(resolved_project),
        principal: Principal = Depends(require(*required)),
        session: AsyncSession = SessionDependency,
    ) -> Principal:
        return await permissions.enforce(session, project, principal, permission)

    return dependency


async def _own_project_id(session: AsyncSession, entity: Any) -> UUID:
    """The default way to find the project: the row says so itself."""
    project_id = getattr(entity, "project_id", None)
    if not isinstance(project_id, UUID):  # pragma: no cover - a wiring mistake, not a state
        raise TypeError(f"{type(entity).__name__} has no project_id to check a permission on")
    return project_id


def for_entity(
    permission: Permission,
    resolver: Callable[..., Awaitable[Any]],
    *scopes: Scope,
    locate: Callable[[AsyncSession, Any], Awaitable[UUID]] = _own_project_id,
) -> Guard:
    """A guard for an endpoint addressed by a row rather than by a project.

    The resolver is the router's own — ``resolved_task``, ``resolved_folder``
    — so FastAPI's per-request dependency cache hands the handler below the
    same object this looked at, and nothing is fetched twice.

    Args:
        resolver: A dependency yielding the row the endpoint is about.
        locate: How to get from that row to its project. The default reads
            ``project_id`` off it, which most rows carry. The two that do not
            — a file item, which knows its folder, and a vault node, which
            knows its tree — pass their own hop.
    """
    required = scopes or (Scope.WRITE,)

    async def dependency(
        entity: Any = Depends(resolver),
        principal: Principal = Depends(require(*required)),
        session: AsyncSession = SessionDependency,
    ) -> Principal:
        project_id = await locate(session, entity)
        return await permissions.enforce_on(session, project_id, principal, permission)

    return dependency
