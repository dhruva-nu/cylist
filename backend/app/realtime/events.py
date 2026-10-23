"""What a committed transaction announces, as types.

Events used to be dictionaries. Each publisher built one by hand, the hub read
them back with ``event.get("task_id")``, and the bus kept a list of the field
names that were UUIDs so it could turn them back from strings when another
process's copy arrived. A field added to an event and not to that list reached
the process that wrote it as a ``UUID`` and every other as a ``str``, and
nothing said so.

An event is now a frozen dataclass, and the codec reads what it should do with
each field off the class: a field annotated as a UUID is written as a string and
restored as a UUID, with no list to keep. Every event is in :data:`EVENTS`, the
one table that says which type name means which class — a test round-trips each
one — and an event that never leaves its process says so with ``wire = False``.

This module is a leaf: it imports nothing of the app's, because the database
layer queues these and the hub and bus consume them.
"""

from __future__ import annotations

import types
import typing
from dataclasses import MISSING, dataclass, fields
from typing import Any, ClassVar, Self
from uuid import UUID


class Event:
    """Base of everything published. Subclasses are dataclasses, and name
    themselves with ``type``."""

    type: ClassVar[str]
    wire: ClassVar[bool] = True
    """Whether other processes hear of it. Not for the ones whose whole job is
    done in the process that committed: a hook delivery is a row any courier may
    claim, and a push is sent once, by whoever made it news."""

    def to_wire(self) -> dict[str, Any]:
        """The event as JSON-safe data: every UUID a string, the type name added."""
        out: dict[str, Any] = {"type": self.type}
        for field in fields(typing.cast(Any, self)):
            value = getattr(self, field.name)
            out[field.name] = str(value) if isinstance(value, UUID) else value
        return out

    @classmethod
    def from_wire(cls, data: dict[str, Any]) -> Self | None:
        """The event from what :meth:`to_wire` wrote, or ``None`` if it will not do.

        A field the class says is a UUID is restored as one; if it is present
        and is not a UUID the whole event is refused rather than passed on with
        a string the hub would misread as "no such id".
        """
        hints = typing.get_type_hints(cls)
        restored: dict[str, Any] = {}
        for field in fields(typing.cast(Any, cls)):
            if field.name not in data:
                if _required(field):
                    return None
                continue
            value = data[field.name]
            if value is not None and _is_uuid(hints[field.name]):
                if not isinstance(value, str):
                    return None
                try:
                    value = UUID(value)
                except ValueError:
                    return None
            restored[field.name] = value
        return cls(**restored)


def _required(field: Any) -> bool:
    return field.default is MISSING and field.default_factory is MISSING


def _is_uuid(annotation: Any) -> bool:
    """Whether a field is annotated ``UUID`` or ``UUID | None``."""
    if annotation is UUID:
        return True
    if typing.get_origin(annotation) in (typing.Union, types.UnionType):
        return UUID in typing.get_args(annotation)
    return False


@dataclass(frozen=True)
class BoardChanged(Event):
    """Something on a project committed. A doorbell, naming at most what."""

    type: ClassVar[str] = "board.changed"
    project_id: UUID | None = None
    entity_type: str | None = None
    entity_id: UUID | None = None
    verb: str | None = None


@dataclass(frozen=True)
class FeedChanged(Event):
    """An agent's feed on one card has grown."""

    type: ClassVar[str] = "agent.feed"
    project_id: UUID
    task_id: UUID


@dataclass(frozen=True)
class PromptChanged(Event):
    """A prompt opened or closed. May carry a permission's answer to its agent."""

    type: ClassVar[str] = "agent.prompt"
    project_id: UUID
    task_id: UUID
    prompt_id: UUID
    person_id: UUID | None = None
    client_session_id: str | None = None
    deliver: dict[str, Any] | None = None


@dataclass(frozen=True)
class AgentConnected(Event):
    """An agent socket opened for a session, so any older one lets go."""

    type: ClassVar[str] = "agent.connected"
    person_id: UUID
    client_session_id: str
    link_id: UUID


@dataclass(frozen=True)
class HooksQueued(Event):
    """This transaction queued webhook deliveries; wake the courier."""

    type: ClassVar[str] = "hooks.queued"
    wire: ClassVar[bool] = False


@dataclass
class PushQueued(Event):
    """This transaction made an agent session worth a push.

    The one event that is not frozen: it is queued when the session first
    becomes worth a push and its snapshot is brought up to date by later
    flushes of the same transaction, so the push carries where it ended.
    """

    type: ClassVar[str] = "push.agent_session"
    wire: ClassVar[bool] = False
    snapshot: Any = None


EVENTS: dict[str, type[Event]] = {
    cls.type: cls
    for cls in (BoardChanged, FeedChanged, PromptChanged, AgentConnected, HooksQueued, PushQueued)
}
"""Every event there is, by the name it travels under."""


def decode(data: dict[str, Any]) -> Event | None:
    """An event off the wire, or ``None`` for one this process does not know or
    cannot read — a newer process's event type, or a corrupt one."""
    cls = EVENTS.get(str(data.get("type")))
    if cls is None or not cls.wire:
        return None
    return cls.from_wire(data)
