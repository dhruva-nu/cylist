"""Two processes, one board: the event bus and the socket's stamp (CYLIST-133).

A deployment may run several processes, and a board in one may be watching a
card an agent in another is changing. Each test here stands two of them up
side by side — two hubs, each with a bus on its own connection to the real
test database — which is what two uvicorn workers are, less the HTTP.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import AgentPromptStatus, AgentSession, SessionKey
from app.realtime import protocol
from app.realtime.bus import Bus
from app.realtime.hub import AgentLink, Hub
from tests import ws
from tests.test_agent_reports import SESSION_A, a_card

HEARD_WITHIN = 2.0
"""Seconds a notification may take to cross. Milliseconds in practice."""


@asynccontextmanager
async def process(settings: Settings) -> AsyncIterator[tuple[Hub, Bus]]:
    """One process's hub and bus, its line up for as long as the block runs."""
    hub = Hub()
    bus = Bus(hub)
    running = asyncio.create_task(bus.run(settings.database_url))
    try:
        await asyncio.wait_for(bus.listening.wait(), HEARD_WITHIN)
        yield hub, bus
    finally:
        running.cancel()
        with suppress(asyncio.CancelledError):
            await running


class TestFanOut:
    async def test_a_board_in_one_process_hears_a_change_committed_in_another(
        self, settings: Settings
    ) -> None:
        project_id = uuid4()
        async with process(settings) as (_, one), process(settings) as (other_hub, _):
            with other_hub.watch(project_id) as watcher:
                one.publish({"type": "board.changed", "project_id": project_id})

                nudge = await asyncio.wait_for(watcher.next(), HEARD_WITHIN)

        assert nudge["type"] == "board.changed"

    async def test_a_process_tells_its_own_boards_once(self, settings: Settings) -> None:
        """Its own news is told locally at once, and skipped when it comes
        back over the line — or every board here would refetch twice."""
        project_id = uuid4()
        async with process(settings) as (hub, bus):
            with hub.watch(project_id) as watcher:
                bus.publish({"type": "board.changed", "project_id": project_id})
                assert (await asyncio.wait_for(watcher.next(), HEARD_WITHIN))["type"]

                # Long enough for the echo to have arrived, had it been let in.
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(watcher.next(), 0.3)

    async def test_a_permission_s_answer_reaches_the_process_holding_the_socket(
        self, settings: Settings
    ) -> None:
        key = SessionKey(uuid4(), "conversation")
        answer = protocol.answer(uuid4(), AgentPromptStatus.ANSWERED, key="deny")
        async with process(settings) as (_, one), process(settings) as (other_hub, _):
            link = AgentLink(key=key)
            other_hub.register_agent(link)

            one.publish(
                {
                    "type": protocol.PROMPT_CHANGED,
                    "project_id": uuid4(),
                    "prompt_id": uuid4(),
                    "person_id": key.person_id,
                    "client_session_id": key.client_session_id,
                    "deliver": answer,
                }
            )

            assert await asyncio.wait_for(link.outbox.get(), HEARD_WITHIN) == answer

    async def test_a_reconnection_in_one_process_displaces_the_socket_in_another(
        self, settings: Settings
    ) -> None:
        key = SessionKey(uuid4(), "conversation")
        async with process(settings) as (hub, bus), process(settings) as (other_hub, _):
            stale = AgentLink(key=key)
            other_hub.register_agent(stale)
            fresh = AgentLink(key=key)
            hub.register_agent(fresh)

            bus.publish(
                {
                    "type": protocol.AGENT_CONNECTED,
                    "person_id": key.person_id,
                    "client_session_id": key.client_session_id,
                    "link_id": fresh.id,
                }
            )

            await asyncio.wait_for(stale.displaced.wait(), HEARD_WITHIN)
            assert not fresh.displaced.is_set()


class TestTheSocketVouches:
    async def test_connecting_stamps_the_session_for_every_process_to_read(
        self, signed_in: AsyncClient, session: AsyncSession
    ) -> None:
        """What lets any process's reaper tell that the session is held."""
        ref = await a_card(signed_in)
        issued = await signed_in.post("/tokens", json={"name": "claude", "scopes": ["write"]})
        token = issued.json()["token"]
        reported = await signed_in.put(
            f"/tasks/{ref}/agent-sessions/{SESSION_A}",
            json={"state": "working"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert reported.status_code == 200, reported.text

        headers = [(b"authorization", f"Bearer {token}".encode())]
        app = signed_in.app  # type: ignore[attr-defined]
        async with ws.connect(app, f"/api/v1/agent-sessions/{SESSION_A}/ws", headers=headers) as s:
            assert (await s.receive_json())["type"] == "ready"
            stamped = await session.scalar(select(AgentSession.socket_seen_at))

        assert stamped is not None
