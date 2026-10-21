"""Every list is read a page at a time (CYLIST-130).

A board used to answer with every card it held, descriptions and all, and the
other lists with every row they had. Now each is a page of at most 100 rows
and a cursor — the last row's id — for the next, and the board is a page per
column. What these tests hold the server to: that walking a list by its
cursors visits every row exactly once and in the list's order; that the cap
cannot be talked past; and that a cursor whose row has left the list is said
to be stale rather than quietly skipping or repeating rows.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.services.paging import MAX_LIMIT

ATLAS = {"key": "ATL", "name": "Atlas Billing Migration"}


async def _atlas(client: AsyncClient) -> None:
    created = await client.post("/projects", json=ATLAS)
    assert created.status_code == 201, created.text


async def _card(client: AsyncClient, title: str) -> dict[str, Any]:
    response = await client.post(
        "/projects/ATL/tasks",
        json={"title": title, "description": f"All about {title}.", "type": "chore"},
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _columns(client: AsyncClient) -> list[dict[str, Any]]:
    return list((await client.get("/projects/ATL/columns")).json()["columns"])


async def _walk(client: AsyncClient, path: str, **params: Any) -> list[dict[str, Any]]:
    """Every row of a list, read by following its cursors to the end."""
    rows: list[dict[str, Any]] = []
    after: str | None = None
    while True:
        query = {**params, **({"after": after} if after else {})}
        response = await client.get(path, params=query)
        assert response.status_code == 200, response.text
        page = response.json()
        rows.extend(page["items"])
        after = page["next"]
        if after is None:
            return rows


class TestEveryList:
    """Proved off the OpenAPI document, so a list added later is held to it too."""

    @staticmethod
    def _gets(app: FastAPI) -> list[tuple[str, dict[str, Any]]]:
        return [
            (path, operation["get"])
            for path, operation in app.openapi()["paths"].items()
            if "get" in operation
        ]

    def test_every_page_is_read_by_a_capped_cursor(self, app: FastAPI) -> None:
        paged = []
        for path, get in self._gets(app):
            schema = get["responses"]["200"]["content"].get("application/json", {}).get("schema")
            if not schema or not schema.get("$ref", "").endswith("Page"):
                continue
            if path.endswith("/history"):
                continue  # numbered pages, for a pager that says "page 2 of 9"
            params = {param["name"]: param for param in get.get("parameters", [])}
            assert "after" in params, path
            assert params["limit"]["schema"]["maximum"] == MAX_LIMIT, path
            paged.append(path)
        assert len(paged) >= 20

    def test_no_list_answers_with_a_bare_array(self, app: FastAPI) -> None:
        # The lists that are not paged, and why: each is bounded by something
        # other than the size of the data.
        bounded = {
            # A constant, the verbs a hook can listen for.
            "/api/v1/hooks/events",
            # At most ten: older sessions are ended as new ones are made.
            "/api/v1/me/sessions",
        }
        bare = {
            path
            for path, get in self._gets(app)
            if get["responses"]["200"]["content"]
            .get("application/json", {})
            .get("schema", {})
            .get("type")
            == "array"
        }
        assert bare <= bounded, sorted(bare - bounded)

    async def test_a_limit_over_the_cap_is_refused(self, signed_in: AsyncClient) -> None:
        await _atlas(signed_in)

        for path in ("/people", "/projects", "/projects/ATL/tasks", "/activity"):
            response = await signed_in.get(path, params={"limit": MAX_LIMIT + 1})
            assert response.status_code == 422, path


class TestTheBoard:
    async def test_is_a_page_per_column_without_descriptions(self, signed_in: AsyncClient) -> None:
        await _atlas(signed_in)
        for number in range(5):
            await _card(signed_in, f"Card {number}")

        board = (await signed_in.get("/projects/ATL/tasks", params={"limit": 2})).json()

        first, *rest = board["columns"]
        assert [column["column_id"] for column in board["columns"]] == [
            column["id"] for column in await _columns(signed_in)
        ], "every column, empty ones included, left to right"
        assert first["total"] == 5
        assert [card["title"] for card in first["items"]] == ["Card 0", "Card 1"]
        assert first["next"] == first["items"][-1]["id"]
        assert all(column["items"] == [] and column["next"] is None for column in rest)
        assert all("description" not in card for card in first["items"])

    async def test_a_column_is_read_to_the_end_by_its_cursor(self, signed_in: AsyncClient) -> None:
        await _atlas(signed_in)
        for number in range(7):
            await _card(signed_in, f"Card {number}")
        board = (await signed_in.get("/projects/ATL/tasks", params={"limit": 3})).json()
        column = board["columns"][0]

        rest = await _walk(
            signed_in, f"/columns/{column['column_id']}/tasks", after=column["next"], limit=3
        )

        titles = [card["title"] for card in column["items"] + rest]
        assert titles == [f"Card {number}" for number in range(7)]

    async def test_a_cursor_whose_card_left_the_column_is_stale(
        self, signed_in: AsyncClient
    ) -> None:
        await _atlas(signed_in)
        for number in range(3):
            await _card(signed_in, f"Card {number}")
        board = (await signed_in.get("/projects/ATL/tasks", params={"limit": 1})).json()
        column = board["columns"][0]
        await signed_in.delete(f"/tasks/{column['items'][0]['reference']}")

        response = await signed_in.get(
            f"/columns/{column['column_id']}/tasks", params={"after": column["next"]}
        )

        assert response.status_code == 422
        assert response.json()["error"]["details"] == {"after": column["next"]}

    async def test_a_card_moved_up_after_the_cursor_is_not_read_twice(
        self, signed_in: AsyncClient
    ) -> None:
        await _atlas(signed_in)
        cards = [await _card(signed_in, f"Card {number}") for number in range(4)]
        board = (await signed_in.get("/projects/ATL/tasks", params={"limit": 2})).json()
        column = board["columns"][0]
        # The last card jumps to the top: already "read", as far as a cursor
        # placed after card 1 is concerned.
        await signed_in.post(
            f"/tasks/{cards[3]['reference']}/move",
            json={"column_id": column["column_id"], "position": 0},
        )

        rest = await _walk(signed_in, f"/columns/{column['column_id']}/tasks", after=column["next"])

        assert [card["title"] for card in rest] == ["Card 2"]

    async def test_the_full_card_still_has_its_description(self, signed_in: AsyncClient) -> None:
        await _atlas(signed_in)
        card = await _card(signed_in, "Card")

        detail = (await signed_in.get(f"/tasks/{card['reference']}")).json()

        assert detail["description"] == "All about Card."


class TestTheTimeline:
    async def test_a_card_carries_its_newest_comments_and_where_the_rest_begin(
        self, signed_in: AsyncClient
    ) -> None:
        await _atlas(signed_in)
        card = await _card(signed_in, "Chatty")
        for number in range(55):
            await signed_in.post(
                f"/tasks/{card['reference']}/comments", json={"body": f"#{number}"}
            )

        detail = (await signed_in.get(f"/tasks/{card['reference']}")).json()

        assert detail["comment_count"] == 55
        assert [entry["body"] for entry in detail["comments"]] == [f"#{n}" for n in range(5, 55)]
        assert detail["comments_next"] == detail["comments"][0]["id"]

        earlier = await _walk(
            signed_in, f"/tasks/{card['reference']}/comments", after=detail["comments_next"]
        )
        assert [entry["body"] for entry in earlier] == [f"#{n}" for n in range(4, -1, -1)]

    async def test_reads_newest_first_with_its_length(self, signed_in: AsyncClient) -> None:
        await _atlas(signed_in)
        card = await _card(signed_in, "Chatty")
        for number in range(3):
            await signed_in.post(
                f"/tasks/{card['reference']}/comments", json={"body": f"#{number}"}
            )

        page = (
            await signed_in.get(f"/tasks/{card['reference']}/comments", params={"limit": 2})
        ).json()

        assert [entry["body"] for entry in page["items"]] == ["#2", "#1"]
        assert page["total"] == 3
        assert page["next"] == page["items"][-1]["id"]

    async def test_a_short_timeline_has_nothing_before_it(self, signed_in: AsyncClient) -> None:
        await _atlas(signed_in)
        card = await _card(signed_in, "Quiet")

        detail = (await signed_in.get(f"/tasks/{card['reference']}")).json()

        assert detail["comments"] == []
        assert detail["comments_next"] is None


class TestColdStorage:
    async def test_is_read_most_recently_finished_first(self, signed_in: AsyncClient) -> None:
        await _atlas(signed_in)
        last = (await _columns(signed_in))[-1]
        for title in ("First done", "Second done", "Third done"):
            card = await _card(signed_in, title)
            await signed_in.post(
                f"/tasks/{card['reference']}/move", json={"column_id": last["id"], "position": 0}
            )
        stored = await signed_in.post("/projects/ATL/cold-storage")
        assert stored.status_code == 200, stored.text

        rows = await _walk(signed_in, "/projects/ATL/cold-storage", limit=2)

        assert [card["title"] for card in rows] == ["Third done", "Second done", "First done"]
        assert all(card["archived_at"] and card["finished_at"] for card in rows)


class TestAGoal:
    async def test_carries_its_first_cards_and_pages_the_rest(self, signed_in: AsyncClient) -> None:
        await _atlas(signed_in)
        me = (await signed_in.get("/me")).json()["person"]["id"]
        goal = (
            await signed_in.post("/projects/ATL/goals", json={"name": "Revamp", "owner_id": me})
        ).json()
        for number in range(60):
            card = await _card(signed_in, f"Card {number:02}")
            await signed_in.patch(f"/tasks/{card['reference']}", json={"goal_id": goal["id"]})

        detail = (await signed_in.get(f"/goals/{goal['reference']}")).json()
        rest = await _walk(
            signed_in, f"/goals/{goal['reference']}/tasks", after=detail["tasks_next"]
        )

        assert len(detail["tasks"]) == 50
        assert detail["progress"]["total"] == 60
        titles = [card["title"] for card in detail["tasks"] + rest]
        assert titles == [f"Card {number:02}" for number in range(60)]


class TestTheDirectory:
    async def test_walking_it_visits_everybody_once_in_order(self, signed_in: AsyncClient) -> None:
        for name in ("Zed", "Amy", "Kai", "Bea", "Ola"):
            created = await signed_in.post(
                "/people",
                json={
                    "name": name,
                    "kind": "client",
                    "title": "Buyer",
                    "responsibilities": "Signs off.",
                },
            )
            assert created.status_code == 201, created.text
        whole = await _walk(signed_in, "/people", limit=100)

        walked = await _walk(signed_in, "/people", limit=2)

        assert [person["id"] for person in walked] == [person["id"] for person in whole]
        clients = [person["name"] for person in walked if person["kind"] == "client"]
        assert clients == ["Amy", "Bea", "Kai", "Ola", "Zed"]


@pytest.mark.parametrize("limit", [1, 3])
async def test_the_activity_feed_continues_where_it_stopped(
    signed_in: AsyncClient, limit: int
) -> None:
    await _atlas(signed_in)
    for number in range(4):
        await _card(signed_in, f"Card {number}")
    whole = (await signed_in.get("/activity", params={"limit": 100})).json()["items"]

    walked = await _walk(signed_in, "/activity", limit=limit)

    assert [entry["id"] for entry in walked] == [entry["id"] for entry in whole]
