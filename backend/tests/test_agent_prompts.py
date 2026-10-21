"""What an agent waits on a person for: a question it asked, or a permission.

CYLIST-85 and CYLIST-89 share one model. A question comes in over HTTP from
the ``ask_human`` tool and is long-polled; a permission comes in over the
agent's socket and its answer goes back down it. Either way a person answers
from the card, the session is freed, and the card can say what was answered.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.db import Database
from app.models import AgentSession, AgentSessionReason, AgentSessionState, Person
from app.models.activity import Channel
from app.schemas.agent_sessions import AgentPromptAnswer, AgentPromptOption, AgentPromptRead
from app.services import agent_prompts, tasks
from app.services.agent_sessions import presence
from tests import ws
from tests.test_agent_reports import SESSION_A, SESSION_B, a_card

SOCKET = f"/api/v1/agent-sessions/{SESSION_A}/ws"


@pytest.fixture
async def agent(signed_in: AsyncClient) -> dict[str, str]:
    """Headers for the agent's own token. The client stays signed in as the
    person, so one client can be both sides of a conversation."""
    issued = await signed_in.post("/tokens", json={"name": "claude", "scopes": ["read", "write"]})
    assert issued.status_code == 201, issued.text
    return {"Authorization": f"Bearer {issued.json()['token']}"}


async def _report(
    client: AsyncClient,
    agent: dict[str, str],
    ref: str,
    state: str,
    session_id: str = SESSION_A,
    **body: Any,
) -> dict[str, Any]:
    response = await client.put(
        f"/tasks/{ref}/agent-sessions/{session_id}",
        json={"state": state, "client_name": ref, **body},
        headers=agent,
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


async def _ask(client: AsyncClient, agent: dict[str, str], ref: str, **body: Any) -> dict[str, Any]:
    response = await client.post(
        f"/tasks/{ref}/agent-prompts",
        json={"question": "Grey dot, or no dot?", "options": ["Grey", "None"], **body},
        headers=agent,
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _answer(client: AsyncClient, ref: str, prompt_id: str, **body: Any) -> dict[str, Any]:
    response = await client.post(f"/tasks/{ref}/agent-prompts/{prompt_id}/answer", json=body)
    assert response.status_code == 200, response.text
    return dict(response.json())


async def _card(client: AsyncClient, ref: str) -> dict[str, Any]:
    board = (await client.get("/projects/ATL/tasks")).json()
    return next(task for task in board if task["reference"] == ref)


async def _sessions(client: AsyncClient, ref: str) -> list[dict[str, Any]]:
    return list((await client.get(f"/tasks/{ref}/agent-sessions")).json())


async def _verbs(client: AsyncClient) -> list[str]:
    feed = (await client.get("/activity")).json()
    return [entry["verb"] for entry in feed if entry["verb"].startswith("agent_session.")]


BACKDATE = {
    "expires_at": text("UPDATE agent_prompt SET expires_at = :when WHERE id = :id"),
    "last_polled_at": text("UPDATE agent_prompt SET last_polled_at = :when WHERE id = :id"),
}
"""One statement per column a test moves into the past, to stand in for the clock."""


async def _backdate(session: AsyncSession, prompt_id: str, column: str, seconds: int) -> None:
    await session.execute(
        BACKDATE[column],
        {"when": datetime.now(UTC) - timedelta(seconds=seconds), "id": prompt_id},
    )
    await session.commit()


class TestAsking:
    async def test_a_question_turns_the_card_amber_and_shows_its_choices(
        self, signed_in: AsyncClient, agent: dict[str, str]
    ) -> None:
        ref = await a_card(signed_in)
        await _report(signed_in, agent, ref, "working")

        asked = await _ask(signed_in, agent, ref)

        assert asked["kind"] == "question"
        assert asked["status"] == "open"
        assert asked["allows_reply"] is True
        assert asked["options"] == [{"key": "1", "label": "Grey"}, {"key": "2", "label": "None"}]
        card = (await _card(signed_in, ref))["agent_session"]
        assert card["state"] == "waiting"
        assert card["reason"] == "question"
        assert card["note"] == "Grey dot, or no dot?"
        assert card["prompt"]["id"] == asked["id"]
        assert await _verbs(signed_in) == ["agent_session.waiting", "agent_session.started"]

    async def test_the_trail_says_what_was_asked(
        self, signed_in: AsyncClient, agent: dict[str, str]
    ) -> None:
        ref = await a_card(signed_in)
        await _report(signed_in, agent, ref, "working")
        await _ask(signed_in, agent, ref)

        history = (await signed_in.get(f"/tasks/{ref}/history")).json()

        assert history["entries"][0]["summary"] == "The agent asked: Grey dot, or no dot?"

    async def test_nobody_would_see_a_question_from_an_unbound_session(
        self, signed_in: AsyncClient, agent: dict[str, str]
    ) -> None:
        ref = await a_card(signed_in)

        response = await signed_in.post(
            f"/tasks/{ref}/agent-prompts", json={"question": "Anyone?"}, headers=agent
        )

        assert response.status_code == 409
        assert "/work" in response.json()["error"]["message"]

    async def test_a_person_does_not_ask_through_this(self, signed_in: AsyncClient) -> None:
        ref = await a_card(signed_in)

        response = await signed_in.post(f"/tasks/{ref}/agent-prompts", json={"question": "Hm?"})

        assert response.status_code == 403

    async def test_options_must_differ(self, signed_in: AsyncClient, agent: dict[str, str]) -> None:
        ref = await a_card(signed_in)
        await _report(signed_in, agent, ref, "working")

        response = await signed_in.post(
            f"/tasks/{ref}/agent-prompts",
            json={"question": "Which?", "options": ["Same", "Same"]},
            headers=agent,
        )

        assert response.status_code == 422

    async def test_the_session_asking_is_the_one_heard_from_last(
        self, signed_in: AsyncClient, agent: dict[str, str]
    ) -> None:
        ref = await a_card(signed_in)
        await _report(signed_in, agent, ref, "working", SESSION_A, client_name="A")
        await _report(signed_in, agent, ref, "working", SESSION_B, client_name="B")

        await _ask(signed_in, agent, ref)

        by_name = {row["client_name"]: row for row in await _sessions(signed_in, ref)}
        assert by_name["B"]["reason"] == "question"
        assert by_name["A"]["state"] == "working"


class TestAnswering:
    async def test_an_answer_frees_the_agent_and_is_kept_for_the_card(
        self, signed_in: AsyncClient, agent: dict[str, str], owner: Person
    ) -> None:
        ref = await a_card(signed_in)
        await _report(signed_in, agent, ref, "working")
        asked = await _ask(signed_in, agent, ref)

        answered = await _answer(signed_in, ref, asked["id"], key="2")

        assert answered["status"] == "answered"
        assert answered["answer"] == "None"
        assert answered["answer_key"] == "2"
        assert answered["answered_by"] == owner.name
        # By id as well, so a viewer can tell "You: None" from "Ada: None".
        assert answered["answered_by_id"] == str(owner.id)
        assert answered["answered_at"] is not None
        card = (await _card(signed_in, ref))["agent_session"]
        assert card["state"] == "working"
        assert card["reason"] is None
        # Still shown, answered, until the session next changes state.
        assert card["prompt"]["status"] == "answered"
        assert card["prompt"]["answer"] == "None"
        assert card["prompt"]["answered_by_id"] == str(owner.id)
        assert (await _verbs(signed_in))[0] == "agent_session.answered"

    async def test_a_question_takes_a_typed_reply(
        self, signed_in: AsyncClient, agent: dict[str, str]
    ) -> None:
        ref = await a_card(signed_in)
        await _report(signed_in, agent, ref, "working")
        asked = await _ask(signed_in, agent, ref)

        answered = await _answer(signed_in, ref, asked["id"], text="  Grey, but lighter  ")

        assert answered["answer"] == "Grey, but lighter"
        assert answered["answer_key"] is None

    async def test_the_first_answer_wins(
        self, signed_in: AsyncClient, agent: dict[str, str]
    ) -> None:
        ref = await a_card(signed_in)
        await _report(signed_in, agent, ref, "working")
        asked = await _ask(signed_in, agent, ref)
        await _answer(signed_in, ref, asked["id"], key="1")

        again = await signed_in.post(
            f"/tasks/{ref}/agent-prompts/{asked['id']}/answer", json={"key": "2"}
        )

        assert again.status_code == 409
        assert "Already answered" in again.json()["error"]["message"]

    async def test_two_answers_at_once_the_second_waits_and_is_told(
        self, signed_in: AsyncClient, agent: dict[str, str], database: Database, owner: Person
    ) -> None:
        """The first answer holds the prompt until it commits; the second,
        arriving meanwhile, waits for it and then hears it already closed —
        rather than both getting 200 and the second overwriting the first."""
        ref = await a_card(signed_in)
        await _report(signed_in, agent, ref, "working")
        asked = await _ask(signed_in, agent, ref)
        person = Principal(
            token_id=None,
            person_id=owner.id,
            label=owner.name,
            scopes=frozenset(Scope),
            channel=Channel.WEB,
        )

        async with database.session() as first:
            task = await tasks.resolve(first, ref)
            await agent_prompts.answer(
                first, person, task, UUID(asked["id"]), AgentPromptAnswer(key="1")
            )
            second = asyncio.create_task(
                signed_in.post(
                    f"/tasks/{ref}/agent-prompts/{asked['id']}/answer", json={"key": "2"}
                )
            )
            await asyncio.sleep(0.3)
            assert not second.done(), "the second answer did not wait for the first"

        response = await second
        assert response.status_code == 409, response.text
        assert "Already answered" in response.json()["error"]["message"]
        assert (await _card(signed_in, ref))["agent_session"]["prompt"]["answer_key"] == "1"

    @pytest.mark.parametrize(
        "body", [{"key": "9"}, {}, {"key": "1", "text": "and also"}, {"text": "   "}]
    )
    async def test_an_answer_names_one_option_or_says_something(
        self, signed_in: AsyncClient, agent: dict[str, str], body: dict[str, str]
    ) -> None:
        ref = await a_card(signed_in)
        await _report(signed_in, agent, ref, "working")
        asked = await _ask(signed_in, agent, ref)

        response = await signed_in.post(
            f"/tasks/{ref}/agent-prompts/{asked['id']}/answer", json=body
        )

        assert response.status_code in (409, 422)
        assert (await _card(signed_in, ref))["agent_session"]["prompt"]["status"] == "open"

    async def test_a_prompt_is_answered_on_its_own_card(
        self, signed_in: AsyncClient, agent: dict[str, str]
    ) -> None:
        ref = await a_card(signed_in)
        await _report(signed_in, agent, ref, "working")
        asked = await _ask(signed_in, agent, ref)
        assignee = (await _card(signed_in, ref))["assignee"]["id"]
        created = await signed_in.post(
            "/projects/ATL/tasks",
            json={"title": "Other", "description": "x", "type": "chore", "assignee_id": assignee},
        )
        assert created.status_code == 201, created.text
        other = created.json()["reference"]

        response = await signed_in.post(
            f"/tasks/{other}/agent-prompts/{asked['id']}/answer", json={"key": "1"}
        )

        assert response.status_code == 404


class TestWaiting:
    async def test_a_long_poll_returns_the_moment_somebody_answers(
        self, signed_in: AsyncClient, agent: dict[str, str]
    ) -> None:
        ref = await a_card(signed_in)
        await _report(signed_in, agent, ref, "working")
        asked = await _ask(signed_in, agent, ref)
        started = time.monotonic()

        async def answer_soon() -> None:
            await asyncio.sleep(0.3)
            await _answer(signed_in, ref, asked["id"], key="1")

        polled, _ = await asyncio.gather(
            signed_in.get(f"/tasks/{ref}/agent-prompts/{asked['id']}?wait=10", headers=agent),
            answer_soon(),
        )

        assert polled.status_code == 200
        assert polled.json()["status"] == "answered"
        assert polled.json()["answer"] == "Grey"
        assert time.monotonic() - started < 5

    async def test_a_long_poll_with_nothing_to_say_says_it_is_still_open(
        self, signed_in: AsyncClient, agent: dict[str, str]
    ) -> None:
        ref = await a_card(signed_in)
        await _report(signed_in, agent, ref, "working")
        asked = await _ask(signed_in, agent, ref)

        polled = await signed_in.get(
            f"/tasks/{ref}/agent-prompts/{asked['id']}?wait=1", headers=agent
        )

        assert polled.json()["status"] == "open"

    async def test_an_unanswered_question_expires(
        self, signed_in: AsyncClient, agent: dict[str, str], session: AsyncSession
    ) -> None:
        ref = await a_card(signed_in)
        await _report(signed_in, agent, ref, "working")
        asked = await _ask(signed_in, agent, ref, timeout_seconds=60)
        await _backdate(session, asked["id"], "expires_at", 1)

        polled = await signed_in.get(
            f"/tasks/{ref}/agent-prompts/{asked['id']}?wait=1", headers=agent
        )
        late = await signed_in.post(
            f"/tasks/{ref}/agent-prompts/{asked['id']}/answer", json={"key": "1"}
        )

        assert polled.json()["status"] == "expired"
        assert late.status_code == 409
        # The tool returns saying nobody answered, and the agent carries on.
        assert (await _card(signed_in, ref))["agent_session"]["state"] == "working"
        assert "agent_session.unanswered" in await _verbs(signed_in)

    async def test_the_agent_can_stop_waiting(
        self, signed_in: AsyncClient, agent: dict[str, str]
    ) -> None:
        ref = await a_card(signed_in)
        await _report(signed_in, agent, ref, "working")
        asked = await _ask(signed_in, agent, ref)

        withdrawn = await signed_in.post(
            f"/tasks/{ref}/agent-prompts/{asked['id']}/withdraw",
            json={"reason": "interrupted"},
            headers=agent,
        )

        assert withdrawn.json()["status"] == "withdrawn"
        assert withdrawn.json()["closed_reason"] == "interrupted"
        # Interrupted: the turn is over, and the session waits on its human.
        card = (await _card(signed_in, ref))["agent_session"]
        assert (card["state"], card["reason"]) == ("waiting", "turn_ended")

    async def test_a_person_does_not_withdraw_an_agents_question(
        self, signed_in: AsyncClient, agent: dict[str, str]
    ) -> None:
        ref = await a_card(signed_in)
        await _report(signed_in, agent, ref, "working")
        asked = await _ask(signed_in, agent, ref)

        response = await signed_in.post(
            f"/tasks/{ref}/agent-prompts/{asked['id']}/withdraw", json={}
        )

        assert response.status_code == 403


class TestReportsWhileAsking:
    async def test_working_from_the_same_turn_does_not_hide_the_question(
        self, signed_in: AsyncClient, agent: dict[str, str]
    ) -> None:
        """A daemon reconnecting mid-question re-sends `working`: the tool is
        running. The card must go on saying it waits on a person."""
        ref = await a_card(signed_in)
        await _report(signed_in, agent, ref, "working")
        await _ask(signed_in, agent, ref)

        row = await _report(signed_in, agent, ref, "working", note="Calling cylist ask_human")

        assert (row["state"], row["reason"]) == ("waiting", "question")
        assert (await _card(signed_in, ref))["agent_session"]["prompt"]["status"] == "open"

    async def test_a_question_whose_tool_stopped_polling_gives_way(
        self, signed_in: AsyncClient, agent: dict[str, str], session: AsyncSession
    ) -> None:
        ref = await a_card(signed_in)
        await _report(signed_in, agent, ref, "working")
        asked = await _ask(signed_in, agent, ref)
        await _backdate(session, asked["id"], "last_polled_at", 600)

        row = await _report(signed_in, agent, ref, "working")

        assert row["state"] == "working"
        prompts = (await signed_in.get(f"/tasks/{ref}/agent-prompts")).json()
        assert (prompts[0]["status"], prompts[0]["closed_reason"]) == ("withdrawn", "abandoned")

    async def test_a_session_ending_withdraws_what_it_asked(
        self, signed_in: AsyncClient, agent: dict[str, str]
    ) -> None:
        ref = await a_card(signed_in)
        await _report(signed_in, agent, ref, "working")
        asked = await _ask(signed_in, agent, ref)

        await _report(signed_in, agent, ref, "done", reason="session_ended")

        polled = await signed_in.get(f"/tasks/{ref}/agent-prompts/{asked['id']}")
        assert (polled.json()["status"], polled.json()["closed_reason"]) == (
            "withdrawn",
            "session_ended",
        )

    async def test_the_sweep_closes_what_nobody_else_will(
        self, signed_in: AsyncClient, agent: dict[str, str], session: AsyncSession
    ) -> None:
        ref = await a_card(signed_in)
        await _report(signed_in, agent, ref, "working")
        asked = await _ask(signed_in, agent, ref)
        await _backdate(session, asked["id"], "last_polled_at", 600)

        closed = await agent_prompts.sweep(session)
        await session.commit()

        assert closed == 1
        polled = await signed_in.get(f"/tasks/{ref}/agent-prompts/{asked['id']}")
        assert polled.json()["closed_reason"] == "abandoned"


class TestTheTaskPanel:
    async def test_the_detail_and_the_list_carry_the_prompt(
        self, signed_in: AsyncClient, agent: dict[str, str]
    ) -> None:
        ref = await a_card(signed_in)
        await _report(signed_in, agent, ref, "working")
        asked = await _ask(signed_in, agent, ref)

        detail = (await signed_in.get(f"/tasks/{ref}")).json()
        listed = (await signed_in.get(f"/tasks/{ref}/agent-prompts")).json()

        assert detail["agent_session"]["prompt"]["id"] == asked["id"]
        assert detail["agent_sessions"][0]["prompt"]["id"] == asked["id"]
        assert [prompt["id"] for prompt in listed] == [asked["id"]]

    async def test_an_answer_stops_showing_once_the_session_moves_on(
        self, signed_in: AsyncClient, agent: dict[str, str]
    ) -> None:
        ref = await a_card(signed_in)
        await _report(signed_in, agent, ref, "working")
        asked = await _ask(signed_in, agent, ref)
        await _answer(signed_in, ref, asked["id"], key="1")

        await _report(signed_in, agent, ref, "waiting", reason="turn_ended")

        assert (await _card(signed_in, ref))["agent_session"]["prompt"] is None


class TestPermissionsOverTheSocket:
    async def test_a_permission_is_answered_from_the_board_and_sent_down_the_socket(
        self, signed_in: AsyncClient, agent: dict[str, str]
    ) -> None:
        ref = await a_card(signed_in)
        prompt_id = str(uuid4())
        headers = [(b"authorization", agent["Authorization"].encode())]

        async with ws.connect(signed_in.app, SOCKET, headers=headers) as socket:
            assert (await socket.receive_json())["type"] == "ready"
            await socket.send_json(
                {
                    "type": "state",
                    "task": ref,
                    "report": {
                        "state": "waiting",
                        "reason": "permission",
                        "client_name": ref,
                        "note": "Running git push origin staging",
                    },
                }
            )
            assert (await socket.receive_json())["type"] == "ack"
            await socket.send_json(
                {
                    "type": "prompt",
                    "task": ref,
                    "prompt": {
                        "id": prompt_id,
                        "kind": "permission",
                        "text": "Running git push origin staging",
                        "tool": "Bash",
                    },
                }
            )
            card = await self._wait_for_prompt(signed_in, ref)
            assert card["prompt"]["kind"] == "permission"
            assert card["prompt"]["allows_reply"] is False
            assert [o["key"] for o in card["prompt"]["options"]] == [
                "allow_once",
                "allow_session",
                "deny",
            ]

            await _answer(signed_in, ref, prompt_id, key="allow_session")
            told = await socket.receive_json()

            assert told == {
                "type": "answer",
                "prompt_id": prompt_id,
                "status": "answered",
                "key": "allow_session",
                "answer": "Allow for this session",
            }

            # A prompt re-sent after a reconnect is answered with its outcome.
            await socket.send_json(
                {
                    "type": "prompt",
                    "task": ref,
                    "prompt": {"id": prompt_id, "kind": "permission", "text": "x"},
                }
            )
            assert (await socket.receive_json())["key"] == "allow_session"

        assert (await _card(signed_in, ref))["agent_session"] is not None

    async def test_a_permission_takes_no_typed_reply(
        self, signed_in: AsyncClient, agent: dict[str, str], session: AsyncSession
    ) -> None:
        ref = await a_card(signed_in)
        await _report(signed_in, agent, ref, "waiting", reason="permission")
        prompt_id = await self._open_permission(session, ref)

        response = await signed_in.post(
            f"/tasks/{ref}/agent-prompts/{prompt_id}/answer", json={"text": "sure"}
        )

        assert response.status_code == 409

    async def test_the_terminal_answering_first_withdraws_it(
        self, signed_in: AsyncClient, agent: dict[str, str], session: AsyncSession
    ) -> None:
        ref = await a_card(signed_in)
        await _report(signed_in, agent, ref, "waiting", reason="permission")
        prompt_id = await self._open_permission(session, ref)

        # The tool ran, so its next PreToolUse reports working.
        await _report(signed_in, agent, ref, "working", note="Running ls")

        polled = (await signed_in.get(f"/tasks/{ref}/agent-prompts/{prompt_id}")).json()
        assert (polled["status"], polled["closed_reason"]) == (
            "withdrawn",
            "answered_in_terminal",
        )

    async def test_the_turn_ending_withdraws_it_and_its_own_notification_does_not(
        self, signed_in: AsyncClient, agent: dict[str, str], session: AsyncSession
    ) -> None:
        """Claude Code leaves the hook running when the terminal answers, so the
        next thing the session says is the first sign the terminal won."""
        ref = await a_card(signed_in)
        await _report(signed_in, agent, ref, "waiting", reason="permission")
        prompt_id = await self._open_permission(session, ref)

        await _report(
            signed_in, agent, ref, "waiting", reason="permission", note="Claude needs permission"
        )
        still = (await signed_in.get(f"/tasks/{ref}/agent-prompts/{prompt_id}")).json()
        await _report(signed_in, agent, ref, "waiting", reason="turn_ended")
        after = (await signed_in.get(f"/tasks/{ref}/agent-prompts/{prompt_id}")).json()

        assert still["status"] == "open"
        assert (after["status"], after["closed_reason"]) == ("withdrawn", "answered_in_terminal")

    async def test_a_withdraw_frame_closes_it(
        self, signed_in: AsyncClient, agent: dict[str, str]
    ) -> None:
        ref = await a_card(signed_in)
        prompt_id = str(uuid4())
        headers = [(b"authorization", agent["Authorization"].encode())]

        async with ws.connect(signed_in.app, SOCKET, headers=headers) as socket:
            await socket.receive_json()
            await socket.send_json(
                {
                    "type": "state",
                    "task": ref,
                    "report": {"state": "waiting", "reason": "permission", "client_name": ref},
                }
            )
            await socket.receive_json()
            await socket.send_json(
                {
                    "type": "prompt",
                    "task": ref,
                    "prompt": {"id": prompt_id, "kind": "permission", "text": "Editing a.py"},
                }
            )
            await self._wait_for_prompt(signed_in, ref)
            await socket.send_json(
                {
                    "type": "withdraw",
                    "task": ref,
                    "prompt_id": prompt_id,
                    "reason": "answered_in_terminal",
                }
            )
            for _ in range(50):
                polled = (await signed_in.get(f"/tasks/{ref}/agent-prompts/{prompt_id}")).json()
                if polled["status"] != "open":
                    break
                await asyncio.sleep(0.02)
            # The terminal said yes or no, and either way the agent carries on.
            assert (await _card(signed_in, ref))["agent_session"]["state"] == "working"

        assert polled["closed_reason"] == "answered_in_terminal"

    async def test_a_withdraw_frame_from_another_session_is_ignored(
        self, signed_in: AsyncClient, agent: dict[str, str], session: AsyncSession
    ) -> None:
        """Prompt ids are on every card; a socket only withdraws its own."""
        ref = await a_card(signed_in)
        await _report(signed_in, agent, ref, "waiting", reason="permission")
        prompt_id = await self._open_permission(session, ref)
        headers = [(b"authorization", agent["Authorization"].encode())]

        other = f"/api/v1/agent-sessions/{SESSION_B}/ws"
        async with ws.connect(signed_in.app, other, headers=headers) as socket:
            await socket.receive_json()
            await socket.send_json(
                {
                    "type": "withdraw",
                    "task": ref,
                    "prompt_id": prompt_id,
                    "reason": "answered_in_terminal",
                }
            )
            # Frames are handled in order: once this one is acknowledged, the
            # withdraw before it has been dealt with.
            await socket.send_json(
                {
                    "type": "state",
                    "task": ref,
                    "report": {"state": "working", "client_name": ref},
                }
            )
            await socket.receive_json()

        polled = (await signed_in.get(f"/tasks/{ref}/agent-prompts/{prompt_id}")).json()
        assert polled["status"] == "open"

    async def test_a_permission_nobody_answered_here_is_still_waiting_in_the_terminal(
        self, signed_in: AsyncClient, agent: dict[str, str], session: AsyncSession
    ) -> None:
        ref = await a_card(signed_in)
        await _report(signed_in, agent, ref, "waiting", reason="permission")
        prompt_id = await self._open_permission(session, ref)
        task = await tasks.resolve(session, ref)

        await agent_prompts.withdraw(session, task, UUID(prompt_id), "expired")
        await session.commit()

        card = (await _card(signed_in, ref))["agent_session"]
        assert (card["state"], card["reason"]) == ("waiting", "permission")
        assert card["prompt"]["status"] == "expired"

    async def test_a_prompt_for_a_session_not_on_the_card_is_refused_at_once(
        self, signed_in: AsyncClient, agent: dict[str, str]
    ) -> None:
        ref = await a_card(signed_in)
        prompt_id = str(uuid4())
        headers = [(b"authorization", agent["Authorization"].encode())]

        async with ws.connect(signed_in.app, SOCKET, headers=headers) as socket:
            await socket.receive_json()
            await socket.send_json(
                {
                    "type": "prompt",
                    "task": ref,
                    "prompt": {"id": prompt_id, "kind": "permission", "text": "Editing a.py"},
                }
            )
            told = await socket.receive_json()

        assert (told["type"], told["status"]) == ("answer", "withdrawn")

    async def test_an_agent_token_cannot_answer_a_permission(
        self, signed_in: AsyncClient, agent: dict[str, str], session: AsyncSession
    ) -> None:
        """The agent asking holds an API token: it must not be able to grant
        itself what Claude Code asked a person for."""
        ref = await a_card(signed_in)
        await _report(signed_in, agent, ref, "waiting", reason="permission")
        prompt_id = await self._open_permission(session, ref)

        response = await signed_in.post(
            f"/tasks/{ref}/agent-prompts/{prompt_id}/answer",
            json={"key": "allow_session"},
            headers=agent,
        )

        assert response.status_code == 403
        assert (await _card(signed_in, ref))["agent_session"]["prompt"]["status"] == "open"

    async def test_a_token_with_permit_answers_a_permission(
        self, signed_in: AsyncClient, agent: dict[str, str], session: AsyncSession
    ) -> None:
        """The phone app's token carries `permit`: a person's own device, not
        the agent's, may answer what the terminal is asking (CYLIST-104)."""
        ref = await a_card(signed_in)
        await _report(signed_in, agent, ref, "waiting", reason="permission")
        prompt_id = await self._open_permission(session, ref)
        issued = await signed_in.post(
            "/tokens", json={"name": "Pixel 9 (Android)", "scopes": ["read", "write", "permit"]}
        )
        assert issued.status_code == 201, issued.text
        phone = {"Authorization": f"Bearer {issued.json()['token']}"}

        response = await signed_in.post(
            f"/tasks/{ref}/agent-prompts/{prompt_id}/answer",
            json={"key": "deny"},
            headers=phone,
        )

        assert response.status_code == 200, response.text
        assert (response.json()["status"], response.json()["answer_key"]) == ("answered", "deny")

    async def test_the_agent_is_never_handed_permit(self, signed_in: AsyncClient) -> None:
        """What `cylist setup` mints an agent's token with leaves `permit` out."""
        described = (await signed_in.get("/setup")).json()
        assert "permit" not in described["agent_scopes"]

    async def _open_permission(self, session: AsyncSession, ref: str) -> str:
        task = await tasks.resolve(session, ref)
        # The session the agent reported, whoever's token that was.
        row = await session.scalar(
            select(AgentSession).where(
                AgentSession.task_id == task.id, AgentSession.client_session_id == SESSION_A
            )
        )
        assert row is not None and row.key is not None
        prompt_id = uuid4()
        reply = await agent_prompts.open_permission(
            session, task, row.key, prompt_id, "Running ls", "Bash"
        )
        assert reply is None
        await session.commit()
        return str(prompt_id)

    async def _wait_for_prompt(self, client: AsyncClient, ref: str) -> dict[str, Any]:
        for _ in range(50):
            card = (await _card(client, ref))["agent_session"]
            if card and card.get("prompt"):
                return dict(card)
            await asyncio.sleep(0.02)
        raise AssertionError("the prompt never reached the card")


class TestPresence:
    def _row(self, *, seen: int, reason: AgentSessionReason) -> AgentSession:
        at = datetime(2026, 10, 12, 14, 0, tzinfo=UTC)
        return AgentSession(
            id=uuid4(),
            task_id=uuid4(),
            actor_label="claude",
            client_session_id=str(uuid4()),
            state=AgentSessionState.WAITING,
            reason=reason,
            started_at=at,
            state_changed_at=at,
            last_seen_at=at + timedelta(minutes=seen),
        )

    def test_a_card_shows_the_session_it_can_answer(self) -> None:
        """Two sessions waiting: one at a turn's end, one on a question. The
        card shows the question, because that is what it can resolve."""
        asking = self._row(seen=1, reason=AgentSessionReason.QUESTION)
        idle = self._row(seen=5, reason=AgentSessionReason.TURN_ENDED)
        prompt = AgentPromptRead(
            id=uuid4(),
            agent_session_id=asking.id,
            task_id=asking.task_id,
            kind="question",
            text="Which?",
            tool=None,
            options=[AgentPromptOption(key="1", label="This")],
            allows_reply=True,
            status="open",
            answer=None,
            answer_key=None,
            answered_by=None,
            answered_by_id=None,
            owner_id=None,
            owner=None,
            answered_at=None,
            closed_reason=None,
            asked_at=asking.state_changed_at,
            expires_at=asking.state_changed_at + timedelta(minutes=30),
            closed_at=None,
        )

        shown = presence([asking, idle], {asking.id: prompt})

        assert shown is not None
        assert shown.reason == AgentSessionReason.QUESTION
        assert shown.prompt == prompt
        # Without the prompt to go on, the latest heard from decides, as before.
        unprompted = presence([asking, idle])
        assert unprompted is not None
        assert unprompted.reason == AgentSessionReason.TURN_ENDED
