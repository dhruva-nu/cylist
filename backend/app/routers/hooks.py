"""Hooks: a project's rules for POSTing its board's changes to a URL.

Everything here is the project admin's, reading included: a hook's URL is
often a capability in itself — a CI trigger, a chat webhook — and its delivery
log carries every event the board sent out. See :mod:`app.services.hooks` for
what fires and :mod:`app.services.hook_delivery` for how it is sent.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.core.crypto import VaultCipher
from app.core.errors import NotFoundError
from app.core.ids import uuid7
from app.db import SessionDependency, database_of, flush_or_refuse, publish_into
from app.models.hook import DeliveryState, Hook, HookDelivery
from app.models.project import Project
from app.routers import guards
from app.routers.paging import PageQuery
from app.routers.projects import resolved_project
from app.routers.vault import vault_cipher
from app.schemas.hooks import (
    DeliverySummary,
    HookCreate,
    HookCreated,
    HookDeliveryPage,
    HookDeliveryRead,
    HookEvent,
    HookFire,
    HookPage,
    HookRead,
    HookSecret,
    HookSecretRotate,
    HookUpdate,
)
from app.services import activity, hook_delivery, hooks
from app.services.hooks import HookFields, HookFilters
from app.services.paging import PageRequest

router = APIRouter(tags=["hooks"])

_FILTERS = (
    "to_column_id",
    "from_column_id",
    "template_id",
    "task_type",
    "agent_only",
    "mentions_only",
    "mentioned_person_id",
)


# The project's admin, and nobody else — the same test roles use. Reading is
# fenced too: see the module note on why a hook's URL is worth guarding.
may_read_hooks = guards.project_admin_only("see or change its hooks", Scope.READ)
may_change_hooks = guards.project_admin_only("see or change its hooks", Scope.WRITE)


async def resolved_hook(
    hook_id: UUID,
    project: Project = Depends(resolved_project),
    session: AsyncSession = SessionDependency,
) -> Hook:
    return await hooks.get(session, project, hook_id)


def _hook_read(hook: Hook, labels: hooks.HookLabels) -> HookRead:
    last = labels.last_delivery
    return HookRead(
        id=hook.id,
        name=hook.name,
        enabled=hook.enabled,
        manual=hook.manual,
        url=hook.url,
        verbs=list(hook.verbs),
        to_column_id=hook.to_column_id,
        to_column_name=labels.to_column_name,
        from_column_id=hook.from_column_id,
        from_column_name=labels.from_column_name,
        template_id=hook.template_id,
        template_name=labels.template_name,
        task_type=hook.task_type,
        agent_only=hook.agent_only,
        mentions_only=hook.mentions_only,
        mentioned_person_id=hook.mentioned_person_id,
        mentioned_person_name=labels.mentioned_person_name,
        secret_hint=hook.secret_hint,
        last_delivery=DeliverySummary.model_validate(last) if last is not None else None,
        created_at=hook.created_at,
        updated_at=hook.updated_at,
    )


async def _read(session: AsyncSession, hook: Hook) -> HookRead:
    """One hook as the page draws it, for the endpoints that return exactly one."""
    labels = await hooks.labels_for(session, [hook])
    return _hook_read(hook, labels[hook.id])


def _filters_of(body: HookCreate | HookUpdate) -> HookFilters:
    return HookFilters(
        given=frozenset(field for field in _FILTERS if field in body.model_fields_set),
        to_column_id=body.to_column_id,
        from_column_id=body.from_column_id,
        template_id=body.template_id,
        task_type=body.task_type,
        agent_only=bool(body.agent_only),
        mentions_only=bool(body.mentions_only),
        mentioned_person_id=body.mentioned_person_id,
    )


def _client(request: Request) -> httpx.AsyncClient:
    """A client for one test send. Its transport is the app's to choose, so a
    test can answer in place of a receiver."""
    return httpx.AsyncClient(transport=request.app.state.hook_transport)


def _unsent(hook: Hook, event: str, payload: dict[str, Any]) -> HookDelivery:
    """A delivery about to be sent from the request, not yet in any session.

    Its defaults are filled in here rather than at a flush, because it is sent
    — and the attempt written onto it — before it is ever added to one.
    """
    return HookDelivery(
        id=uuid7(),
        hook_id=hook.id,
        event=event,
        payload=payload,
        state=DeliveryState.PENDING,
        attempt_count=0,
        attempts=[],
    )


async def _send_released(
    request: Request, session: AsyncSession, hook: Hook, delivery: HookDelivery
) -> None:
    """Send ``delivery`` now, holding no database connection while the receiver answers.

    A receiver has :data:`~app.services.hook_delivery.TIMEOUT` to answer, and a
    request that held its connection for that long, a few at a time, used to
    starve the pool (CYLIST-131). So the request's work so far — only reads,
    and the credential's bookkeeping — is committed first, and the delivery
    joins the session only once the attempt is written on it, to be kept in a
    short transaction of its own.
    """
    await database_of(request).release(session)
    async with _client(request) as client:
        await hook_delivery.send_now(
            session, client, request.app.state.settings.vault_keyring, hook, delivery
        )


async def _keep(session: AsyncSession, hook: Hook, delivery: HookDelivery) -> None:
    """Put a sent delivery in the log.

    The hook may have been deleted while the receiver was answering, now that
    no row of its was held over the send; that is a 404, not a foreign-key 500.
    """
    session.add(delivery)
    await flush_or_refuse(
        session,
        NotFoundError(
            "That hook was deleted while it was being sent.", details={"hook_id": str(hook.id)}
        ),
    )


@router.get(
    "/hooks/events",
    response_model=list[HookEvent],
    summary="List the events a hook can fire on",
)
async def list_events(_: Principal = Depends(require(Scope.READ))) -> list[HookEvent]:
    """Every verb a hook may name, grouped and in the order the Hooks page lists them."""
    return [
        HookEvent(
            verb=kind.verb,
            label=kind.label,
            hint=kind.hint,
            category=category.id,
            category_name=category.name,
        )
        for category in hooks.CATEGORIES
        for kind in category.events
    ]


@router.get(
    "/projects/{project_ref}/hooks",
    response_model=HookPage,
    summary="List a project's hooks",
)
async def list_hooks(
    project: Project = Depends(resolved_project),
    _: Principal = Depends(may_read_hooks),
    session: AsyncSession = SessionDependency,
    page: PageRequest = PageQuery,
) -> HookPage:
    """The project's hooks, by name, a page at a time."""
    found = await hooks.list_hooks(session, project, page)
    labels = await hooks.labels_for(session, found.rows)
    return HookPage(
        items=[_hook_read(hook, labels[hook.id]) for hook in found.rows], next=found.next
    )


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
        manual=body.manual,
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
        HookFields(
            name=body.name,
            enabled=body.enabled,
            manual=body.manual,
            verbs=body.verbs,
            url=body.url,
        ),
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
    delivery = _unsent(hook, hook_delivery.TEST_EVENT, hooks.sample_body(project, hook, principal))
    await _send_released(request, session, hook, delivery)
    await _keep(session, hook, delivery)
    await session.refresh(delivery)
    return HookDeliveryRead.model_validate(delivery)


@router.post(
    "/projects/{project_ref}/hooks/{hook_id}/fire",
    response_model=HookDeliveryRead,
    summary="Fire a manual hook",
    responses={409: {"description": "The hook is not a manual one, or it is switched off."}},
)
async def fire_hook(
    request: Request,
    body: HookFire | None = None,
    project: Project = Depends(resolved_project),
    hook: Hook = Depends(resolved_hook),
    principal: Principal = Depends(may_change_hooks),
    session: AsyncSession = SessionDependency,
) -> HookDeliveryRead:
    """POST a `hook.fired` event to a manual hook's URL now, and answer with what came back.

    Sent at once, so the page that pressed the button sees the receiver's
    answer. Unlike a test it is the real thing: if the receiver does not take
    it, it is tried again on the same schedule as any change.
    """
    hooks.refuse_unless_fireable(project, hook)
    message = body.message if body is not None else None
    delivery = _unsent(
        hook, hook_delivery.FIRED_EVENT, hooks.fired_body(project, hook, principal, message)
    )
    await _send_released(request, session, hook, delivery)
    # Recorded once the receiver has answered, in the transaction that keeps
    # the delivery, so a firing is on the trail exactly when it is in the log.
    await activity.record(
        session,
        principal,
        "hook.fired",
        entity_type="hook",
        entity_id=hook.id,
        project_id=project.id,
        payload={"name": hook.name, "message": message},
    )
    await _keep(session, hook, delivery)
    await session.refresh(delivery)
    return HookDeliveryRead.model_validate(delivery)


@router.get(
    "/projects/{project_ref}/hooks/{hook_id}/deliveries",
    response_model=HookDeliveryPage,
    summary="List a hook's recent deliveries",
)
async def list_deliveries(
    hook: Hook = Depends(resolved_hook),
    _: Principal = Depends(may_read_hooks),
    session: AsyncSession = SessionDependency,
    page: PageRequest = PageQuery,
) -> HookDeliveryPage:
    """Newest first, a page at a time, each with every attempt made at it.
    Finished ones are kept for 30 days."""
    found = await hooks.deliveries(session, hook, page)
    return HookDeliveryPage(
        items=[HookDeliveryRead.model_validate(delivery) for delivery in found.rows],
        next=found.next,
    )


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
