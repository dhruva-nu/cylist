"""Hooks: a project's rules for POSTing its board's changes to a URL.

Everything here is the project admin's, reading included: a hook's URL is
often a capability in itself — a CI trigger, a chat webhook — and its delivery
log carries every event the board sent out. See :mod:`app.services.hooks` for
what fires and :mod:`app.services.hook_delivery` for how it is sent.
"""

from __future__ import annotations

from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.core.crypto import VaultCipher
from app.core.errors import ForbiddenError
from app.db import SessionDependency, publish_into
from app.models.board import BoardColumn
from app.models.hook import Hook, HookDelivery
from app.models.project import Project
from app.models.template import TaskTemplate
from app.routers.guards import Guard
from app.routers.projects import resolved_project
from app.routers.vault import vault_cipher
from app.schemas.hooks import (
    DeliverySummary,
    HookCreate,
    HookCreated,
    HookDeliveryRead,
    HookEvent,
    HookRead,
    HookSecret,
    HookSecretRotate,
    HookUpdate,
)
from app.services import activity, hook_delivery, hooks, roles
from app.services.hooks import HookFields, HookFilters

router = APIRouter(tags=["hooks"])

_FILTERS = ("to_column_id", "from_column_id", "template_id", "task_type")


def _admin_of_hooks(*scopes: Scope) -> Guard:
    async def dependency(
        project: Project = Depends(resolved_project),
        principal: Principal = Depends(require(*scopes)),
        session: AsyncSession = SessionDependency,
    ) -> Principal:
        """The project's admin, and nobody else — the same test roles use."""
        if not await roles.may_administer(session, project, principal):
            raise ForbiddenError(
                f"Only an admin of {project.key} can see or change its hooks.",
                details={"project_key": project.key},
            )
        return principal

    return dependency


may_read_hooks = _admin_of_hooks(Scope.READ)
may_change_hooks = _admin_of_hooks(Scope.WRITE)


async def resolved_hook(
    hook_id: UUID,
    project: Project = Depends(resolved_project),
    session: AsyncSession = SessionDependency,
) -> Hook:
    return await hooks.get(session, project, hook_id)


async def _read(session: AsyncSession, hook: Hook) -> HookRead:
    async def column_name(column_id: UUID | None) -> str | None:
        column = await session.get(BoardColumn, column_id) if column_id else None
        return column.name if column is not None else None

    template = await session.get(TaskTemplate, hook.template_id) if hook.template_id else None
    last = await session.scalar(
        select(HookDelivery)
        .where(HookDelivery.hook_id == hook.id)
        .order_by(HookDelivery.created_at.desc(), HookDelivery.id.desc())
        .limit(1)
    )
    return HookRead(
        id=hook.id,
        name=hook.name,
        enabled=hook.enabled,
        url=hook.url,
        verbs=list(hook.verbs),
        to_column_id=hook.to_column_id,
        to_column_name=await column_name(hook.to_column_id),
        from_column_id=hook.from_column_id,
        from_column_name=await column_name(hook.from_column_id),
        template_id=hook.template_id,
        template_name=template.name if template is not None else None,
        task_type=hook.task_type,
        secret_hint=hook.secret_hint,
        last_delivery=DeliverySummary.model_validate(last) if last is not None else None,
        created_at=hook.created_at,
        updated_at=hook.updated_at,
    )


def _filters_of(body: HookCreate | HookUpdate) -> HookFilters:
    return HookFilters(
        given=frozenset(field for field in _FILTERS if field in body.model_fields_set),
        to_column_id=body.to_column_id,
        from_column_id=body.from_column_id,
        template_id=body.template_id,
        task_type=body.task_type,
    )


def _client(request: Request) -> httpx.AsyncClient:
    """A client for one test send. Its transport is the app's to choose, so a
    test can answer in place of a receiver."""
    return httpx.AsyncClient(transport=request.app.state.hook_transport)


@router.get(
    "/hooks/events",
    response_model=list[HookEvent],
    summary="List the events a hook can fire on",
)
async def list_events(_: Principal = Depends(require(Scope.READ))) -> list[HookEvent]:
    """Every verb a hook may name, in the order the Hooks page lists them."""
    return [HookEvent(verb=verb, label=label) for verb, label in hooks.EVENTS.items()]


@router.get(
    "/projects/{project_ref}/hooks",
    response_model=list[HookRead],
    summary="List a project's hooks",
)
async def list_hooks(
    project: Project = Depends(resolved_project),
    _: Principal = Depends(may_read_hooks),
    session: AsyncSession = SessionDependency,
) -> list[HookRead]:
    return [await _read(session, hook) for hook in await hooks.list_hooks(session, project)]


@router.post(
    "/projects/{project_ref}/hooks",
    response_model=HookCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Add a hook",
    responses={
        409: {"description": "The project already has a hook by that name."},
        422: {"description": "An unknown event, a column or template from elsewhere, a bad URL."},
    },
)
async def create_hook(
    body: HookCreate,
    project: Project = Depends(resolved_project),
    principal: Principal = Depends(may_change_hooks),
    cipher: VaultCipher = Depends(vault_cipher),
    session: AsyncSession = SessionDependency,
) -> HookCreated:
    """Add a rule that POSTs matching changes to a URL.

    The response carries the signing secret, which is not shown again — keep
    it where the receiver can check signatures with it, or rotate it later.
    """
    hook, secret = await hooks.create(
        session,
        project,
        cipher,
        name=body.name,
        url=body.url,
        enabled=body.enabled,
        verbs=body.verbs,
        filters=_filters_of(body),
        secret=body.secret,
    )
    await activity.record(
        session,
        principal,
        "hook.created",
        entity_type="hook",
        entity_id=hook.id,
        project_id=project.id,
        payload={"name": hook.name},
    )
    await session.flush()
    await session.refresh(hook)
    return HookCreated(**(await _read(session, hook)).model_dump(), secret=secret)


@router.get(
    "/projects/{project_ref}/hooks/{hook_id}",
    response_model=HookRead,
    summary="Get a hook",
)
async def get_hook(
    hook: Hook = Depends(resolved_hook),
    _: Principal = Depends(may_read_hooks),
    session: AsyncSession = SessionDependency,
) -> HookRead:
    return await _read(session, hook)


@router.patch(
    "/projects/{project_ref}/hooks/{hook_id}",
    response_model=HookRead,
    summary="Change a hook",
)
async def update_hook(
    body: HookUpdate,
    project: Project = Depends(resolved_project),
    hook: Hook = Depends(resolved_hook),
    principal: Principal = Depends(may_change_hooks),
    session: AsyncSession = SessionDependency,
) -> HookRead:
    """Change any of a hook's fields. A filter sent as `null` is cleared."""
    changed = await hooks.update(
        session,
        project,
        hook,
        HookFields(name=body.name, enabled=body.enabled, verbs=body.verbs, url=body.url),
        _filters_of(body),
    )
    if changed:
        await activity.record(
            session,
            principal,
            "hook.updated",
            entity_type="hook",
            entity_id=hook.id,
            project_id=project.id,
            payload={"name": hook.name, "fields": changed},
        )
        await session.flush()
        await session.refresh(hook)
    return await _read(session, hook)


@router.delete(
    "/projects/{project_ref}/hooks/{hook_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a hook",
)
async def delete_hook(
    project: Project = Depends(resolved_project),
    hook: Hook = Depends(resolved_hook),
    principal: Principal = Depends(may_change_hooks),
    session: AsyncSession = SessionDependency,
) -> Response:
    """Delete a hook and its delivery log. Anything still queued is not sent."""
    name = hook.name
    await hooks.delete(session, hook)
    await activity.record(
        session,
        principal,
        "hook.deleted",
        entity_type="hook",
        project_id=project.id,
        payload={"name": name},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/projects/{project_ref}/hooks/{hook_id}/secret",
    response_model=HookSecret,
    summary="Rotate a hook's signing secret",
)
async def rotate_secret(
    body: HookSecretRotate,
    project: Project = Depends(resolved_project),
    hook: Hook = Depends(resolved_hook),
    principal: Principal = Depends(may_change_hooks),
    cipher: VaultCipher = Depends(vault_cipher),
    session: AsyncSession = SessionDependency,
) -> HookSecret:
    """Give the hook a new signing secret and show it, once. Everything sent from
    now on — retries included — is signed with the new one."""
    secret = hooks.rotate_secret(cipher, hook, body.secret)
    await activity.record(
        session,
        principal,
        "hook.secret_rotated",
        entity_type="hook",
        entity_id=hook.id,
        project_id=project.id,
        payload={"name": hook.name},
    )
    return HookSecret(secret=secret, secret_hint=hook.secret_hint)


@router.post(
    "/projects/{project_ref}/hooks/{hook_id}/test",
    response_model=HookDeliveryRead,
    summary="Send a hook a test event",
)
async def test_hook(
    request: Request,
    project: Project = Depends(resolved_project),
    hook: Hook = Depends(resolved_hook),
    principal: Principal = Depends(may_change_hooks),
    session: AsyncSession = SessionDependency,
) -> HookDeliveryRead:
    """POST a `hook.test` event to the hook's URL now, and answer with what came back.

    Sent whether or not the hook is enabled — finding out whether the receiver
    is listening is the point of switching one on. Kept in the delivery log
    like any other, and not retried.
    """
    delivery = HookDelivery(
        hook_id=hook.id,
        event=hook_delivery.TEST_EVENT,
        payload=hooks.sample_body(project, hook, principal),
    )
    session.add(delivery)
    async with _client(request) as client:
        await hook_delivery.send_now(
            session, client, request.app.state.settings.vault_key, hook, delivery
        )
    await session.flush()
    await session.refresh(delivery)
    return HookDeliveryRead.model_validate(delivery)


@router.get(
    "/projects/{project_ref}/hooks/{hook_id}/deliveries",
    response_model=list[HookDeliveryRead],
    summary="List a hook's recent deliveries",
)
async def list_deliveries(
    hook: Hook = Depends(resolved_hook),
    _: Principal = Depends(may_read_hooks),
    session: AsyncSession = SessionDependency,
    limit: int = Query(default=25, ge=1, le=100),
) -> list[HookDeliveryRead]:
    """Newest first, each with every attempt made at it. Finished ones are kept
    for 30 days."""
    found = await hooks.deliveries(session, hook, limit=limit)
    return [HookDeliveryRead.model_validate(delivery) for delivery in found]


@router.post(
    "/projects/{project_ref}/hooks/{hook_id}/deliveries/{delivery_id}/redeliver",
    response_model=HookDeliveryRead,
    summary="Send a delivery again",
)
async def redeliver(
    delivery_id: UUID,
    hook: Hook = Depends(resolved_hook),
    _: Principal = Depends(may_change_hooks),
    session: AsyncSession = SessionDependency,
) -> HookDeliveryRead:
    """Queue the same body for one more attempt at the next pass, however the
    last one went."""
    delivery = await hooks.delivery(session, hook, delivery_id)
    hook_delivery.requeue(delivery)
    # Through the outbox rather than straight at the courier: woken before the
    # commit, it would look, find nothing due yet, and sleep through it.
    publish_into(session, {"type": hooks.QUEUED})
    await session.flush()
    await session.refresh(delivery)
    return HookDeliveryRead.model_validate(delivery)
