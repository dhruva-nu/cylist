"""Shapes shared by more than one endpoint."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Schema(BaseModel):
    """Base for response models read straight off an ORM object."""

    model_config = ConfigDict(from_attributes=True)


class Acknowledged(Schema):
    """Returned by endpoints whose only outcome is 'it worked'."""

    ok: bool = True
