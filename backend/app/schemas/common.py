"""Shapes shared by more than one endpoint."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Schema(BaseModel):
    """Base for response models read straight off an ORM object."""

    model_config = ConfigDict(from_attributes=True)


class ErrorDetail(Schema):
    code: str = Field(description="Stable machine-readable error code.")
    message: str = Field(description="Human-readable explanation.")
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(Schema):
    """The envelope every failure is returned in."""

    error: ErrorDetail


class Acknowledged(Schema):
    """Returned by endpoints whose only outcome is 'it worked'."""

    ok: bool = True
