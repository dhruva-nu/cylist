"""Images in a card's description."""

from __future__ import annotations

from uuid import UUID

from pydantic import Field

from app.schemas.common import Schema


class ImageRead(Schema):
    """An uploaded image, and the address a description refers to it by."""

    id: UUID
    url: str = Field(
        description=(
            "Where the image is served, relative to the site root: "
            "`/api/v1/images/{id}`. Put it in a description as markdown — "
            "`![screenshot](/api/v1/images/{id})`."
        )
    )
    mime: str = Field(description="The type read off the content: PNG, JPEG, GIF or WebP.")
    size: int = Field(description="Bytes.")
