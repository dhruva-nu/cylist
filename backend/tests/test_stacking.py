"""Where a card sits in its column, and what putting it there costs (CYLIST-127).

A move writes the card that moved and nothing else, however tall the column;
two moves into the same column at once queue on the column's lock rather than
both taking the same key; two in opposite directions do not wait on each
other at all; and the rare spread-out of a column reads ids and positions
alone.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from itertools import pairwise
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import event, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.db import Database
from app.models.task import Task
from app.schemas.tasks import TaskMove
from app.services import columns
from app.services.tasks import lookup, moves, stacking
from tests.conftest import cards_of, put_members

ATLAS = {"key": "ATL", "name": "Atlas Billing Migration"}
ADITI = {
    "name": "Aditi K",
    "kind": "team",
    "title": "Backend engineer",
    "responsibilities": "Payments and webhooks.",
}


async def _setup(client: AsyncClient) -> str:
    await client.post("/projects", json=ATLAS)
    person = (await client.post("/people", json=ADITI)).json()["id"]
    await put_members(client, "ATL", [person])
    return str(person)


async def _create(client: AsyncClient, assignee: str, title: str) -> dict[str, Any]:
    response = await client.post(
        "/projects/ATL/tasks",
        json={
            "title": title,
            "description": "Stacked for a test.",
            "type": "chore",
            "assignee_id": assignee,
        },
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _columns(client: AsyncClient) -> list[str]:
    columns = (await client.get("/projects/ATL/columns")).json()["columns"]
    return [str(column["id"]) for column in columns]


async def _board(client: AsyncClient) -> list[dict[str, Any]]:
    return list(cards_of((await client.get("/projects/ATL/tasks")).json()))


async def _titles_in(client: AsyncClient, column_id: str) -> list[str]:
    return [task["title"] for task in await _board(client) if task["column_id"] == column_id]


async def _row_versions(database: Database) -> dict[str, str]:
    """Every card's ``xmin``: the transaction that last wrote its row.

    Any UPDATE of a row gives it a new one, even an UPDATE that sets a column
    to the value it already had — so a row whose ``xmin`` is unchanged was not
    written at all.
    """
    async with database.engine.connect() as connection:
        rows = await connection.execute(text("SELECT id::text, xmin::text FROM task"))
        return {row[0]: row[1] for row in rows}


async def _no_duplicate_positions(database: Database) -> None:
    async with database.engine.connect() as connection:
        clashes = await connection.scalar(
            text(
                "SELECT count(*) FROM (SELECT column_id, position FROM task "
                "WHERE column_id IS NOT NULL GROUP BY 1, 2 HAVING count(*) > 1) AS clash"
            )
        )
    assert clashes == 0


@contextmanager
def _statements(database: Database) -> Iterator[list[str]]:
    """Every SQL statement sent while the block runs, as text."""
    sent: list[str] = []

    def record(*args: Any) -> None:
        sent.append(str(args[2]))

    engine = database.engine.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield sent
    finally:
        event.remove(engine, "before_cursor_execute", record)


class TestOneMoveWritesOneRow:
    async def test_dropping_into_the_middle_of_a_tall_column(
        self, signed_in: AsyncClient, database: Database
    ) -> None:
        person = await _setup(signed_in)
        cards = [await _create(signed_in, person, f"Card {n}") for n in range(30)]
        todo = (await _columns(signed_in))[0]
        moving = cards[-1]
        before = await _row_versions(database)

        response = await signed_in.post(
            f"/tasks/{moving['id']}/move", json={"column_id": todo, "position": 15}
        )

        assert response.status_code == 200, response.text
        after = await _row_versions(database)
        assert [card for card in after if after[card] != before[card]] == [moving["id"]]
        titles = await _titles_in(signed_in, todo)
        assert titles[15] == "Card 29"
        assert titles[:15] == [f"Card {n}" for n in range(15)]
        assert titles[16:] == [f"Card {n}" for n in range(15, 29)]

    async def test_moving_to_another_column_leaves_both_stacks_alone(
        self, signed_in: AsyncClient, database: Database
    ) -> None:
        person = await _setup(signed_in)
        cards = [await _create(signed_in, person, f"Card {n}") for n in range(10)]
        todo, done = (await _columns(signed_in))[:2]
        for card in cards[5:]:
            await signed_in.post(
                f"/tasks/{card['id']}/move", json={"column_id": done, "position": 99}
            )
        before = await _row_versions(database)

        await signed_in.post(
            f"/tasks/{cards[0]['id']}/move", json={"column_id": done, "position": 2}
        )

        after = await _row_versions(database)
        assert [card for card in after if after[card] != before[card]] == [cards[0]["id"]]
        assert await _titles_in(signed_in, todo) == [f"Card {n}" for n in range(1, 5)]
        assert await _titles_in(signed_in, done) == [
            "Card 5",
            "Card 6",
            "Card 0",
            "Card 7",
            "Card 8",
            "Card 9",
        ]

    async def test_deleting_a_card_writes_none_of_the_others(
        self, signed_in: AsyncClient, database: Database
    ) -> None:
        person = await _setup(signed_in)
        cards = [await _create(signed_in, person, f"Card {n}") for n in range(5)]
        before = await _row_versions(database)

        await signed_in.delete(f"/tasks/{cards[0]['id']}")

        after = await _row_versions(database)
        assert after == {card: version for card, version in before.items() if card in after}
        assert len(after) == 4


class TestTwoMovesAtOnce:
    async def test_into_the_same_slot_the_second_waits_and_both_land(
        self, signed_in: AsyncClient, database: Database
    ) -> None:
        """Without the lock both would read the same two neighbours and take
        the same key between them."""
        person = await _setup(signed_in)
        for title in ("Top", "Bottom", "X", "Y"):
            await _create(signed_in, person, title)
        todo, done = (await _columns(signed_in))[:2]
        target = TaskMove.model_validate({"column_id": done, "position": 1})
        for title in ("Top", "Bottom"):
            ref = next(task for task in await _board(signed_in) if task["title"] == title)
            await signed_in.post(
                f"/tasks/{ref['id']}/move", json={"column_id": done, "position": 99}
            )

        async with database.session() as first, database.session() as second:
            x = await lookup.resolve(first, "ATL-3")
            await moves.move(first, x, target)

            y = await lookup.resolve(second, "ATL-4")
            racing = asyncio.create_task(moves.move(second, y, target))
            await asyncio.sleep(0.5)
            assert not racing.done(), "the second move should be waiting on the column"

            await first.commit()
            await racing

        await _no_duplicate_positions(database)
        assert await _titles_in(signed_in, done) == ["Top", "Y", "X", "Bottom"]
        assert await _titles_in(signed_in, todo) == []

    async def test_in_opposite_directions_neither_waits(
        self, signed_in: AsyncClient, database: Database
    ) -> None:
        """Each move locks only the column it lands in, so two crossing moves
        hold one lock each and want nothing of the other's — where rewriting
        both columns used to leave each waiting on the other."""
        person = await _setup(signed_in)
        for title in ("A", "B", "C", "D"):
            await _create(signed_in, person, title)
        todo, done = (await _columns(signed_in))[:2]
        for ref in ("ATL-3", "ATL-4"):
            await signed_in.post(f"/tasks/{ref}/move", json={"column_id": done})

        async with database.session() as first, database.session() as second:
            a = await lookup.resolve(first, "ATL-1")
            await moves.move(first, a, TaskMove.model_validate({"column_id": done}))

            c = await lookup.resolve(second, "ATL-3")
            await asyncio.wait_for(
                moves.move(second, c, TaskMove.model_validate({"column_id": todo})), timeout=5
            )

        await _no_duplicate_positions(database)
        assert await _titles_in(signed_in, todo) == ["C", "B"]
        assert await _titles_in(signed_in, done) == ["A", "D"]

    async def test_a_column_being_deleted_waits_for_a_move_into_it(
        self, signed_in: AsyncClient, database: Database
    ) -> None:
        """The placement holds the project before the column, as everything on
        a project does. When it took the column alone, a delete could take the
        project in between and then wait on the column, while the move's own
        activity entry waited on the project: a deadlock, which Postgres broke
        by failing one of the two with a 500."""
        person = await _setup(signed_in)
        await _create(signed_in, person, "Card")
        done = (await _columns(signed_in))[1]

        async with database.session() as mover, database.session() as deleter:
            card = await lookup.resolve(mover, "ATL-1")
            await moves.move(mover, card, TaskMove.model_validate({"column_id": done}))

            column = await columns.get(deleter, done)
            deleting = asyncio.create_task(columns.delete(deleter, column))
            await asyncio.sleep(0.5)
            assert not deleting.done(), "the delete should be waiting on the move's project lock"

            await mover.commit()
            with pytest.raises(ConflictError, match="Move this column's tasks"):
                await deleting

    async def test_many_at_once_over_http(self, signed_in: AsyncClient, database: Database) -> None:
        person = await _setup(signed_in)
        cards = [await _create(signed_in, person, f"Card {n}") for n in range(8)]
        done = (await _columns(signed_in))[1]

        responses = await asyncio.gather(
            *(
                signed_in.post(f"/tasks/{card['id']}/move", json={"column_id": done})
                for card in cards
            )
        )

        assert [response.status_code for response in responses] == [200] * len(cards)
        await _no_duplicate_positions(database)
        assert sorted(await _titles_in(signed_in, done)) == sorted(card["title"] for card in cards)


class TestWhenAGapRunsOut:
    async def _squeezed(self, client: AsyncClient, database: Database) -> list[str]:
        """Three cards in the first column, the top two one apart."""
        person = await _setup(client)
        for title in ("A", "B", "C"):
            await _create(client, person, title)
        async with database.session() as session:
            for reference, position in (("ATL-1", 10), ("ATL-2", 11)):
                card = await lookup.resolve(session, reference)
                card.position = position
        return await _columns(client)

    async def test_the_column_is_spread_out_and_keeps_its_order(
        self, signed_in: AsyncClient, database: Database
    ) -> None:
        todo = (await self._squeezed(signed_in, database))[0]

        await signed_in.post("/tasks/ATL-3/move", json={"column_id": todo, "position": 1})

        board = await _board(signed_in)
        assert [task["title"] for task in board] == ["A", "C", "B"]
        positions = [task["position"] for task in board]
        assert positions == sorted(set(positions))
        assert all(b - a >= 2 for a, b in pairwise(positions))

    async def test_it_holds_the_lock_and_reads_only_ids_and_positions(
        self, signed_in: AsyncClient, database: Database
    ) -> None:
        todo = (await self._squeezed(signed_in, database))[0]

        async with database.session() as session:
            card = await lookup.resolve(session, "ATL-3")
            with _statements(database) as sent:
                card.position = await stacking.slot(session, UUID(todo), index=1, moving=card.id)

        lock = next(index for index, sql in enumerate(sent) if "FOR NO KEY UPDATE" in sql)
        spread = next(index for index, sql in enumerate(sent) if "row_number()" in sql)
        assert lock < spread
        # Every card's title is a column of `task` that only a whole row brings.
        assert not any("task.title" in sql for sql in sent)

    async def test_cards_already_in_the_session_hear_their_new_keys(
        self, signed_in: AsyncClient, database: Database
    ) -> None:
        todo = (await self._squeezed(signed_in, database))[0]

        async with database.session() as session:
            top = await lookup.resolve(session, "ATL-1")
            await stacking.respace(session, UUID(todo))
            stored = await session.scalar(select(Task.position).where(Task.id == top.id))

            assert top.position == stored


class TestTheBackstop:
    async def test_two_cards_cannot_share_a_key(
        self, signed_in: AsyncClient, session: AsyncSession
    ) -> None:
        person = await _setup(signed_in)
        first = await _create(signed_in, person, "First")
        second = await _create(signed_in, person, "Second")

        with pytest.raises(IntegrityError, match="uq_task_column_id_position"):
            await session.execute(
                update(Task).where(Task.id == second["id"]).values(position=first["position"])
            )
