"""Matching hooks is the courier's, after the commit: what that changes and keeps."""

from __future__ import annotations

import asyncio

from httpx import AsyncClient
from sqlalchemy import func, select

from app.models.activity import Activity
from app.models.hook import HookDelivery
from app.services import hooks
from tests.test_hooks import (
    _columns,
    _comment,
    _database,
    _hook,
    _move,
    _project,
    _queued,
    _task,
)


async def _pending(client: AsyncClient) -> int:
    async with _database(client).session() as session:
        count = await session.scalar(
            select(func.count()).select_from(Activity).where(Activity.hooks_pending.is_(True))
        )
        return int(count or 0)


async def _deliveries(client: AsyncClient) -> int:
    async with _database(client).session() as session:
        count = await session.scalar(select(func.count()).select_from(HookDelivery))
        return int(count or 0)


class TestAWriteOnlyMarksItsChange:
    async def test_no_delivery_exists_until_the_courier_has_matched(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _project(signed_in)
        await _hook(signed_in, verbs=["task.created"])
        await hooks.match_pending(_database(signed_in))

        await _task(signed_in, person)

        assert await _deliveries(signed_in) == 0
        assert await _pending(signed_in) > 0

    async def test_matching_queues_the_delivery_and_clears_the_mark(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _project(signed_in)
        await _hook(signed_in, verbs=["task.created"])
        await _task(signed_in, person)

        matched = await hooks.match_pending(_database(signed_in))

        assert matched > 0
        assert await _deliveries(signed_in) == 1
        assert await _pending(signed_in) == 0

    async def test_a_second_pass_finds_nothing_and_queues_nothing_more(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _project(signed_in)
        await _hook(signed_in, verbs=["task.created"])
        await _task(signed_in, person)
        await hooks.match_pending(_database(signed_in))

        assert await hooks.match_pending(_database(signed_in)) == 0
        assert await _deliveries(signed_in) == 1

    async def test_two_couriers_at_once_queue_each_change_once(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _project(signed_in)
        await _hook(signed_in, verbs=["task.created"])
        for _ in range(5):
            await _task(signed_in, person)

        await asyncio.gather(
            hooks.match_pending(_database(signed_in)), hooks.match_pending(_database(signed_in))
        )

        assert await _deliveries(signed_in) == 5

    async def test_a_change_no_hook_could_want_is_not_marked(self, signed_in: AsyncClient) -> None:
        """Marks are for verbs hooks can name, on a project, that changed something."""
        await _project(signed_in)
        await hooks.match_pending(_database(signed_in))

        response = await signed_in.post(
            "/projects/ATL/hooks", json={"name": "H", "url": "https://x.example/h"}
        )
        assert response.status_code == 201

        async with _database(signed_in).session() as session:
            marked = list(
                await session.scalars(select(Activity.verb).where(Activity.hooks_pending.is_(True)))
            )
        assert "hook.created" not in marked


class TestWhatTheCourierSees:
    async def test_a_move_matches_the_column_it_arrived_in_even_if_the_card_has_moved_on(
        self, signed_in: AsyncClient
    ) -> None:
        """The card is read as it is when matching, so the column of a move is the
        one the change recorded — or the first move would miss a later one's column."""
        person = await _project(signed_in)
        columns = await _columns(signed_in)
        hook = await _hook(signed_in, verbs=["task.moved"], to_column_id=columns["In staging"])
        card = await _task(signed_in, person)
        await hooks.match_pending(_database(signed_in))

        await _move(signed_in, card, columns["In staging"])
        await _move(signed_in, card, columns["Done"])

        queued = await _queued(signed_in, hook["id"])
        assert len(queued) == 1
        assert queued[0].payload["to_column"]["name"] == "In staging"

    async def test_a_hook_added_afterwards_is_not_handed_what_came_before_it(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _project(signed_in)
        card = await _task(signed_in, person)
        await _comment(signed_in, card, "Before the hook.")

        late = await _hook(signed_in, name="Late")

        assert await _queued(signed_in, late["id"]) == []

    async def test_deliveries_keep_the_order_the_changes_were_made_in(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _project(signed_in)
        hook = await _hook(signed_in, verbs=["task.commented"])
        card = await _task(signed_in, person)
        for text in ("one", "two", "three"):
            await _comment(signed_in, card, text)

        queued = await _queued(signed_in, hook["id"])

        assert [delivery.payload["detail"]["comment"] for delivery in queued] == [
            "one",
            "two",
            "three",
        ]
