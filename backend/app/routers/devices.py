"""Push devices: a phone asking to be told when an agent needs its person.

The Android app registers its Firebase Cloud Messaging token here after
signing in and unregisters it before signing out (CYLIST-107). Both need only
``write``, which the phone's own token carries; ``admin`` is not involved,
because a device can only ever be registered for the caller.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.db import SessionDependency
from app.schemas.common import Acknowledged
from app.schemas.devices import DeviceRead, DeviceRegister
from app.services import devices

router = APIRouter(prefix="/devices", tags=["devices"])


@router.get("", response_model=list[DeviceRead], summary="List your push devices")
async def list_devices(
    principal: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
) -> list[DeviceRead]:
    """The devices registered to receive pushes for you, newest first."""
    return [
        DeviceRead.model_validate(device) for device in await devices.list_for(session, principal)
    ]


@router.post(
    "",
    response_model=DeviceRead,
    summary="Register a push device",
    responses={403: {"description": "The bootstrap session is nobody to push to."}},
)
async def register_device(
    body: DeviceRegister,
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> DeviceRead:
    """Register this device's FCM token for the caller, or refresh it.

    Idempotent: send it after every sign-in and whenever Firebase rotates the
    token. A token another person registered moves to the caller, since an
    install is used by whoever is signed in on it now.

    The device is sent to while the credential that registered it stays live;
    revoking that token stops its pushes.
    """
    return DeviceRead.model_validate(await devices.register(session, principal, body))


@router.delete(
    "/{device_id}",
    response_model=Acknowledged,
    summary="Unregister a push device",
    responses={404: {"description": "No such device of yours."}},
)
async def unregister_device(
    device_id: UUID,
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> Acknowledged:
    """Stop pushing to one of your devices. The app calls this before signing out."""
    await devices.unregister(session, principal, device_id)
    return Acknowledged()
