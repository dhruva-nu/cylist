"""Agent sessions: what a hook may say about a card, and what the card says back."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentSession, AgentSessionReason, AgentSessionState
from app.services.agent_sessions import presence

ATLAS = {"key": "ATL", "name": "Atlas Billing Migration"}
ADITI = {
    "name": "Aditi K",
    "kind": "team",
    "role": "Backend engineer",
    "responsibilities": "Payments and webhooks.",
}

SESSION_A = "sess-aaaa"
SESSION_B = "sess-bbbb"


async def _setup(client: AsyncClient) -> str:
    await client.post("/projects", json=ATLAS)
    person = (await client.post("/people", json=ADITI)).json()["id"]
    await client.put("/projects/ATL/members", json={"person_ids": [person]})
    return str(person)


async def _card(client: AsyncClient, assignee: str, title: str = "Dedupe webhooks") -> str:
    response = await client.post(
        "/projects/ATL/tasks",
        json={
            "title": title,
            "description": "Duplicate deliveries create double payments.",
            "type": "bug",
            "assignee_id": assignee,
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["reference"])


async def _agent(client: AsyncClient, *scopes: str, name: str = "claude") -> dict[str, str]:
    """Headers for a fresh API token — the credential a hook reports with."""
    created = await client.post(
        "/tokens", json={"name": name, "scopes": list(scopes) or ["read", "write"]}
    )
    assert created.status_code == 201, created.text
    return {"Authorization": f"Bearer {created.json()['token']}"}


async def _put(
    client: AsyncClient,
    agent: dict[str, str],
    ref: str,
    state: str,
    session_id: str = SESSION_A,
    **body: Any,
) -> dict[str, Any]:
    response = await client.put(
        f"/tasks/{ref}/agent-sessions/{session_id}",
        json={"state": state, **body},
        headers=agent,
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


async def _card_read(client: AsyncClient, ref: str) -> dict[str, Any]:
    board = (await client.get("/projects/ATL/tasks")).json()
    return next(task for task in board if task["reference"] == ref)


async def _agent_activity(client: AsyncClient) -> list[str]:
    feed = (await client.get("/activity")).json()
    return [entry["verb"] for entry in feed if entry["verb"].startswith("agent_session.")]


class TestReporting:
    async def test_the_first_report_creates_a_row(self, signed_in: AsyncClient) -> None:
        agent = await _agent(signed_in)
        ref = await _card(signed_in, await _setup(signed_in))

        row = await _put(signed_in, agent, ref, "working", client_name="ATL-1")

        assert row["state"] == "working"
        assert row["reason"] is None
        assert row["client_session_id"] == SESSION_A
        assert row["client_name"] == "ATL-1"
        assert row["actor_label"] == "claude"
        assert row["ended_at"] is None
        assert row["is_stale"] is False

    async def test_a_repeated_report_is_a_heartbeat_and_not_history(
        self, signed_in: AsyncClient
    ) -> None:
        agent = await _agent(signed_in)
        ref = await _card(signed_in, await _setup(signed_in))
        first = await _put(signed_in, agent, ref, "working")

        second = await _put(signed_in, agent, ref, "working")

        assert second["id"] == first["id"]
        assert second["last_seen_at"] >= first["last_seen_at"]
        assert second["state_changed_at"] == first["state_changed_at"]
        # The first report was a transition; the second must not have been.
        assert await _agent_activity(signed_in) == ["agent_session.started"]

    async def test_a_change_of_state_writes_exactly_one_line(self, signed_in: AsyncClient) -> None:
        agent = await _agent(signed_in)
        ref = await _card(signed_in, await _setup(signed_in))
        await _put(signed_in, agent, ref, "working")

        waiting = await _put(signed_in, agent, ref, "waiting", reason="permission")

        assert waiting["state"] == "waiting"
        assert waiting["reason"] == "permission"
        verbs = await _agent_activity(signed_in)
        assert verbs.count("agent_session.waiting") == 1
        assert verbs[0] == "agent_session.waiting"  # newest first

        history = (await signed_in.get(f"/tasks/{ref}/history")).json()["entries"]
        assert history[0]["summary"] == "The agent is waiting for permission to run something."
        assert history[0]["channel"] == "api"

    async def test_going_back_to_work_after_a_turn_is_not_history(
        self, signed_in: AsyncClient
    ) -> None:
        """Every turn ends and every prompt starts one; the trail is not for that."""
        agent = await _agent(signed_in)
        ref = await _card(signed_in, await _setup(signed_in))
        await _put(signed_in, agent, ref, "working")
        await _put(signed_in, agent, ref, "waiting", reason="turn_ended")

        await _put(signed_in, agent, ref, "working")

        assert await _agent_activity(signed_in) == [
            "agent_session.waiting",
            "agent_session.started",
        ]

    async def test_done_ends_the_row_and_working_reopens_the_same_one(
        self, signed_in: AsyncClient
    ) -> None:
        agent = await _agent(signed_in)
        ref = await _card(signed_in, await _setup(signed_in))
        opened = await _put(signed_in, agent, ref, "working")

        done = await _put(signed_in, agent, ref, "done", reason="session_ended")
        assert done["state"] == "done"
        assert done["reason"] == "session_ended"
        assert done["ended_at"] is not None

        back = await _put(signed_in, agent, ref, "working")
        assert back["id"] == opened["id"]
        assert back["state"] == "working"
        assert back["ended_at"] is None
        assert back["reason"] is None
        assert await _agent_activity(signed_in) == [
            "agent_session.started",
            "agent_session.finished",
            "agent_session.started",
        ]

    async def test_a_web_session_is_not_an_agent(self, signed_in: AsyncClient) -> None:
        ref = await _card(signed_in, await _setup(signed_in))

        response = await signed_in.put(
            f"/tasks/{ref}/agent-sessions/{SESSION_A}", json={"state": "working"}
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden"

    async def test_a_read_only_token_cannot_report(self, signed_in: AsyncClient) -> None:
        reader = await _agent(signed_in, "read", name="reader")
        ref = await _card(signed_in, await _setup(signed_in))

        response = await signed_in.put(
            f"/tasks/{ref}/agent-sessions/{SESSION_A}", json={"state": "working"}, headers=reader
        )

        assert response.status_code == 403
        assert response.json()["error"]["details"]["missing_scopes"] == ["write"]

    async def test_an_unknown_task_is_a_clean_404(self, signed_in: AsyncClient) -> None:
        agent = await _agent(signed_in)
        await _setup(signed_in)

        response = await signed_in.put(
            "/tasks/ATL-99/agent-sessions/x", json={"state": "working"}, headers=agent
        )

        assert response.status_code == 404


class TestOneCardAtATime:
    async def test_binding_to_a_second_card_ends_the_row_on_the_first(
        self, signed_in: AsyncClient
    ) -> None:
        agent = await _agent(signed_in)
        person = await _setup(signed_in)
        first = await _card(signed_in, person, "First")
        second = await _card(signed_in, person, "Second")
        await _put(signed_in, agent, first, "working")

        await _put(signed_in, agent, second, "working")

        on_first = (await signed_in.get(f"/tasks/{first}/agent-sessions")).json()
        assert [(row["state"], row["reason"]) for row in on_first] == [("done", "moved")]
        on_second = (await signed_in.get(f"/tasks/{second}/agent-sessions")).json()
        assert [row["state"] for row in on_second] == ["working"]

        history = (await signed_in.get(f"/tasks/{first}/history")).json()["entries"]
        assert history[0]["summary"] == f"The agent moved on to {second}."

    async def test_two_sessions_on_one_card_are_two_rows(self, signed_in: AsyncClient) -> None:
        agent = await _agent(signed_in)
        ref = await _card(signed_in, await _setup(signed_in))

        await _put(signed_in, agent, ref, "working", SESSION_A)
        await _put(signed_in, agent, ref, "working", SESSION_B)

        rows = (await signed_in.get(f"/tasks/{ref}/agent-sessions")).json()
        assert sorted(row["client_session_id"] for row in rows) == [SESSION_A, SESSION_B]


class TestWhatTheCardSays:
    async def test_a_card_with_no_agent_says_so(self, signed_in: AsyncClient) -> None:
        ref = await _card(signed_in, await _setup(signed_in))

        card = await _card_read(signed_in, ref)

        assert card["agent_session"] is None
        detail = (await signed_in.get(f"/tasks/{ref}")).json()
        assert detail["agent_session"] is None
        assert detail["agent_sessions"] == []

    async def test_waiting_beats_working(self, signed_in: AsyncClient) -> None:
        agent = await _agent(signed_in)
        ref = await _card(signed_in, await _setup(signed_in))
        await _put(signed_in, agent, ref, "working", SESSION_A, client_name="one")
        await _put(signed_in, agent, ref, "waiting", SESSION_B, reason="idle", client_name="two")

        card = await _card_read(signed_in, ref)

        assert card["agent_session"]["state"] == "waiting"
        assert card["agent_session"]["reason"] == "idle"
        assert card["agent_session"]["client_name"] == "two"
        assert card["agent_session"]["count"] == 2

    async def test_working_beats_done(self, signed_in: AsyncClient) -> None:
        agent = await _agent(signed_in)
        ref = await _card(signed_in, await _setup(signed_in))
        await _put(signed_in, agent, ref, "done", SESSION_A)
        await _put(signed_in, agent, ref, "working", SESSION_B)

        card = await _card_read(signed_in, ref)

        assert card["agent_session"]["state"] == "working"
        assert card["agent_session"]["count"] == 1

    async def test_all_finished_is_done_until_dismissed(self, signed_in: AsyncClient) -> None:
        agent = await _agent(signed_in)
        ref = await _card(signed_in, await _setup(signed_in))
        await _put(signed_in, agent, ref, "working")
        await _put(signed_in, agent, ref, "done", reason="session_ended")

        assert (await _card_read(signed_in, ref))["agent_session"]["state"] == "done"

        dismissed = await signed_in.post(f"/tasks/{ref}/agent-sessions/dismiss")
        assert dismissed.status_code == 204

        assert (await _card_read(signed_in, ref))["agent_session"] is None
        assert (await signed_in.get(f"/tasks/{ref}/agent-sessions")).json() == []

    async def test_a_silent_worker_goes_stale(
        self, signed_in: AsyncClient, session: AsyncSession
    ) -> None:
        agent = await _agent(signed_in)
        ref = await _card(signed_in, await _setup(signed_in))
        await _put(signed_in, agent, ref, "working")
        await _silence(session, SESSION_A, minutes=11)

        card = await _card_read(signed_in, ref)

        assert card["agent_session"]["state"] == "stale"
        detail = (await signed_in.get(f"/tasks/{ref}")).json()
        assert detail["agent_sessions"][0]["is_stale"] is True

    async def test_a_stale_worker_is_ignored_while_another_is_live(
        self, signed_in: AsyncClient, session: AsyncSession
    ) -> None:
        agent = await _agent(signed_in)
        ref = await _card(signed_in, await _setup(signed_in))
        await _put(signed_in, agent, ref, "working", SESSION_A)
        await _put(signed_in, agent, ref, "working", SESSION_B)
        await _silence(session, SESSION_A, minutes=11)

        card = await _card_read(signed_in, ref)

        assert card["agent_session"]["state"] == "working"
        assert card["agent_session"]["client_name"] is None
        assert card["agent_session"]["count"] == 2

    async def test_a_goals_page_draws_the_same_card(self, signed_in: AsyncClient) -> None:
        agent = await _agent(signed_in)
        person = await _setup(signed_in)
        goal = await signed_in.post(
            "/projects/ATL/goals",
            json={"name": "Ledger cutover", "description": "All of it.", "owner_id": person},
        )
        assert goal.status_code == 201, goal.text
        ref = await _card(signed_in, person)
        await signed_in.patch(f"/tasks/{ref}", json={"goal_id": goal.json()["id"]})
        await _put(signed_in, agent, ref, "working")

        page = (await signed_in.get(f"/goals/{goal.json()['reference']}")).json()

        assert page["tasks"][0]["agent_session"]["state"] == "working"


async def _silence(session: AsyncSession, client_session_id: str, *, minutes: int) -> None:
    """Pretend nothing has been heard from a session for a while."""
    await session.execute(
        update(AgentSession)
        .where(AgentSession.client_session_id == client_session_id)
        .values(last_seen_at=datetime.now(UTC) - timedelta(minutes=minutes))
    )
    await session.commit()


class TestTouchingTheCard:
    async def test_a_persons_edit_dismisses_finished_sessions(self, signed_in: AsyncClient) -> None:
        agent = await _agent(signed_in)
        ref = await _card(signed_in, await _setup(signed_in))
        await _put(signed_in, agent, ref, "working")
        await _put(signed_in, agent, ref, "done")
        assert (await _card_read(signed_in, ref))["agent_session"]["state"] == "done"

        edited = await signed_in.patch(f"/tasks/{ref}", json={"title": "Renamed by hand"})
        assert edited.status_code == 200

        assert (await _card_read(signed_in, ref))["agent_session"] is None

    async def test_a_persons_comment_leaves_a_live_session_alone(
        self, signed_in: AsyncClient
    ) -> None:
        agent = await _agent(signed_in)
        ref = await _card(signed_in, await _setup(signed_in))
        await _put(signed_in, agent, ref, "waiting", reason="turn_ended")

        await signed_in.post(f"/tasks/{ref}/comments", json={"body": "Looking at it."})

        assert (await _card_read(signed_in, ref))["agent_session"]["state"] == "waiting"

    async def test_an_agents_own_write_is_a_heartbeat(
        self, signed_in: AsyncClient, session: AsyncSession
    ) -> None:
        agent = await _agent(signed_in)
        ref = await _card(signed_in, await _setup(signed_in))
        await _put(signed_in, agent, ref, "working")
        await _silence(session, SESSION_A, minutes=11)
        assert (await _card_read(signed_in, ref))["agent_session"]["state"] == "stale"

        commented = await signed_in.post(
            f"/tasks/{ref}/comments", json={"body": "Found it."}, headers=agent
        )
        assert commented.status_code == 201

        assert (await _card_read(signed_in, ref))["agent_session"]["state"] == "working"

    async def test_another_agents_write_is_not(
        self, signed_in: AsyncClient, session: AsyncSession
    ) -> None:
        agent = await _agent(signed_in, name="claude")
        other = await _agent(signed_in, name="codex")
        ref = await _card(signed_in, await _setup(signed_in))
        await _put(signed_in, agent, ref, "working")
        await _silence(session, SESSION_A, minutes=11)

        await signed_in.post(f"/tasks/{ref}/comments", json={"body": "Hi."}, headers=other)

        assert (await _card_read(signed_in, ref))["agent_session"]["state"] == "stale"

    async def test_deleting_the_card_takes_its_sessions(
        self, signed_in: AsyncClient, session: AsyncSession
    ) -> None:
        agent = await _agent(signed_in)
        ref = await _card(signed_in, await _setup(signed_in))
        await _put(signed_in, agent, ref, "working")

        assert (await signed_in.delete(f"/tasks/{ref}")).status_code == 200

        left = await session.scalars(select(AgentSession))
        assert list(left) == []


# --- The pure part ----------------------------------------------------------

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
STALE_AFTER = timedelta(minutes=10)


def _row(
    state: AgentSessionState,
    *,
    seen_ago: timedelta = timedelta(0),
    reason: AgentSessionReason | None = None,
    ended: bool = False,
    dismissed: bool = False,
    name: str | None = None,
) -> AgentSession:
    seen = NOW - seen_ago
    return AgentSession(
        id=uuid4(),
        task_id=uuid4(),
        token_id=None,
        actor_label="claude",
        client_session_id=str(uuid4()),
        client_name=name,
        state=state,
        reason=reason,
        started_at=seen - timedelta(minutes=30),
        state_changed_at=seen,
        last_seen_at=seen,
        ended_at=seen if ended else None,
        dismissed_at=seen if dismissed else None,
    )


class TestPresence:
    def test_nothing_from_nothing(self) -> None:
        assert presence([], NOW, STALE_AFTER) is None

    def test_waiting_beats_working(self) -> None:
        rows = [
            _row(AgentSessionState.WORKING, name="busy"),
            _row(AgentSessionState.WAITING, reason=AgentSessionReason.PERMISSION, name="asking"),
        ]
        found = presence(rows, NOW, STALE_AFTER)
        assert found is not None
        assert found.state == "waiting"
        assert found.reason is AgentSessionReason.PERMISSION
        assert found.client_name == "asking"
        assert found.count == 2

    def test_working_beats_done(self) -> None:
        rows = [_row(AgentSessionState.DONE, ended=True), _row(AgentSessionState.WORKING)]
        found = presence(rows, NOW, STALE_AFTER)
        assert found is not None
        assert found.state == "working"
        assert found.count == 1

    def test_all_ended_is_done(self) -> None:
        rows = [
            _row(AgentSessionState.DONE, ended=True, seen_ago=timedelta(hours=1)),
            _row(AgentSessionState.DONE, ended=True, name="latest"),
        ]
        found = presence(rows, NOW, STALE_AFTER)
        assert found is not None
        assert found.state == "done"
        assert found.count == 2
        assert found.client_name == "latest"

    def test_a_silent_worker_is_stale(self) -> None:
        rows = [_row(AgentSessionState.WORKING, seen_ago=timedelta(minutes=11))]
        found = presence(rows, NOW, STALE_AFTER)
        assert found is not None
        assert found.state == "stale"

    def test_exactly_at_the_threshold_is_still_working(self) -> None:
        rows = [_row(AgentSessionState.WORKING, seen_ago=STALE_AFTER)]
        found = presence(rows, NOW, STALE_AFTER)
        assert found is not None
        assert found.state == "working"

    def test_stale_is_ignored_while_another_is_live(self) -> None:
        rows = [
            _row(AgentSessionState.WORKING, seen_ago=timedelta(minutes=11), name="crashed"),
            _row(AgentSessionState.WORKING, name="alive"),
        ]
        found = presence(rows, NOW, STALE_AFTER)
        assert found is not None
        assert found.state == "working"
        assert found.client_name == "alive"
        assert found.count == 2

    def test_stale_beside_done_is_stale_not_done(self) -> None:
        """A row that is open, however quiet, is not over."""
        rows = [
            _row(AgentSessionState.WORKING, seen_ago=timedelta(minutes=11)),
            _row(AgentSessionState.DONE, ended=True),
        ]
        found = presence(rows, NOW, STALE_AFTER)
        assert found is not None
        assert found.state == "stale"

    def test_a_waiting_session_never_goes_stale(self) -> None:
        """Nothing fires while a human is away; silence is what waiting looks like."""
        rows = [_row(AgentSessionState.WAITING, seen_ago=timedelta(hours=3))]
        found = presence(rows, NOW, STALE_AFTER)
        assert found is not None
        assert found.state == "waiting"

    def test_dismissed_rows_are_not_counted(self) -> None:
        rows = [_row(AgentSessionState.DONE, ended=True, dismissed=True)]
        assert presence(rows, NOW, STALE_AFTER) is None

    def test_since_is_the_state_change_not_the_heartbeat(self) -> None:
        row = _row(AgentSessionState.WORKING)
        row.state_changed_at = NOW - timedelta(minutes=5)
        found = presence([row], NOW, STALE_AFTER)
        assert found is not None
        assert found.since == NOW - timedelta(minutes=5)
        assert found.last_seen_at == NOW
