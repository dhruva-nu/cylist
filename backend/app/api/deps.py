"""The dependencies more than one router declares.

Routers import their shared resolvers and guards from here and never from one
another: a router that has to borrow another's ``resolved_project`` is a router
that cannot be read, tested or moved without it. Each resolver turns a path
segment into a row *and* applies the fence — a project, role or column the
caller cannot see is a 404 — so none of the routes below them repeats it.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import guards
from app.auth.dependencies import current_principal
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.config import Settings, app_settings
from app.core import crypto
from app.core.crypto import VaultCipher
from app.core.errors import NotFoundError
from app.db import SessionDependency
from app.models.board import BoardColumn
from app.models.project import Project
from app.models.role import ProjectRole
from app.services import columns, projects, roles

ProjectRef = Path(
    description="The project's id, or its key such as `ATL` (case-insensitive).",
    examples=["ATL"],
)


async def resolved_project(
    project_ref: str = ProjectRef,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = SessionDependency,
) -> Project:
    """Turn the path segment into a project, 404-ing if nothing matches.

    A project the caller is not on 404s too, so every route under
    ``/projects/{project_ref}`` is fenced by membership through this one
    dependency — see :func:`app.services.projects.require_reachable`.
    """
    return await projects.resolve_reachable(session, project_ref, principal)


async def resolved_column(
    column_id: UUID,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = SessionDependency,
) -> BoardColumn:
    """Turn the path segment into a column, 404-ing if nothing matches — or if it is
    on a project the caller is not a member of."""
    column = await columns.get(session, column_id)
    await projects.require_reachable(
        session, column.project_id, principal, NotFoundError("No column with that id.")
    )
    return column


RoleRef = Path(
    description="The role's id, or its name such as `Reviewer` (case-insensitive).",
    examples=["Reviewer"],
)


async def resolved_role(
    role_ref: str = RoleRef,
    project: Project = Depends(resolved_project),
    session: AsyncSession = SessionDependency,
) -> ProjectRole:
    """Turn the path segment into one of this project's roles, or 404."""
    return await roles.resolve(session, project, role_ref)


project_admin = guards.project_admin_only("manage its roles", Scope.WRITE)
"""Admit only a caller who may manage this project's roles.

Two guards in one, and they answer different questions. ``Scope.WRITE`` asks
what the *credential* may do, so a read-only token is refused here as it is
everywhere. The admin check asks who the *person* behind it is on this board —
which is the question the ticket asks, and the one scopes have never been able
to answer, since they say nothing about which project is in front of them.

A credential acts as whoever minted it, so an agent token owned by an admin
passes: that is the admin working through a program, and it is what
``api_token.person_id`` has meant since CYLIST-44. Keeping an agent out while
letting its owner in is a job for a narrower scope on the token, not for this.
"""


def vault_cipher(settings: Settings = Depends(app_settings)) -> VaultCipher:
    """The cipher this deployment's vault keys configure.

    Built on demand rather than at startup, so a deployment that has not set a
    key still boots and still serves everything outside the vault — and fails
    loudly, here, the moment a credential is involved.
    """
    return crypto.cipher_for(settings.vault_keyring)
