"""Push devices: registering a phone, forgetting it, and finding whom to tell.

The HTTP side is :mod:`app.routers.devices`; what is sent, and when, is
:mod:`app.services.push`. This module is only the rows.
"""

from __future__ import annotations

from collections.abc import Collection
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.principal import Principal
from app.core.clock import now
from app.core.errors import ForbiddenError, NotFoundError
from app.models.api_token import ApiToken
from app.models.device import PushDevice
from app.services.commands.devices import DeviceRegister
from app.services.paging import PageRequest, Window, keyset


def _caller(principal: Principal) -> tuple[UUID, UUID]:
    """The person and credential a device would belong to, or refuse.

    The bootstrap session is nobody, and a push has to be addressed to
    somebody.
    """
    if principal.person_id is None or principal.token_id is None:
        raise ForbiddenError("Sign in as a person to register a device.")
    return principal.person_id, principal.token_id


async def register(session: AsyncSession, principal: Principal, data: DeviceRegister) -> PushDevice:
    """Register ``data.fcm_token`` for the caller, or move it to them.

    Idempotent: the app calls this on every sign-in and every token rotation,
    and the same token registered twice is still one row.
    """
    person_id, token_id = _caller(principal)
    moment = now()
    # One upsert rather than a lookup and then an insert: the app registers on
    # every sign-in, and two of those racing for one phone both found no row
    # and the second insert failed on the token's unique index.
    claimed = {
        "person_id": person_id,
        "token_id": token_id,
        "platform": data.platform,
        "label": data.label,
        "last_registered_at": moment,
    }
    statement = postgresql.insert(PushDevice).values(fcm_token=data.fcm_token, **claimed)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[PushDevice.fcm_token],
            set_={**claimed, "updated_at": func.now()},
        )
    )
    device = await session.scalar(
        select(PushDevice)
        .where(PushDevice.fcm_token == data.fcm_token)
        .execution_options(populate_existing=True)
    )
    if device is None:  # pragma: no cover - the upsert either inserted or updated it
        raise RuntimeError("No device row after registering one")
    return device


async def list_for(
    session: AsyncSession, principal: Principal, page: PageRequest
) -> Window[PushDevice]:
    """A page of the caller's own devices, newest registration first."""
    person_id, _ = _caller(principal)
    return await keyset(
        session,
        select(PushDevice).where(PushDevice.person_id == person_id),
        keys=(PushDevice.last_registered_at, PushDevice.id),
        page=page,
        descending=True,
    )


async def unregister(session: AsyncSession, principal: Principal, device_id: UUID) -> None:
    """Forget one of the caller's devices.

    Somebody else's device is reported as missing rather than forbidden, so
    the endpoint does not say which ids exist.
    """
    person_id, _ = _caller(principal)
    device = await session.get(PushDevice, device_id)
    if device is None or device.person_id != person_id:
        raise NotFoundError("No such device.")
    await session.delete(device)


async def recipients(session: AsyncSession, person_ids: Collection[UUID]) -> list[PushDevice]:
    """Every device of these people that may still be sent to.

    A device registered with a credential that has since been revoked or has
    expired is left out: signing a phone out from the web app silences it
    without the phone's help.
    """
    if not person_ids:
        return []
    moment = now()
    devices = await session.scalars(
        select(PushDevice)
        .join(ApiToken, ApiToken.id == PushDevice.token_id)
        .where(
            PushDevice.person_id.in_(person_ids),
            ApiToken.revoked_at.is_(None),
            (ApiToken.expires_at.is_(None)) | (ApiToken.expires_at > moment),
        )
        .order_by(PushDevice.created_at)
    )
    return list(devices)


async def forget_tokens(session: AsyncSession, fcm_tokens: Collection[str]) -> int:
    """Delete the devices FCM said no longer exist. Returns how many went."""
    if not fcm_tokens:
        return 0
    deleted = await session.execute(
        delete(PushDevice).where(PushDevice.fcm_token.in_(fcm_tokens)).returning(PushDevice.id)
    )
    return len(deleted.all())
