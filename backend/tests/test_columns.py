"""Board columns: the starter pair, the two-to-eight range, reordering and deletion."""

from __future__ import annotations

from httpx import AsyncClient

ATLAS = {"key": "ATL", "name": "Atlas Billing Migration"}
TEAM = {
    "name": "Aditi K",
    "kind": "team",
    "role": "Backend engineer",
    "responsibilities": "Payments and webhooks.",
}
UNKNOWN_ID = "00000000-0000-7000-8000-000000000000"


async def _board(client: AsyncClient) -> list[dict[str, object]]:
    body = (await client.get("/projects/ATL/columns")).json()
    return list(body["columns"])


async def _add_column(client: AsyncClient, name: str) -> dict[str, object]:
    response = await client.post(
        "/projects/ATL/columns", json={"name": name, "description": f"Work that is {name}."}
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _fill_to_eight(client: AsyncClient) -> None:
    for index in range(6):
        await _add_column(client, f"Column {index}")


async def _task_in(client: AsyncClient, column_id: str) -> str:
    """Create a task and move it into a column, returning its id."""
    person = (await client.post("/people", json=TEAM)).json()["id"]
    await client.put("/projects/ATL/members", json={"person_ids": [person]})
    task = (
        await client.post(
            "/projects/ATL/tasks",
            json={
                "title": "Stripe webhook idempotency",
                "description": "Duplicate deliveries create double payments.",
                "type": "bug",
                "due_date": "2026-09-01",
                "assignee_id": person,
            },
        )
    ).json()
    await client.post(f"/tasks/{task['id']}/move", json={"column_id": column_id, "position": 0})
    return str(task["id"])


class TestStarterColumns:
    async def test_a_new_project_arrives_with_a_usable_board(self, signed_in: AsyncClient) -> None:
        """A board with no columns cannot hold a task, so creation seeds two."""
        await signed_in.post("/projects", json=ATLAS)

        columns = await _board(signed_in)

        assert [column["name"] for column in columns] == ["To do", "Done"]
        assert [column["position"] for column in columns] == [0, 1]

    async def test_the_starter_columns_are_described(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)

        columns = await _board(signed_in)

        assert all(column["description"] for column in columns)

    async def test_the_board_reports_its_limits(self, signed_in: AsyncClient) -> None:
        """So a client can grey out its 'add a column' tile without hard-coding 8."""
        await signed_in.post("/projects", json=ATLAS)

        body = (await signed_in.get("/projects/ATL/columns")).json()

        assert body["min_columns"] == 2
        assert body["max_columns"] == 8

    async def test_seeding_is_not_logged_as_its_own_event(self, signed_in: AsyncClient) -> None:
        """The columns are part of creating the project, not a second action."""
        await signed_in.post("/projects", json=ATLAS)

        entries = (await signed_in.get("/activity", params={"entity_type": "column"})).json()

        assert entries == []


class TestAdding:
    async def test_a_new_column_goes_to_the_right(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)

        column = await _add_column(signed_in, "Review")

        assert column["position"] == 2
        assert [c["name"] for c in await _board(signed_in)] == ["To do", "Done", "Review"]

    async def test_a_new_column_starts_empty(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)

        assert (await _add_column(signed_in, "Review"))["task_count"] == 0

    async def test_the_eighth_column_is_allowed(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)

        await _fill_to_eight(signed_in)

        assert len(await _board(signed_in)) == 8

    async def test_the_ninth_column_is_refused(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        await _fill_to_eight(signed_in)

        response = await signed_in.post(
            "/projects/ATL/columns", json={"name": "One more", "description": "Nope."}
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"
        assert response.json()["error"]["details"] == {"column_count": 8, "max_columns": 8}

    async def test_a_refused_ninth_column_is_not_half_written(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        await _fill_to_eight(signed_in)

        await signed_in.post("/projects/ATL/columns", json={"name": "X", "description": "Nope."})

        assert len(await _board(signed_in)) == 8

    async def test_a_description_is_required(self, signed_in: AsyncClient) -> None:
        """An unexplained column is how two columns come to mean the same thing."""
        await signed_in.post("/projects", json=ATLAS)

        assert (
            await signed_in.post("/projects/ATL/columns", json={"name": "Review"})
        ).status_code == 422

    async def test_a_blank_description_is_not_a_description(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)

        response = await signed_in.post(
            "/projects/ATL/columns", json={"name": "Review", "description": "   "}
        )

        assert response.status_code == 422

    async def test_it_is_recorded_against_the_project(self, signed_in: AsyncClient) -> None:
        created = (await signed_in.post("/projects", json=ATLAS)).json()
        await _add_column(signed_in, "Review")

        entries = (await signed_in.get("/activity", params={"project": created["id"]})).json()

        assert entries[0]["verb"] == "column.created"
        assert entries[0]["payload"] == {"name": "Review", "position": 2}


class TestUpdating:
    async def test_changes_only_the_fields_given(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        column = (await _board(signed_in))[0]

        updated = (
            await signed_in.patch(f"/columns/{column['id']}", json={"name": "Backlog"})
        ).json()

        assert updated["name"] == "Backlog"
        assert updated["description"] == column["description"]

    async def test_an_unknown_column_is_a_clean_404(self, signed_in: AsyncClient) -> None:
        response = await signed_in.patch(f"/columns/{UNKNOWN_ID}", json={"name": "Backlog"})

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


class TestDeleting:
    async def test_removes_an_empty_column(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        extra = await _add_column(signed_in, "Review")

        assert (await signed_in.delete(f"/columns/{extra['id']}")).status_code == 200
        assert [c["name"] for c in await _board(signed_in)] == ["To do", "Done"]

    async def test_closes_the_gap_it_leaves(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        await _add_column(signed_in, "Review")
        middle = (await _board(signed_in))[1]

        await signed_in.delete(f"/columns/{middle['id']}")

        assert [c["position"] for c in await _board(signed_in)] == [0, 1]

    async def test_refuses_to_take_a_board_below_two_columns(self, signed_in: AsyncClient) -> None:
        """Nothing can move across a single column, so it is not a board."""
        await signed_in.post("/projects", json=ATLAS)
        first = (await _board(signed_in))[0]

        response = await signed_in.delete(f"/columns/{first['id']}")

        assert response.status_code == 409
        assert response.json()["error"]["details"] == {"column_count": 2, "min_columns": 2}
        assert len(await _board(signed_in)) == 2

    async def test_refuses_a_column_that_still_holds_tasks(self, signed_in: AsyncClient) -> None:
        """Tidying a board must never be a way to delete work by accident."""
        await signed_in.post("/projects", json=ATLAS)
        extra = await _add_column(signed_in, "Review")
        await _task_in(signed_in, str(extra["id"]))

        response = await signed_in.delete(f"/columns/{extra['id']}")

        assert response.status_code == 409
        assert response.json()["error"]["details"] == {"task_count": 1}

    async def test_the_same_column_deletes_once_its_tasks_have_moved(
        self, signed_in: AsyncClient
    ) -> None:
        await signed_in.post("/projects", json=ATLAS)
        extra = await _add_column(signed_in, "Review")
        task_id = await _task_in(signed_in, str(extra["id"]))
        elsewhere = (await _board(signed_in))[0]
        await signed_in.post(
            f"/tasks/{task_id}/move", json={"column_id": elsewhere["id"], "position": 0}
        )

        assert (await signed_in.delete(f"/columns/{extra['id']}")).status_code == 200

    async def test_it_is_recorded_against_the_project(self, signed_in: AsyncClient) -> None:
        created = (await signed_in.post("/projects", json=ATLAS)).json()
        extra = await _add_column(signed_in, "Review")

        await signed_in.delete(f"/columns/{extra['id']}")

        entries = (await signed_in.get("/activity", params={"project": created["id"]})).json()
        assert entries[0]["verb"] == "column.deleted"
        assert entries[0]["payload"] == {"name": "Review"}


class TestReordering:
    async def test_sets_the_left_to_right_order(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        await _add_column(signed_in, "Review")
        ids = [str(column["id"]) for column in await _board(signed_in)]

        response = await signed_in.put(
            "/projects/ATL/columns/order", json={"column_ids": list(reversed(ids))}
        )

        assert response.status_code == 200
        assert [c["name"] for c in response.json()["columns"]] == ["Review", "Done", "To do"]

    async def test_reordering_changes_where_new_tasks_land(self, signed_in: AsyncClient) -> None:
        """The first column is a workflow decision, not a display preference."""
        await signed_in.post("/projects", json=ATLAS)
        ids = [str(column["id"]) for column in await _board(signed_in)]
        await signed_in.put("/projects/ATL/columns/order", json={"column_ids": list(reversed(ids))})

        person = (await signed_in.post("/people", json=TEAM)).json()["id"]
        await signed_in.put("/projects/ATL/members", json={"person_ids": [person]})
        task = (
            await signed_in.post(
                "/projects/ATL/tasks",
                json={
                    "title": "Back-fill invoices",
                    "description": "Import the archived dump.",
                    "type": "chore",
                    "due_date": "2026-09-20",
                    "assignee_id": person,
                },
            )
        ).json()

        assert task["column_id"] == ids[1]

    async def test_refuses_a_partial_list(self, signed_in: AsyncClient) -> None:
        """Where the omitted columns would go has no obvious answer."""
        await signed_in.post("/projects", json=ATLAS)
        ids = [str(column["id"]) for column in await _board(signed_in)]

        response = await signed_in.put("/projects/ATL/columns/order", json={"column_ids": ids[:1]})

        assert response.status_code == 422
        assert response.json()["error"]["details"]["expected_column_ids"] == ids

    async def test_refuses_a_duplicated_id(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        ids = [str(column["id"]) for column in await _board(signed_in)]

        response = await signed_in.put(
            "/projects/ATL/columns/order", json={"column_ids": [ids[0], ids[0]]}
        )

        assert response.status_code == 422

    async def test_refuses_a_column_from_another_board(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        await signed_in.post("/projects", json={"key": "HRM", "name": "Hermes"})
        mine = [str(column["id"]) for column in await _board(signed_in)]
        theirs = (await signed_in.get("/projects/HRM/columns")).json()["columns"][0]["id"]

        response = await signed_in.put(
            "/projects/ATL/columns/order", json={"column_ids": [mine[0], theirs]}
        )

        assert response.status_code == 422

    async def test_a_rejected_reorder_leaves_the_board_alone(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        before = await _board(signed_in)

        await signed_in.put(
            "/projects/ATL/columns/order", json={"column_ids": [str(before[0]["id"])]}
        )

        assert await _board(signed_in) == before


class TestCounts:
    async def test_a_column_reports_how_many_cards_it_holds(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        await _task_in(signed_in, str((await _board(signed_in))[1]["id"]))

        columns = await _board(signed_in)

        assert [column["task_count"] for column in columns] == [0, 1]

    async def test_the_project_summary_counts_the_board(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        await _add_column(signed_in, "Review")
        await _task_in(signed_in, str((await _board(signed_in))[0]["id"]))

        summary = (await signed_in.get("/projects/ATL/summary")).json()

        assert summary["column_count"] == 3
        assert summary["task_count"] == 1
        assert summary["blocked_count"] == 0
        assert summary["on_hold_count"] == 0
