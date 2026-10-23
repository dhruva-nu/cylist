"""Push devices: a phone registering to be told about agents."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.models.device import DevicePlatform
from app.schemas.common import Page, Schema
from app.services.commands.devices import (
    DeviceRegister as DeviceRegister,
)


class DeviceRead(Schema):
    """A registered device. Its FCM token is never sent back."""

    id: UUID
    platform: DevicePlatform
    label: str
    person_id: UUID
    token_id: UUID = Field(description="The API token the device registered with.")
    created_at: datetime
    last_registered_at: datetime


class DevicePage(Page[DeviceRead]):
    """A page of the caller's push devices, newest registration first."""
