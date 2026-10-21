"""Whose an agent session is, and what that keeps other people out of.

An agent session used to be named by the client's own conversation id alone,
and that id was on every card. So anyone who could read a card could report as
the session, end it, take over its socket and be handed its permission answers
(CYLIST-120); and anyone who could edit the card could grant a tool on somebody
else's machine (CYLIST-141). These tests are two people on one board — the
owner with an agent, and Aditi, a teammate with an agent of her own.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import AGENT_QUIET_AFTER, AGENT_SOCKET_WITNESSES_FOR
from app.models import AgentSession, Person
from app.services import agent_prompts, agent_reports, tasks
from tests import ws
from tests.conftest import INVITEE_PASSWORD, OWNER_NAME, open_account, put_members
from tests.test_agent_reports import SESSION_A, a_card

SOCKET = f"/api/v1/agent-sessions/{SESSION_A}/ws"

ADITI = {
    "name": "Aditi K",
    "kind": "team",
    "title": "Backend engineer",
    "responsibilities": "Payments and webhooks.",
}


async def _token(client: AsyncClient) -> dict[str, str]:
    issued = await client.post("/tokens", json={"name": "claude", "scopes": ["read", "write"]})
    assert issued.status_code == 201, issued.text
    return {"Authorization": f"Bearer {issued.json()['token']}"}


@pytest.fixture
async def board(signed_in: AsyncClient, other_client: AsyncClient) -> str:
    """A card on ATL, with Aditi signed in on her own browser as a member."""
    ref = await a_card(signed_in)
    person, invitation = await open_account(signed_in, ADITI, "aditi@cylist.dev")
    accepted = await other_client.post(
        "/auth/accept-invite", json={"token": invitation, "password": INVITEE_PASSWORD}
    )
    assert accepted.status_code == 200, accepted.text
    staffed = await put_members(signed_in, "ATL", [str(person["id"])])
    assert staffed.status_code == 200, staffed.text
    return ref


@pytest.fixture
async def mine(signed_in: AsyncClient) -> dict[str, str]:
    """The owner's agent's token."""
    return await _token(signed_in)


@pytest.fixture
async def hers(other_client: AsyncClient, board: str) -> dict[str, str]:
    """Aditi's agent's token."""
    return await _token(other_client)


async def _report(
    client: AsyncClient, agent: dict[str, str], ref: str, state: str, **body: Any
) -> dict[str, Any]:
    response = await client.put(
        f"/tasks/{ref}/agent-sessions/{SESSION_A}",
        json={"state": state, "client_name": ref, **body},
        headers=agent,
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


async def _rows(client: AsyncClient, ref: str) -> list[dict[str, Any]]:
    return list((await client.get(f"/tasks/{ref}/agent-sessions")).json())


async def _open_permission(session: AsyncSession, ref: str, owner: Person) -> str:
    """A permission from the owner's session, as its socket would put it."""
    task = await tasks.resolve(session, ref)
    row = await session.scalar(
        select(AgentSession).where(
            AgentSession.task_id == task.id, AgentSession.person_id == owner.id
        )
    )
    assert row is not None and row.key is not None
    prompt_id = uuid4()
    reply = await agent_prompts.open_permission(
        session, task, row.key, prompt_id, "Running git push origin staging", "Bash"
    )
    assert reply is None
    await session.commit()
    return str(prompt_id)


class TestASessionIsItsOwners:
    async def test_the_client_s_id_is_never_shown_back(
        self, signed_in: AsyncClient, board: str, mine: dict[str, str]
    ) -> None:
        """It was all it took to impersonate the session, and the owner
        already knows it."""
        reported = await _report(signed_in, mine, board, "working")

        assert "client_session_id" not in reported
        assert all("client_session_id" not in row for row in await _rows(signed_in, board))
        history = (await signed_in.get(f"/tasks/{board}/history")).json()["entries"]
        agent_lines = [e for e in history if e["verb"].startswith("agent_session.")]
        assert agent_lines
        assert all("client_session_id" not in e["payload"] for e in agent_lines)

    async def test_another_person_s_report_under_the_same_id_is_a_session_of_their_own(
        self,
        signed_in: AsyncClient,
        other_client: AsyncClient,
        board: str,
        mine: dict[str, str],
        hers: dict[str, str],
    ) -> None:
        await _report(signed_in, mine, board, "working")

        await _report(other_client, hers, board, "done", reason="session_ended")

        rows = await _rows(signed_in, board)
        assert sorted((row["state"], row["actor_label"]) for row in rows) == [
            ("done", "claude"),
            ("working", "claude"),
        ]
        assert (await _report(signed_in, mine, board, "working"))["state"] == "working"

    async def test_another_person_s_agent_cannot_withdraw_the_question(
        self,
        signed_in: AsyncClient,
        other_client: AsyncClient,
        board: str,
        mine: dict[str, str],
        hers: dict[str, str],
    ) -> None:
        await _report(signed_in, mine, board, "working")
        asked = await signed_in.post(
            f"/tasks/{board}/agent-prompts", json={"question": "Grey?"}, headers=mine
        )
        prompt_id = asked.json()["id"]

        theirs = await other_client.post(
            f"/tasks/{board}/agent-prompts/{prompt_id}/withdraw", json={}, headers=hers
        )
        assert theirs.status_code == 404, theirs.text

        # Any token of the owner's will do: the MCP server and the hook are
        # often handed different ones.
        another_of_mine = await _token(signed_in)
        own = await signed_in.post(
            f"/tasks/{board}/agent-prompts/{prompt_id}/withdraw", json={}, headers=another_of_mine
        )
        assert own.status_code == 200, own.text
        assert own.json()["status"] == "withdrawn"

    async def test_another_person_s_socket_under_the_same_id_neither_displaces_nor_hears(
        self,
        signed_in: AsyncClient,
        other_client: AsyncClient,
        board: str,
        mine: dict[str, str],
        hers: dict[str, str],
        session: AsyncSession,
        owner: Person,
    ) -> None:
        app = signed_in.app  # type: ignore[attr-defined]
        my_headers = [(b"authorization", mine["Authorization"].encode())]
        her_headers = [(b"authorization", hers["Authorization"].encode())]

        async with ws.connect(app, SOCKET, headers=my_headers) as my_socket:
            assert (await my_socket.receive_json())["type"] == "ready"
            await my_socket.send_json(
                {
                    "type": "state",
                    "task": board,
                    "report": {"state": "waiting", "reason": "permission"},
                }
            )
            assert (await my_socket.receive_json())["type"] == "ack"
            prompt_id = await _open_permission(session, board, owner)

            async with ws.connect(app, SOCKET, headers=her_headers) as her_socket:
                assert (await her_socket.receive_json())["type"] == "ready"

                answered = await signed_in.post(
                    f"/tasks/{board}/agent-prompts/{prompt_id}/answer", json={"key": "allow_once"}
                )
                assert answered.status_code == 200, answered.text

                told = await my_socket.receive_json()
                assert (told["type"], told["key"]) == ("answer", "allow_once")
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(her_socket.receive_json(), 0.3)

                await her_socket.send_json({"type": "bye"})
                assert await her_socket.expect_close() == 1000

            # Her goodbye ended her session of that id, which she never had,
            # and not the owner's.
            await my_socket.send_json({"type": "heartbeat"})
            rows = await _rows(signed_in, board)
            assert [row["state"] for row in rows] == ["working"]


class TestWhoAnswersAPermission:
    async def test_only_the_person_whose_machine_it_is(
        self,
        signed_in: AsyncClient,
        other_client: AsyncClient,
        board: str,
        mine: dict[str, str],
        session: AsyncSession,
        owner: Person,
    ) -> None:
        await _report(signed_in, mine, board, "waiting", reason="permission")
        prompt_id = await _open_permission(session, board, owner)

        theirs = await other_client.post(
            f"/tasks/{board}/agent-prompts/{prompt_id}/answer", json={"key": "allow_once"}
        )
        assert theirs.status_code == 403, theirs.text
        assert OWNER_NAME in theirs.json()["error"]["message"]

        own = await signed_in.post(
            f"/tasks/{board}/agent-prompts/{prompt_id}/answer", json={"key": "deny"}
        )
        assert own.status_code == 200, own.text
        assert own.json()["answered_by_id"] == str(owner.id)

    async def test_a_question_is_still_anyone_s_who_may_edit_the_card(
        self,
        signed_in: AsyncClient,
        other_client: AsyncClient,
        board: str,
        mine: dict[str, str],
    ) -> None:
        await _report(signed_in, mine, board, "working")
        asked = await signed_in.post(
            f"/tasks/{board}/agent-prompts", json={"question": "Grey?"}, headers=mine
        )

        answered = await other_client.post(
            f"/tasks/{board}/agent-prompts/{asked.json()['id']}/answer", json={"text": "Grey."}
        )

        assert answered.status_code == 200, answered.text

    async def test_the_card_says_who_is_being_asked(
        self,
        signed_in: AsyncClient,
        board: str,
        mine: dict[str, str],
        session: AsyncSession,
        owner: Person,
    ) -> None:
        await _report(signed_in, mine, board, "waiting", reason="permission")
        await _open_permission(session, board, owner)

        shown = (await _rows(signed_in, board))[0]["prompt"]

        assert (shown["owner_id"], shown["owner"]) == (str(owner.id), OWNER_NAME)


class TestAQuestionBeingWaitedOn:
    """CYLIST-134: a session that is only polling for an answer is not gone."""

    async def _silence(self, session: AsyncSession, minutes: int) -> None:
        await session.execute(
            text("UPDATE agent_session SET last_seen_at = :when"),
            {"when": datetime.now(UTC) - timedelta(minutes=minutes)},
        )
        await session.commit()

    async def _reap(self, session: AsyncSession) -> int:
        reaped = await agent_reports.reap_unwitnessed(
            session, quiet_after=AGENT_QUIET_AFTER, witnessed_for=AGENT_SOCKET_WITNESSES_FOR
        )
        await session.commit()
        return reaped

    async def test_polling_for_the_answer_keeps_the_session_alive(
        self, signed_in: AsyncClient, board: str, mine: dict[str, str], session: AsyncSession
    ) -> None:
        await _report(signed_in, mine, board, "working")
        asked = await signed_in.post(
            f"/tasks/{board}/agent-prompts", json={"question": "Grey?"}, headers=mine
        )
        await self._silence(session, minutes=25)

        polled = await signed_in.get(
            f"/tasks/{board}/agent-prompts/{asked.json()['id']}?wait=1", headers=mine
        )
        assert polled.status_code == 200, polled.text

        assert await self._reap(session) == 0
        assert [row["state"] for row in await _rows(signed_in, board)] == ["waiting"]

    async def test_someone_else_polling_it_does_not(
        self,
        signed_in: AsyncClient,
        other_client: AsyncClient,
        board: str,
        mine: dict[str, str],
        hers: dict[str, str],
        session: AsyncSession,
    ) -> None:
        await _report(signed_in, mine, board, "working")
        asked = await signed_in.post(
            f"/tasks/{board}/agent-prompts", json={"question": "Grey?"}, headers=mine
        )
        await self._silence(session, minutes=25)

        await other_client.get(
            f"/tasks/{board}/agent-prompts/{asked.json()['id']}?wait=1", headers=hers
        )

        assert await self._reap(session) == 1
