"""The base every read model is built on."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ReadModel(BaseModel):
    """Base for a plain value a service builds and hands back.

    ``from_attributes`` is what the HTTP layer's own base has always had, kept so
    that one can be built straight off an ORM row.
    """

    model_config = ConfigDict(from_attributes=True)
