"""The commands the agents services take.

Plain inputs to the services, validated as they are built. The HTTP layer
re-exports them (``app.schemas.agents``) as the bodies its routes accept,
so there is one definition and the services import nothing from it.
"""

from __future__ import annotations

from pydantic import Field

from app.services.commands.base import Command


class SkillUpdate(Command):
    """Change a skill's description without re-uploading it."""

    description: str | None = Field(
        default=None,
        max_length=2000,
        description="One line on what the skill does. Send null to clear it.",
    )
