"""Shapes shared by more than one endpoint."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Schema(BaseModel):
    """Base for response models read straight off an ORM object."""

    model_config = ConfigDict(from_attributes=True)


class Acknowledged(Schema):
    """Returned by endpoints whose only outcome is 'it worked'."""

    ok: bool = True


class Page[ItemT](Schema):
    """One page of a list, and the cursor for the next.

    Every list endpoint answers in this shape (CYLIST-130): at most `limit`
    rows, in the list's own order, and `next` to pass back as `after` for the
    rows that follow. Each list has a named subclass — `PersonPage`,
    `TaskSummaryPage` — so a generated client gets a type it can name rather
    than one spelled out of brackets.
    """

    items: list[ItemT]
    next: UUID | None = Field(
        description=("Pass this as `after` to read the next page. Null when this page is the last.")
    )
