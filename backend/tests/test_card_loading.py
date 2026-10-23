"""Which of a card's relationships a query brings with it.

A card's assignee, column, template, goal, waiting-on list, tick boxes and due
dates are not fetched with every card: they raise when read unloaded, and a
query for whole cards asks for them with :func:`app.models.task.card_loads`. So
a query that forgets is a loud failure in a test, not an unexplained extra
round trip in production — and these hold the lookups a response is built from
to asking.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import inspect, select
from sqlalchemy.exc import InvalidRequestError

from app.db import Database
from app.models.project import Project
from app.models.task import Task
from app.services import tasks
from tests.conftest import put_members

LOADED_WITH_A_CARD = {
    "assignee",
    "column",
    "template",
    "goal",
    "waiting_on",
    "checklist",
    "column_due_dates",
}
ALWAYS_LOADED = {"project", "parent"}
"""``reference`` is in every response a card appears in, and cannot be read without them."""


async def _board(client: AsyncClient) -> str:
    await client.post("/projects", json={"key": "ATL", "name": "Atlas"})
    person = (
        await client.post(
            "/people",
            json={"name": "Aditi K", "kind": "team", "title": "Dev", "responsibilities": "All."},
        )
    ).json()["id"]
    await put_members(client, "ATL", [person])
    parent = (
        await client.post(
            "/projects/ATL/tasks",
            json={
                "title": "A card",
                "description": "Something to do.",
                "type": "bug",
                "due_date": "2026-09-01",
                "assignee_id": person,
            },
        )
    ).json()
    await client.post(
        f"/tasks/{parent['reference']}/subtasks",
        json={
            "title": "A part",
            "description": "Something to do.",
            "type": "bug",
            "due_date": "2026-09-01",
            "assignee_id": person,
        },
    )
    return str(parent["reference"])


def unloaded(task: Task) -> set[str]:
    return set(inspect(task).unloaded) & (LOADED_WITH_A_CARD | ALWAYS_LOADED)


class TestAskingForCards:
    async def test_a_card_read_without_asking_cannot_be_read_further(
        self, signed_in: AsyncClient, database: Database
    ) -> None:
        await _board(signed_in)

        async with database.session() as session:
            bare = await session.scalar(select(Task).where(Task.number == 1))
            assert bare is not None
            with pytest.raises(InvalidRequestError, match="lazy='raise'"):
                bare.assignee  # noqa: B018 - the read is the test

    async def test_the_reference_is_always_readable(
        self, signed_in: AsyncClient, database: Database
    ) -> None:
        reference = await _board(signed_in)

        async with database.session() as session:
            bare = await session.scalar(select(Task).where(Task.parent_id.is_not(None)))
            assert bare is not None
            assert bare.reference == f"{reference}-1"

    async def test_every_lookup_a_response_is_built_from_brings_what_it_reads(
        self, signed_in: AsyncClient, database: Database
    ) -> None:
        reference = await _board(signed_in)

        async with database.session() as session:
            card = await tasks.resolve(session, reference)
            project = await session.get(Project, card.project_id)
            assert project is not None
            found = {
                "resolve": [card],
                "get": [await tasks.get(session, card.id)],
                "subtasks": await tasks.subtasks(session, card),
                "all_for_project": await tasks.all_for_project(session, project),
                "board_pages": [
                    task
                    for stack in await tasks.board_pages(session, project, limit=10)
                    for task in stack.window.rows
                ],
            }

        for lookup, cards in found.items():
            assert cards, lookup
            for task in cards:
                assert not unloaded(task), (lookup, unloaded(task))
