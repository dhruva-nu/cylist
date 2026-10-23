"""The commands the devices services take.

Plain inputs to the services, validated as they are built. The HTTP layer
re-exports them (``app.schemas.devices``) as the bodies its routes accept,
so there is one definition and the services import nothing from it.
"""

from __future__ import annotations

from pydantic import Field

from app.models.device import DevicePlatform
from app.services.commands.base import Command


class DeviceRegister(Command):
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
