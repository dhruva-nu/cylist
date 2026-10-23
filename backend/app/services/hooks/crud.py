"""The rules themselves: making, changing and deleting a hook, and what it may be told."""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import HOOK_SECRET_DOMAIN, VaultCipher
from app.core.errors import (
    ConflictError,
    NotFoundError,
    Reason,
    UnprocessableRequestError,
    not_on_this_project,
)
from app.core.ids import uuid7
from app.db import flush_or_refuse
from app.models.board import BoardColumn
from app.models.hook import Hook, HookDelivery
from app.models.person import Person
from app.models.project import Project
from app.models.task import TaskType
from app.models.template import TaskTemplate
from app.services import names, projects
from app.services.hooks.catalogue import EVENTS, SECRET_MAX_LENGTH, SECRET_MIN_LENGTH, SECRET_PREFIX
from app.services.paging import PageRequest, Window, keyset

# --- The rules themselves --------------------------------------------------


@dataclass(frozen=True)
class HookFields:
    """What an update may set. ``None`` leaves a field alone; to clear a
    filter, :class:`HookFilters` says so explicitly."""

    name: str | None = None
    enabled: bool | None = None
    manual: bool | None = None
    verbs: list[str] | None = None
    url: str | None = None


@dataclass(frozen=True)
class HookFilters:
    """The filters an update sets, each one only if it is named in ``given``."""

    given: frozenset[str]
    to_column_id: UUID | None = None
    from_column_id: UUID | None = None
    template_id: UUID | None = None
    task_type: TaskType | None = None
    agent_only: bool = False
    mentions_only: bool = False
    mentioned_person_id: UUID | None = None


@dataclass(frozen=True)
class HookLabels:
    """What a hook's ids name, and how it last went — for drawing it on a page."""

    to_column_name: str | None
    from_column_name: str | None
    template_name: str | None
    mentioned_person_name: str | None
    last_delivery: HookDelivery | None


async def list_hooks(session: AsyncSession, project: Project, page: PageRequest) -> Window[Hook]:
    return await keyset(
        session,
        select(Hook).where(Hook.project_id == project.id),
        keys=(Hook.name, Hook.id),
        page=page,
    )


async def labels_for(session: AsyncSession, hooks: Sequence[Hook]) -> dict[UUID, HookLabels]:
    """Name every column and template these hooks filter on, and find each one's
    latest delivery.

    Four queries for the whole list rather than five per hook: the Hooks page
    draws every one of them at once. A filter whose column, template or person
    has since been deleted is named ``None``, which is what tells the page that
    it now matches nothing.
    """
    if not hooks:
        return {}

    column_ids = {
        column_id
        for hook in hooks
        for column_id in (hook.to_column_id, hook.from_column_id)
        if column_id is not None
    }
    template_ids = {hook.template_id for hook in hooks if hook.template_id is not None}
    person_ids = {
        hook.mentioned_person_id for hook in hooks if hook.mentioned_person_id is not None
    }
    column_names = await _names_by_id(session, BoardColumn, column_ids)
    template_names = await _names_by_id(session, TaskTemplate, template_ids)
    person_names = await _names_by_id(session, Person, person_ids)

    # DISTINCT ON keeps the first row per hook in this ordering — its newest.
    latest = await session.scalars(
        select(HookDelivery)
        .where(HookDelivery.hook_id.in_([hook.id for hook in hooks]))
        .order_by(HookDelivery.hook_id, HookDelivery.created_at.desc(), HookDelivery.id.desc())
        .distinct(HookDelivery.hook_id)
    )
    last_by_hook = {delivery.hook_id: delivery for delivery in latest}

    return {
        hook.id: HookLabels(
            to_column_name=column_names.get(hook.to_column_id) if hook.to_column_id else None,
            from_column_name=(
                column_names.get(hook.from_column_id) if hook.from_column_id else None
            ),
            template_name=template_names.get(hook.template_id) if hook.template_id else None,
            mentioned_person_name=(
                person_names.get(hook.mentioned_person_id) if hook.mentioned_person_id else None
            ),
            last_delivery=last_by_hook.get(hook.id),
        )
        for hook in hooks
    }


async def _names_by_id(
    session: AsyncSession,
    model: type[BoardColumn] | type[TaskTemplate] | type[Person],
    ids: set[UUID],
) -> dict[UUID, str]:
    if not ids:
        return {}
    rows = await session.execute(select(model.id, model.name).where(model.id.in_(ids)))
    return dict(rows.tuples().all())


async def get(session: AsyncSession, project: Project, hook_id: UUID) -> Hook:
    hook = await session.get(Hook, hook_id)
    if hook is None or hook.project_id != project.id:
        raise NotFoundError(
            f"{project.key} has no hook {hook_id}.",
            details={"project_key": project.key, "hook_id": str(hook_id)},
        )
    return hook


async def create(
    session: AsyncSession,
    project: Project,
    cipher: VaultCipher,
    *,
    name: str,
    url: str,
    enabled: bool,
    manual: bool,
    verbs: list[str],
    filters: HookFilters,
    secret: str | None,
) -> tuple[Hook, str]:
    """Make a hook, and hand back its signing secret — the only time it is shown
    unasked. Generated unless the caller brought one."""
    await _refuse_taken_name(session, project, name)
    # The id is drawn here rather than at the flush, because the secret is
    # sealed against it and the column holding it is NOT NULL.
    hook = Hook(
        id=uuid7(),
        project_id=project.id,
        name=name,
        enabled=enabled,
        manual=manual,
        verbs=_checked_verbs(verbs),
        url=checked_url(url),
    )
    await _apply_filters(session, project, hook, filters)
    _refuse_rules_on_manual(hook)
    plaintext = _seal(cipher, hook, secret)
    session.add(hook)
    await flush_or_refuse(session, _taken_name(project, name))
    return hook, plaintext


async def update(
    session: AsyncSession,
    project: Project,
    hook: Hook,
    fields: HookFields,
    filters: HookFilters,
) -> list[str]:
    """Change a hook. Returns the names of the fields that changed."""
    changed: list[str] = []
    if fields.name is not None and fields.name != hook.name:
        await _refuse_taken_name(session, project, fields.name)
        hook.name = fields.name
        await flush_or_refuse(session, _taken_name(project, fields.name))
        changed.append("name")
    if fields.enabled is not None and fields.enabled != hook.enabled:
        hook.enabled = fields.enabled
        changed.append("enabled")
    if fields.manual is not None and fields.manual != hook.manual:
        hook.manual = fields.manual
        changed.append("manual")
    if fields.verbs is not None:
        verbs = _checked_verbs(fields.verbs)
        if verbs != list(hook.verbs):
            hook.verbs = verbs
            changed.append("events")
    if fields.url is not None:
        url = checked_url(fields.url)
        if url != hook.url:
            hook.url = url
            # Somewhere new has earned a fresh start: whatever the old address
            # kept failing to do says nothing about this one.
            hook.consecutive_failures = 0
            hook.paused_until = None
            changed.append("url")
    changed += await _apply_filters(session, project, hook, filters)
    _refuse_rules_on_manual(hook)
    return changed


async def delete(session: AsyncSession, hook: Hook) -> None:
    await session.delete(hook)


def rotate_secret(cipher: VaultCipher, hook: Hook, secret: str | None = None) -> str:
    """Give a hook a new signing secret, returning it. The old one stops
    verifying for every delivery sent from now on, including retries."""
    return _seal(cipher, hook, secret)


def secret_of(cipher: VaultCipher, hook: Hook) -> str:
    return cipher.open(
        hook.secret_ciphertext,
        node_id=hook.id,
        key_version=hook.key_version,
        domain=HOOK_SECRET_DOMAIN,
    )


async def deliveries(session: AsyncSession, hook: Hook, page: PageRequest) -> Window[HookDelivery]:
    return await keyset(
        session,
        select(HookDelivery).where(HookDelivery.hook_id == hook.id),
        keys=(HookDelivery.created_at, HookDelivery.id),
        page=page,
        descending=True,
    )


async def delivery(session: AsyncSession, hook: Hook, delivery_id: UUID) -> HookDelivery:
    found = await session.get(HookDelivery, delivery_id)
    if found is None or found.hook_id != hook.id:
        raise NotFoundError(
            f"The hook {hook.name!r} has no delivery {delivery_id}.",
            details={"hook_id": str(hook.id), "delivery_id": str(delivery_id)},
        )
    return found


def refuse_unless_fireable(project: Project, hook: Hook) -> None:
    """Only a manual hook, switched on, is fired by hand.

    A hook that fires on changes is not: what it sends is a change, and a
    firing reports none — "Send test" is how to try one of those. One that is
    off stays off, as it does for a board's changes.
    """
    if not hook.manual:
        raise ConflictError(
            f"The hook {hook.name!r} fires on {project.key}'s changes, not by hand. "
            "Send it a test instead, or make it a manual hook.",
            details={"hook_id": str(hook.id)},
            reason=Reason.HOOK_NOT_MANUAL,
        )
    if not hook.enabled:
        raise ConflictError(
            f"The hook {hook.name!r} is off. Switch it on to fire it.",
            details={"hook_id": str(hook.id)},
            reason=Reason.HOOK_OFF,
        )


def checked_url(url: str) -> str:
    """An absolute ``http``/``https`` URL, or a 422 saying what is wrong with it.

    Read twice: once by ``urlsplit`` for its shape, and once by the client the
    courier sends with, which is stricter — a port that is not a number, say.
    A URL only the second refuses would otherwise be saved, and then fail
    every delivery at the moment it was sent rather than here, where the
    person typing it can see why.
    """
    url = url.strip()
    refused = UnprocessableRequestError(
        "A hook's URL must be an absolute http:// or https:// address.",
        details={"url": url},
    )
    try:
        parts = urlsplit(url)
        parsed = httpx.URL(url)
    except (ValueError, httpx.InvalidURL) as exc:
        raise refused from exc
    if parts.scheme not in ("http", "https") or not parts.netloc or not parsed.host:
        raise refused
    return url


def _checked_verbs(verbs: list[str]) -> list[str]:
    unknown = sorted(set(verbs) - EVENTS.keys())
    if unknown:
        raise UnprocessableRequestError(
            f"Hooks cannot fire on {', '.join(unknown)}.",
            details={"unknown": unknown, "known": sorted(EVENTS)},
        )
    # Kept in catalogue order, so the same set always reads the same way.
    return [verb for verb in EVENTS if verb in verbs]


async def _apply_filters(
    session: AsyncSession, project: Project, hook: Hook, filters: HookFilters
) -> list[str]:
    changed: list[str] = []
    for field in ("to_column_id", "from_column_id"):
        if field in filters.given:
            value = getattr(filters, field)
            if value is not None:
                await _own_column(session, project, value)
            if getattr(hook, field) != value:
                setattr(hook, field, value)
                changed.append(field)
    if "template_id" in filters.given:
        if filters.template_id is not None:
            await _own_template(session, project, filters.template_id)
        if hook.template_id != filters.template_id:
            hook.template_id = filters.template_id
            changed.append("template_id")
    if "task_type" in filters.given and hook.task_type != filters.task_type:
        hook.task_type = filters.task_type
        changed.append("task_type")
    if "agent_only" in filters.given and hook.agent_only != filters.agent_only:
        hook.agent_only = filters.agent_only
        changed.append("agent_only")
    if "mentions_only" in filters.given and hook.mentions_only != filters.mentions_only:
        hook.mentions_only = filters.mentions_only
        changed.append("mentions_only")
    if "mentioned_person_id" in filters.given:
        if filters.mentioned_person_id is not None:
            await _own_member(session, project, filters.mentioned_person_id)
        if hook.mentioned_person_id != filters.mentioned_person_id:
            hook.mentioned_person_id = filters.mentioned_person_id
            changed.append("mentioned_person_id")
    return changed


def _refuse_rules_on_manual(hook: Hook) -> None:
    # A manual hook with events or filters would read as though they did
    # something, and they never would.
    rules = any(
        (
            hook.verbs,
            hook.to_column_id,
            hook.from_column_id,
            hook.template_id,
            hook.task_type,
            hook.agent_only,
            hook.mentions_only,
            hook.mentioned_person_id,
        )
    )
    if hook.manual and rules:
        raise UnprocessableRequestError(
            "A manual hook fires only when somebody fires it, so it takes no events or "
            "filters. Clear them, or leave it firing on changes.",
            details={"hook_id": str(hook.id)},
        )


async def _own_column(session: AsyncSession, project: Project, column_id: UUID) -> None:
    column = await session.get(BoardColumn, column_id)
    if column is None or column.project_id != project.id:
        raise not_on_this_project("column", column_id=column_id)


async def _own_template(session: AsyncSession, project: Project, template_id: UUID) -> None:
    template = await session.get(TaskTemplate, template_id)
    if template is None or template.project_id != project.id:
        raise not_on_this_project("template", template_id=template_id)


async def _own_member(session: AsyncSession, project: Project, person_id: UUID) -> None:
    # Only a member can be tagged, so a hook waiting on anybody else would
    # wait forever.
    if not await projects.is_member(session, project.id, person_id):
        raise UnprocessableRequestError(
            f"That person is not on {project.key}, so no comment there can tag them.",
            details={"person_id": str(person_id)},
        )


async def _refuse_taken_name(session: AsyncSession, project: Project, name: str) -> None:
    await names.ensure_free(
        session,
        select(Hook.id).where(Hook.project_id == project.id, Hook.name == name),
        lambda _: _taken_name(project, name),
    )


def _taken_name(project: Project, name: str) -> ConflictError:
    return names.taken(f"{project.key} already has a hook called {name!r}.", name=name)


def _seal(cipher: VaultCipher, hook: Hook, secret: str | None) -> str:
    plaintext = secret.strip() if secret else SECRET_PREFIX + secrets.token_urlsafe(32)
    if not SECRET_MIN_LENGTH <= len(plaintext) <= SECRET_MAX_LENGTH:
        raise UnprocessableRequestError(
            f"A signing secret must be {SECRET_MIN_LENGTH} to {SECRET_MAX_LENGTH} characters, "
            "or left out to have one generated.",
        )
    hook.secret_ciphertext = cipher.seal(plaintext, node_id=hook.id, domain=HOOK_SECRET_DOMAIN)
    hook.key_version = cipher.version
    hook.secret_hint = plaintext[-4:]
    return plaintext
