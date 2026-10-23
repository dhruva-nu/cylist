"""The base every command is built on."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Command(BaseModel):
    """Base for a plain input to a service: built, validated, and handed over.

    ``from_attributes`` is what the HTTP layer's own base has always had, kept so
    that a command can be built from an object as well as from a body.
    """

    model_config = ConfigDict(from_attributes=True)
