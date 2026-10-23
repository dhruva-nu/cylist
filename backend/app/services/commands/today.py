"""The commands the today services take.

Plain inputs to the services, validated as they are built. The HTTP layer
re-exports them (``app.schemas.today``) as the bodies its routes accept,
so there is one definition and the services import nothing from it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.models.today import TEXT_MAX_LENGTH


class TodoUpdate(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=TEXT_MAX_LENGTH)
    state: Literal["open", "done", "not_done"] | None = Field(
        default=None,
        description="Tick it (`done`), untick it (`open`) or give up on it (`not_done`). "
        "`moved` is what wrap-up sets, never this.",
    )
