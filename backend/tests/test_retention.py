"""Retention: what the hourly prune takes from the tables that only grow.

CYLIST-139. Dismissed agent sessions go after a while, with their feed and
prompts; the activity trail's old rows move to ``activity_archive``. Nothing
that is still open, or still showing on a card, is ever taken.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text, update

from app.config import Settings
from app.core.clock import now
from app.db import Database
from app.models import (
    Activity,
    ActivityArchive,
    AgentFeedEntry,
    AgentFeedKind,
    AgentPrompt,
    AgentPromptKind,
    AgentPromptStatus,
    AgentSession,
    AgentSessionReason,
    AgentSessionState,
)
from app.services import hook_delivery, retention
from app.services.retention import Retention
from tests.conftest import VAULT_KEY, put_members

ATLAS = {"key": "ATL", "name": "Atlas Billing Migration"}
ADITI = {
    "name": "Aditi K",
    "kind": "team",
    "title": "Backend engineer",
    "responsibilities": "Payments and webhooks.",
}

MONTH = timedelta(days=30)
KEEP_SESSIONS = Retention(agent_sessions_for=MONTH)


async def _card(client: AsyncClient) -> str:
    await client.post("/projects", json=ATLAS)
    person = (await client.post("/people", json=ADITI)).json()["id"]
    await put_members(client, "ATL", [person])
    response = await client.post(
        "/projects/ATL/tasks",
        json={
            "title": "Dedupe webhooks",
            "description": "Duplicate deliveries create double payments.",
            "type": "bug",
            "assignee_id": person,
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["reference"])


async def _agent(client: AsyncClient) -> dict[str, str]:
    created = await client.post("/tokens", json={"name": "claude", "scopes": ["read", "write"]})
    assert created.status_code == 201, created.text
    return {"Authorization": f"Bearer {created.json()['token']}"}


async def _session(client: AsyncClient, agent: dict[str, str], ref: str, name: str) -> UUID:
    """A session reported working on the card, as a hook would, and its row id."""
    response = await client.put(
        f"/tasks/{ref}/agent-sessions/{name}", json={"state": "working"}, headers=agent
    )
    assert response.status_code == 200, response.text
    return UUID(response.json()["id"])


async def _make(
    database: Database,
    session_id: UUID,
    *,
    state: AgentSessionState,
    dismissed_ago: timedelta | None,
    ended_ago: timedelta | None = None,
) -> None:
    """Put a session in a state, as though time had passed since it got there."""
    moment = now()
    done = state is AgentSessionState.DONE
    ended = moment - (ended_ago or dismissed_ago or timedelta(0)) if done else None
    async with database.session() as session:
        await session.execute(
            update(AgentSession)
            .where(AgentSession.id == session_id)
            .values(
                state=state,
                reason=AgentSessionReason.SESSION_ENDED
                if done
                else AgentSessionReason.TURN_ENDED
                if state is AgentSessionState.WAITING
                else None,
                ended_at=ended,
                dismissed_at=moment - dismissed_ago if dismissed_ago is not None else None,
            )
        )


async def _furnish(database: Database, session_id: UUID, *, prompt: AgentPromptStatus) -> None:
    """Give a session a feed line and a prompt, so a cascade has something to take."""
    moment = now()
    async with database.session() as session:
        row = await session.get(AgentSession, session_id)
        assert row is not None
        session.add(
            AgentFeedEntry(
                agent_session_id=row.id,
                task_id=row.task_id,
                at=moment,
                kind=AgentFeedKind.RUN,
                tool="Bash",
                text="pytest -q",
            )
        )
        closed = prompt is not AgentPromptStatus.OPEN
        session.add(
            AgentPrompt(
                agent_session_id=row.id,
                task_id=row.task_id,
                kind=AgentPromptKind.QUESTION,
                text="Ship it?",
                options=[],
                status=prompt,
                closed_reason="session_ended" if prompt is AgentPromptStatus.WITHDRAWN else None,
                asked_at=moment,
                expires_at=moment + timedelta(hours=1),
                last_polled_at=moment,
                answered_at=moment if prompt is AgentPromptStatus.ANSWERED else None,
                closed_at=moment if closed else None,
            )
        )


async def _count(database: Database, model: Any, *where: Any) -> int:
    async with database.session() as session:
        return int(await session.scalar(select(func.count()).select_from(model).where(*where)) or 0)


async def _sessions(database: Database) -> set[UUID]:
    async with database.session() as session:
        return set(await session.scalars(select(AgentSession.id)))


class TestAgentSessions:
    async def test_a_session_dismissed_past_the_window_goes_with_its_feed_and_prompts(
        self, signed_in: AsyncClient, database: Database
    ) -> None:
        ref = await _card(signed_in)
        old = await _session(signed_in, await _agent(signed_in), ref, "sess-old")
        await _furnish(database, old, prompt=AgentPromptStatus.ANSWERED)
        await _make(database, old, state=AgentSessionState.DONE, dismissed_ago=MONTH * 2)

        swept = await retention.apply(database, KEEP_SESSIONS)

        assert swept.agent_sessions == 1
        assert await _sessions(database) == set()
        assert await _count(database, AgentFeedEntry) == 0
        assert await _count(database, AgentPrompt) == 0

    async def test_a_session_dismissed_within_the_window_stays(
        self, signed_in: AsyncClient, database: Database
    ) -> None:
        ref = await _card(signed_in)
        recent = await _session(signed_in, await _agent(signed_in), ref, "sess-recent")
        await _make(
            database, recent, state=AgentSessionState.DONE, dismissed_ago=timedelta(days=29)
        )

        assert (await retention.apply(database, KEEP_SESSIONS)).agent_sessions == 0
        assert await _sessions(database) == {recent}

    async def test_a_finished_session_still_on_its_card_stays_however_old(
        self, signed_in: AsyncClient, database: Database
    ) -> None:
        """Ended a year ago and never dismissed: it is still drawn on the card."""
        ref = await _card(signed_in)
        showing = await _session(signed_in, await _agent(signed_in), ref, "sess-showing")
        await _make(
            database,
            showing,
            state=AgentSessionState.DONE,
            dismissed_ago=None,
            ended_ago=timedelta(days=365),
        )

        await retention.apply(database, KEEP_SESSIONS)

        assert await _sessions(database) == {showing}

    @pytest.mark.parametrize("state", [AgentSessionState.WORKING, AgentSessionState.WAITING])
    async def test_an_open_session_is_never_taken(
        self, signed_in: AsyncClient, database: Database, state: AgentSessionState
    ) -> None:
        """Even one carrying an old dismissal, which no code path should leave
        on an open row — the rule says ``done``, not "dismissed" alone."""
        ref = await _card(signed_in)
        open_ = await _session(signed_in, await _agent(signed_in), ref, "sess-open")
        await _make(database, open_, state=state, dismissed_ago=MONTH * 12)

        await retention.apply(database, KEEP_SESSIONS)

        assert await _sessions(database) == {open_}

    async def test_a_session_holding_an_open_prompt_is_never_taken(
        self, signed_in: AsyncClient, database: Database
    ) -> None:
        ref = await _card(signed_in)
        asking = await _session(signed_in, await _agent(signed_in), ref, "sess-asking")
        await _furnish(database, asking, prompt=AgentPromptStatus.OPEN)
        await _make(database, asking, state=AgentSessionState.DONE, dismissed_ago=MONTH * 2)

        await retention.apply(database, KEEP_SESSIONS)

        assert await _sessions(database) == {asking}
        assert await _count(database, AgentPrompt) == 1

    async def test_only_the_old_ones_go_and_in_batches(
        self,
        signed_in: AsyncClient,
        database: Database,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(retention, "SESSION_BATCH", 2)
        ref = await _card(signed_in)
        agent = await _agent(signed_in)
        old = [await _session(signed_in, agent, ref, f"sess-old-{n}") for n in range(5)]
        for row in old:
            await _make(database, row, state=AgentSessionState.DONE, dismissed_ago=MONTH * 2)
        kept = await _session(signed_in, agent, ref, "sess-working")

        assert (await retention.apply(database, KEEP_SESSIONS)).agent_sessions == 5
        assert await _sessions(database) == {kept}

    async def test_a_returning_conversation_starts_afresh_after_its_row_is_gone(
        self, signed_in: AsyncClient, database: Database
    ) -> None:
        ref = await _card(signed_in)
        agent = await _agent(signed_in)
        old = await _session(signed_in, agent, ref, "sess-again")
        await _make(database, old, state=AgentSessionState.DONE, dismissed_ago=MONTH * 2)
        await retention.apply(database, KEEP_SESSIONS)

        again = await _session(signed_in, agent, ref, "sess-again")

        assert again != old
        assert await _sessions(database) == {again}

    async def test_switched_off_takes_nothing(
        self, signed_in: AsyncClient, database: Database
    ) -> None:
        ref = await _card(signed_in)
        old = await _session(signed_in, await _agent(signed_in), ref, "sess-old")
        await _make(database, old, state=AgentSessionState.DONE, dismissed_ago=MONTH * 24)

        assert await retention.apply(database, Retention()) == retention.Swept()
        assert await _sessions(database) == {old}


async def _age_activity(database: Database, by: timedelta) -> None:
    """Every trail row so far, moved back in time."""
    async with database.session() as session:
        await session.execute(update(Activity).values(occurred_at=Activity.occurred_at - by))


class TestActivity:
    async def test_old_rows_move_to_the_archive_whole(
        self, signed_in: AsyncClient, database: Database
    ) -> None:
        ref = await _card(signed_in)
        await _age_activity(database, timedelta(days=400))
        async with database.session() as session:
            before = {
                row.id: (row.verb, row.actor_label, row.channel.value, row.payload, row.occurred_at)
                for row in await session.scalars(select(Activity))
            }
        assert before
        await signed_in.patch(f"/tasks/{ref}", json={"title": "Dedupe webhook deliveries"})
        fresh = await _count(database, Activity) - len(before)
        assert fresh >= 1

        swept = await retention.apply(database, Retention(activity_for=timedelta(days=365)))

        assert swept.activity == len(before)
        assert await _count(database, Activity) == fresh
        async with database.session() as session:
            archived = {
                row.id: (row.verb, row.actor_label, row.channel, row.payload, row.occurred_at)
                for row in await session.scalars(select(ActivityArchive))
            }
        assert archived == before

    async def test_a_cards_history_keeps_what_is_inside_the_window(
        self, signed_in: AsyncClient, database: Database
    ) -> None:
        ref = await _card(signed_in)
        await _age_activity(database, timedelta(days=400))
        await signed_in.patch(f"/tasks/{ref}", json={"title": "Dedupe webhook deliveries"})

        await retention.apply(database, Retention(activity_for=timedelta(days=365)))

        history = (await signed_in.get(f"/tasks/{ref}/history")).json()
        assert [entry["verb"] for entry in history["entries"]] == ["task.updated"]

    async def test_moves_in_batches(
        self,
        signed_in: AsyncClient,
        database: Database,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(retention, "ACTIVITY_BATCH", 2)
        await _card(signed_in)
        await _age_activity(database, timedelta(days=400))
        old = await _count(database, Activity)
        assert old > 2

        swept = await retention.apply(database, Retention(activity_for=timedelta(days=365)))

        assert swept.activity == old
        assert await _count(database, ActivityArchive) == old

    async def test_switched_off_moves_nothing(
        self, signed_in: AsyncClient, database: Database
    ) -> None:
        await _card(signed_in)
        await _age_activity(database, timedelta(days=4000))

        await retention.apply(database, Retention(agent_sessions_for=MONTH))

        assert await _count(database, ActivityArchive) == 0


class TestSettings:
    def test_zero_keeps_forever(self, settings: Settings) -> None:
        kept = settings.model_copy(
            update={"agent_session_retention_days": 0, "activity_retention_days": 0}
        )
        assert Retention.of(kept) == Retention()

    def test_the_defaults_are_a_month_of_dismissed_sessions_and_a_year_of_activity(self) -> None:
        fields = Settings.model_fields
        assert fields["agent_session_retention_days"].default == 30
        assert fields["activity_retention_days"].default == 365
        defaults = Settings.model_construct()
        assert Retention.of(defaults) == Retention(
            agent_sessions_for=timedelta(days=30), activity_for=timedelta(days=365)
        )

    def test_a_negative_window_is_refused(self) -> None:
        with pytest.raises(ValueError):
            Settings(agent_session_retention_days=-1, _env_file=None)


async def test_the_couriers_hourly_prune_applies_retention(
    signed_in: AsyncClient, database: Database
) -> None:
    """The first pass of a courier prunes, so retention runs as soon as it starts."""
    ref = await _card(signed_in)
    old = await _session(signed_in, await _agent(signed_in), ref, "sess-old")
    await _make(database, old, state=AgentSessionState.DONE, dismissed_ago=MONTH * 2)
    await _age_activity(database, timedelta(days=400))

    courier = hook_delivery.Courier(Retention(agent_sessions_for=MONTH, activity_for=MONTH * 12))
    running = asyncio.create_task(
        courier.run(database, VAULT_KEY, httpx.MockTransport(lambda _: httpx.Response(200)))
    )
    try:
        for _ in range(200):
            if await _count(database, ActivityArchive) and not await _sessions(database):
                break
            await asyncio.sleep(0.02)
    finally:
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running

    assert await _sessions(database) == set()
    assert await _count(database, ActivityArchive) > 0


class TestStorageHealth:
    async def test_needs_no_credentials_and_lists_every_table(
        self, client: AsyncClient, database: Database
    ) -> None:
        assert "authorization" not in client.headers
        response = await client.get("/health/storage")

        assert response.status_code == 200, response.text
        tables = {entry["table"]: entry for entry in response.json()["tables"]}
        assert {"activity", "activity_archive", "agent_session", "agent_feed_entry"} <= set(tables)
        assert all(entry["total_bytes"] >= 0 and entry["rows"] >= 0 for entry in tables.values())

    async def test_counts_the_rows_a_table_holds(
        self, signed_in: AsyncClient, database: Database
    ) -> None:
        await _card(signed_in)
        expected = await _count(database, Activity)
        assert expected > 0
        # The statistics follow writes within moments; ANALYZE makes it now.
        async with database.session() as session:
            await session.execute(text("ANALYZE activity"))

        body = (await signed_in.get("/health/storage")).json()
        tables = {entry["table"]: entry for entry in body["tables"]}

        assert tables["activity"]["rows"] == expected
        assert tables["activity"]["total_bytes"] > 0
