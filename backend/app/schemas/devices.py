"""Push devices: a phone registering to be told about agents."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.models.device import DevicePlatform
from app.schemas.common import Schema


class DeviceRegister(Schema):
    fcm_token: str = Field(
        min_length=1,
        max_length=4096,
        description=(
            "The registration token Firebase Cloud Messaging gave this app install. "
            "Registering one that is already known moves it to the caller."
        ),
    )
    platform: DevicePlatform = DevicePlatform.ANDROID
    label: str = Field(
        min_length=1,
        max_length=120,
        description="What the device is called, as a person would recognise it.",
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
