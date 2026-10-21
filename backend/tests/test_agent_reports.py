"""The endings nobody reports.

A session that says goodbye writes its own last row. These are the other
cases — it simply stopped saying anything and no socket vouches for it, or
the socket that did went down with its process — and between them they are
what replaced the staleness the board used to compute on every read.

A restart is no longer one of them (CYLIST-133, CYLIST-134): starting up ends
nothing, because another process may hold the sockets, and the questions on
the board are still being answered.

Time is controlled the way the rest of this suite does it: by backdating
``last_seen_at`` and ``socket_seen_at`` with raw SQL, since there is no
clock-freezing fixture and the service reads the real one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import Database
from app.main import create_app
from app.services import agent_reports
from tests.conftest import put_members

SESSION_A = "11111111-1111-4111-8111-111111111111"
SESSION_B = "22222222-2222-4222-8222-222222222222"

QUIET = timedelta(minutes=10)
WITNESS = timedelta(minutes=6)


@pytest.fixture
async def agent(signed_in: AsyncClient) -> AsyncClient:
    """A client reporting as an agent would: a bearer token with `write`."""
    issued = await signed_in.post(
        "/tokens", json={"name": "claude code hook", "scopes": ["read", "write"]}
    )
    assert issued.status_code in (200, 201), issued.text
    signed_in.headers["Authorization"] = f"Bearer {issued.json()['token']}"
    return signed_in


async def a_card(client: AsyncClient) -> str:
    await client.post("/projects", json={"key": "ATL", "name": "Atlas", "description": ""})
    person = (
        await client.post(
            "/people",
            json={
                "name": "Aditi K",
                "kind": "team",
                "title": "Backend engineer",
                "responsibilities": "Payments.",
            },
        )
    ).json()["id"]
    await put_members(client, "ATL", [person])
    created = await client.post(
        "/projects/ATL/tasks",
        json={
            "title": "Stripe webhook idempotency",
            "description": "Dedupe on event id.",
            "type": "bug",
            "due_date": "2026-09-01",
            "assignee_id": person,
        },
    )
    assert created.status_code == 201, created.text
    return str(created.json()["reference"])


async def working(client: AsyncClient, ref: str, session_id: str) -> None:
    response = await client.put(
        f"/tasks/{ref}/agent-sessions/{session_id}",
        json={"state": "working", "client_name": ref},
    )
    assert response.status_code == 200, response.text


async def states(client: AsyncClient, ref: str) -> list[tuple[str, str | None]]:
    rows = (await client.get(f"/tasks/{ref}/agent-sessions")).json()
    return [(row["state"], row["reason"]) for row in rows]


async def silence(session: AsyncSession, client_session_id: str, *, minutes: int) -> None:
    """Backdate a row's last word, to stand in for the clock moving."""
    await session.execute(
        text("UPDATE agent_session SET last_seen_at = :when WHERE client_session_id = :sid"),
        {"when": datetime.now(UTC) - timedelta(minutes=minutes), "sid": client_session_id},
    )
    await session.commit()


async def vouched(session: AsyncSession, client_session_id: str, *, minutes_ago: int) -> None:
    """Say a socket last stamped the row this long ago."""
    await session.execute(
        text("UPDATE agent_session SET socket_seen_at = :when WHERE client_session_id = :sid"),
        {"when": datetime.now(UTC) - timedelta(minutes=minutes_ago), "sid": client_session_id},
    )
    await session.commit()


async def reap(session: AsyncSession) -> int:
    return await agent_reports.reap_unwitnessed(session, quiet_after=QUIET, witnessed_for=WITNESS)


class TestAfterARestart:
    async def test_starting_up_ends_nothing_and_withdraws_no_question(
        self, agent: AsyncClient, settings: Settings
    ) -> None:
        """A deploy used to end every open session and withdraw every question
        on the board. Now a process starting up leaves both alone: a second
        process may hold the sockets, and a person may be mid-answer."""
        ref = await a_card(agent)
        await working(agent, ref, SESSION_A)
        asked = await agent.post(f"/tasks/{ref}/agent-prompts", json={"question": "Grey?"})
        assert asked.status_code == 201, asked.text

        restarted = create_app(settings)
        async with restarted.router.lifespan_context(restarted):
            pass

        assert await states(agent, ref) == [("waiting", "question")]
        prompt = (await agent.get(f"/tasks/{ref}/agent-prompts/{asked.json()['id']}")).json()
        assert prompt["status"] == "open"

    async def test_a_session_whose_socket_died_with_its_process_is_reaped(
        self, agent: AsyncClient, session: AsyncSession
    ) -> None:
        """What the boot sweep was for, done by the reaper instead: the socket
        has stopped vouching for it, and it has been quiet since."""
        ref = await a_card(agent)
        await working(agent, ref, SESSION_A)
        await vouched(session, SESSION_A, minutes_ago=7)
        await silence(session, SESSION_A, minutes=11)

        assert await reap(session) == 1
        await session.commit()

        assert await states(agent, ref) == [("done", "connection_lost")]
        history = (await agent.get(f"/tasks/{ref}/history")).json()
        finished = [e for e in history["entries"] if e["verb"] == "agent_session.finished"]
        assert finished[0]["payload"]["cause"] == "unwitnessed"


class TestGoingQuiet:
    async def test_a_quiet_session_with_no_socket_is_ended(
        self, agent: AsyncClient, session: AsyncSession
    ) -> None:
        ref = await a_card(agent)
        await working(agent, ref, SESSION_A)
        await silence(session, SESSION_A, minutes=11)

        reaped = await reap(session)
        await session.commit()

        assert reaped == 1
        assert await states(agent, ref) == [("done", "connection_lost")]

    async def test_a_session_a_socket_vouched_for_lately_is_left_alone(
        self, agent: AsyncClient, session: AsyncSession
    ) -> None:
        """Witnessed beats quiet. One tool call can run for an hour without
        the agent having anything to say, and the socket is better evidence
        than a timestamp — whichever process holds it."""
        ref = await a_card(agent)
        await working(agent, ref, SESSION_A)
        await silence(session, SESSION_A, minutes=90)
        await vouched(session, SESSION_A, minutes_ago=1)

        assert await reap(session) == 0
        assert await states(agent, ref) == [("working", None)]

    async def test_a_session_that_spoke_recently_is_left_alone(
        self, agent: AsyncClient, session: AsyncSession
    ) -> None:
        ref = await a_card(agent)
        await working(agent, ref, SESSION_A)

        assert await reap(session) == 0
        assert await states(agent, ref) == [("working", None)]

    async def test_one_quiet_session_does_not_end_a_live_neighbour(
        self, agent: AsyncClient, session: AsyncSession
    ) -> None:
        ref = await a_card(agent)
        await working(agent, ref, SESSION_A)
        await working(agent, ref, SESSION_B)
        await silence(session, SESSION_A, minutes=11)

        assert await reap(session) == 1
        await session.commit()

        assert sorted(await states(agent, ref)) == [
            ("done", "connection_lost"),
            ("working", None),
        ]


class TestTheReapersLease:
    async def test_one_pass_at_a_time_reaps(self, database: Database) -> None:
        """Every process runs a reaper; whichever holds the lease does the
        pass, and the others find it taken until that pass commits."""
        async with database.session() as first:
            assert await agent_reports.take_reaper_lease(first)
            async with database.session() as second:
                assert not await agent_reports.take_reaper_lease(second)
        async with database.session() as third:
            assert await agent_reports.take_reaper_lease(third)
