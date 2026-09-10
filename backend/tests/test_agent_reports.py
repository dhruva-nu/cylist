"""The two endings nobody reports.

A session that says goodbye writes its own last row. These are the other
cases — the process restarted underneath it, or it simply stopped saying
anything and holds no socket to vouch for it — and between them they are what
replaced the staleness the board used to compute on every read.

Time is controlled the way the rest of this suite does it: by backdating
``last_seen_at`` with raw SQL, since there is no clock-freezing fixture and
the service reads the real one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import agent_reports

SESSION_A = "11111111-1111-4111-8111-111111111111"
SESSION_B = "22222222-2222-4222-8222-222222222222"

QUIET = timedelta(minutes=10)


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
                "role": "Backend engineer",
                "responsibilities": "Payments.",
            },
        )
    ).json()["id"]
    await client.put("/projects/ATL/members", json={"person_ids": [person]})
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


class TestAfterARestart:
    async def test_every_open_session_is_ended(
        self, agent: AsyncClient, session: AsyncSession
    ) -> None:
        """The boot sweep. One process, freshly started, holds no sockets —
        so nothing is running, whatever the rows say."""
        ref = await a_card(agent)
        await working(agent, ref, SESSION_A)
        await working(agent, ref, SESSION_B)

        ended = await agent_reports.end_open_sessions(session)
        await session.commit()

        assert ended == 2
        assert await states(agent, ref) == [
            ("done", "connection_lost"),
            ("done", "connection_lost"),
        ]

    async def test_a_session_already_finished_is_left_alone(
        self, agent: AsyncClient, session: AsyncSession
    ) -> None:
        """It ended for a reason it gave, and that reason is the record."""
        ref = await a_card(agent)
        await agent.put(
            f"/tasks/{ref}/agent-sessions/{SESSION_A}",
            json={"state": "done", "reason": "session_ended"},
        )

        assert await agent_reports.end_open_sessions(session) == 0
        assert await states(agent, ref) == [("done", "session_ended")]

    async def test_it_says_so_in_the_trail(self, agent: AsyncClient, session: AsyncSession) -> None:
        """A card that went grey while nobody was watching can say when."""
        ref = await a_card(agent)
        await working(agent, ref, SESSION_A)

        await agent_reports.end_open_sessions(session)
        await session.commit()

        history = (await agent.get(f"/tasks/{ref}/history")).json()
        finished = [
            entry for entry in history["entries"] if entry["verb"] == "agent_session.finished"
        ]
        assert finished, history
        assert finished[0]["payload"]["cause"] == "restart"


class TestGoingQuiet:
    async def test_a_quiet_session_with_no_socket_is_ended(
        self, agent: AsyncClient, session: AsyncSession
    ) -> None:
        ref = await a_card(agent)
        await working(agent, ref, SESSION_A)
        await silence(session, SESSION_A, minutes=11)

        reaped = await agent_reports.reap_unwitnessed(session, frozenset(), quiet_after=QUIET)
        await session.commit()

        assert reaped == 1
        assert await states(agent, ref) == [("done", "connection_lost")]

    async def test_a_session_holding_a_socket_is_left_alone(
        self, agent: AsyncClient, session: AsyncSession
    ) -> None:
        """Witnessed beats quiet. One tool call can run for an hour without
        the agent having anything to say, and the socket is better evidence
        than a timestamp."""
        ref = await a_card(agent)
        await working(agent, ref, SESSION_A)
        await silence(session, SESSION_A, minutes=90)

        reaped = await agent_reports.reap_unwitnessed(
            session, frozenset({SESSION_A}), quiet_after=QUIET
        )

        assert reaped == 0
        assert await states(agent, ref) == [("working", None)]

    async def test_a_session_that_spoke_recently_is_left_alone(
        self, agent: AsyncClient, session: AsyncSession
    ) -> None:
        ref = await a_card(agent)
        await working(agent, ref, SESSION_A)

        reaped = await agent_reports.reap_unwitnessed(session, frozenset(), quiet_after=QUIET)

        assert reaped == 0
        assert await states(agent, ref) == [("working", None)]

    async def test_one_quiet_session_does_not_end_a_live_neighbour(
        self, agent: AsyncClient, session: AsyncSession
    ) -> None:
        ref = await a_card(agent)
        await working(agent, ref, SESSION_A)
        await working(agent, ref, SESSION_B)
        await silence(session, SESSION_A, minutes=11)

        assert await agent_reports.reap_unwitnessed(session, frozenset(), quiet_after=QUIET) == 1
        await session.commit()

        assert sorted(await states(agent, ref)) == [
            ("done", "connection_lost"),
            ("working", None),
        ]
