"""Every event there is, round-tripped, and the table that names them."""

from __future__ import annotations

import json
import typing
from dataclasses import MISSING, fields
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.realtime import events
from app.realtime.events import (
    EVENTS,
    AgentConnected,
    BoardChanged,
    Event,
    FeedChanged,
    HooksQueued,
    PromptChanged,
    PushQueued,
    decode,
)

WIRE_EVENTS = [cls for cls in EVENTS.values() if cls.wire]


def sample(cls: type[Event]) -> Event:
    """An instance with every field filled in — UUIDs as UUIDs, the rest as text or data."""
    hints = typing.get_type_hints(cls)
    values: dict[str, Any] = {}
    for field in fields(typing.cast(Any, cls)):
        hint = hints[field.name]
        if events._is_uuid(hint):
            values[field.name] = uuid4()
        elif "dict" in str(hint):
            values[field.name] = {"type": "answer", "status": "answered"}
        elif field.default is not MISSING and field.default is not None:
            values[field.name] = field.default
        else:
            values[field.name] = f"{field.name}-value"
    return cls(**values)


class TestTheTable:
    def test_every_event_is_in_it_under_its_own_name(self) -> None:
        for name, cls in EVENTS.items():
            assert cls.type == name

    def test_every_subclass_is_in_it(self) -> None:
        """A new event that is not registered would never be understood on the other end."""
        defined = {
            cls for cls in vars(events).values() if isinstance(cls, type) and issubclass(cls, Event)
        }
        assert defined - {Event} == set(EVENTS.values())

    def test_the_names_are_distinct(self) -> None:
        assert len({cls.type for cls in EVENTS.values()}) == len(EVENTS)


class TestTheWire:
    @pytest.mark.parametrize("cls", WIRE_EVENTS, ids=lambda cls: cls.type)
    def test_an_event_survives_the_wire_unchanged(self, cls: type[Event]) -> None:
        """Through JSON, as the bus sends it: a UUID that came out a string would fail here."""
        original = sample(cls)

        arrived = decode(json.loads(json.dumps(original.to_wire(), default=str)))

        assert arrived == original

    @pytest.mark.parametrize("cls", WIRE_EVENTS, ids=lambda cls: cls.type)
    def test_every_uuid_field_comes_back_a_uuid(self, cls: type[Event]) -> None:
        """The failure this exists for: a field added to an event and forgotten by the codec."""
        arrived = decode(json.loads(json.dumps(sample(cls).to_wire(), default=str)))
        assert arrived is not None
        hints = typing.get_type_hints(cls)
        for field in fields(typing.cast(Any, cls)):
            if events._is_uuid(hints[field.name]):
                assert isinstance(getattr(arrived, field.name), UUID), field.name

    def test_a_none_stays_none(self) -> None:
        arrived = decode(json.loads(json.dumps(BoardChanged(project_id=uuid4()).to_wire())))

        assert arrived == BoardChanged(project_id=arrived.project_id if arrived else None)
        assert isinstance(arrived, BoardChanged)
        assert arrived.entity_id is None

    def test_the_type_name_travels_with_it(self) -> None:
        assert FeedChanged(project_id=uuid4(), task_id=uuid4()).to_wire()["type"] == "agent.feed"


class TestWhatIsRefused:
    def test_an_unknown_type_is_not_an_event(self) -> None:
        assert decode({"type": "something.new"}) is None

    def test_an_id_that_is_not_a_uuid_refuses_the_whole_event(self) -> None:
        wire = FeedChanged(project_id=uuid4(), task_id=uuid4()).to_wire()
        wire["task_id"] = "not-a-uuid"

        assert decode(wire) is None

    def test_a_missing_required_field_refuses_it(self) -> None:
        wire = AgentConnected(person_id=uuid4(), client_session_id="c", link_id=uuid4()).to_wire()
        del wire["link_id"]

        assert decode(wire) is None

    def test_an_event_for_this_process_alone_is_never_decoded(self) -> None:
        assert decode(HooksQueued().to_wire()) is None
        assert decode(PushQueued(snapshot=None).to_wire()) is None

    def test_the_wire_knows_which_events_leave_the_process(self) -> None:
        assert not HooksQueued.wire
        assert not PushQueued.wire
        assert all(cls.wire for cls in (BoardChanged, FeedChanged, PromptChanged, AgentConnected))
