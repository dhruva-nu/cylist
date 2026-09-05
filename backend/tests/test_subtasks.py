"""Sub-tasks: the work under a card, the tick boxes on one, and the gate.

Neither kind of sub-task is on the board. A sub-task has a reference, an owner
and a timeline of its own, but no column: it is finished by being ticked off,
not by being dragged anywhere.

The gate is the point of the feature: a card whose parts are still open cannot
reach the board's last column. Both kinds of sub-task hold it back, and both
have two ways out — finished, or cancelled.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

ATLAS = {"key": "ATL", "name": "Atlas Billing Migration"}
ADITI = {
    "name": "Aditi K",
    "kind": "team",
    "role": "Backend engineer",
    "responsibilities": "Payments and webhooks.",
}


async def _setup(client: AsyncClient) -> str:
    await client.post("/projects", json=ATLAS)
    person = (await client.post("/people", json=ADITI)).json()["id"]
    await client.put("/projects/ATL/members", json={"person_ids": [person]})
    return str(person)


def _body(assignee: str, **overrides: Any) -> dict[str, Any]:
    return {
        "title": "Stripe webhook idempotency",
        "description": "Duplicate deliveries create double payments. Dedupe on event id.",
        "type": "bug",
        "due_date": "2026-09-01",
        "assignee_id": assignee,
        **overrides,
    }


async def _create(client: AsyncClient, assignee: str, **overrides: Any) -> dict[str, Any]:
    response = await client.post("/projects/ATL/tasks", json=_body(assignee, **overrides))
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _subtask(
    client: AsyncClient, parent: str, assignee: str, **overrides: Any
) -> dict[str, Any]:
    response = await client.post(f"/tasks/{parent}/subtasks", json=_body(assignee, **overrides))
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _columns(client: AsyncClient) -> list[dict[str, Any]]:
    return list((await client.get("/projects/ATL/columns")).json()["columns"])


async def _move(client: AsyncClient, task: str, column: str) -> Any:
    return await client.post(f"/tasks/{task}/move", json={"column_id": column, "position": 0})


async def _finish(client: AsyncClient, task: str) -> None:
    """Put a card in the board's last column, asserting it got there."""
    done = (await _columns(client))[-1]["id"]
    response = await _move(client, task, done)
    assert response.status_code == 200, response.text


async def _tick(client: AsyncClient, task: str, finished: bool = True) -> Any:
    """Tick a sub-task off, which is the only way one is finished."""
    return await client.post(f"/tasks/{task}/finish", json={"finished": finished})


class TestBoardSubtasks:
    async def test_a_subtask_is_numbered_under_its_parent(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        parent = await _create(signed_in, person)

        first = await _subtask(signed_in, parent["reference"], person, title="Replay endpoint")
        second = await _subtask(signed_in, parent["reference"], person, title="Backfill")

        assert parent["reference"] == "ATL-1"
        assert first["reference"] == "ATL-1-1"
        assert second["reference"] == "ATL-1-2"
        assert first["parent_reference"] == "ATL-1"
        assert first["sub_number"] == 1
        assert first["number"] is None

    async def test_a_subtask_takes_no_project_number(self, signed_in: AsyncClient) -> None:
        """The board's own numbering must not skip because a card was split."""
        person = await _setup(signed_in)
        parent = await _create(signed_in, person)

        await _subtask(signed_in, parent["reference"], person)
        sibling = await _create(signed_in, person)

        assert sibling["reference"] == "ATL-2"

    async def test_a_sub_number_is_never_reused(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        parent = await _create(signed_in, person)
        first = await _subtask(signed_in, parent["reference"], person)

        await signed_in.delete(f"/tasks/{first['id']}")
        second = await _subtask(signed_in, parent["reference"], person)

        assert second["reference"] == "ATL-1-2"

    async def test_a_subtask_is_not_on_the_board(self, signed_in: AsyncClient) -> None:
        """One piece of work is one card. The board lists the parent alone."""
        person = await _setup(signed_in)
        parent = await _create(signed_in, person)

        subtask = await _subtask(signed_in, parent["reference"], person)

        board = (await signed_in.get("/projects/ATL/tasks")).json()
        assert [task["reference"] for task in board] == ["ATL-1"]
        assert subtask["column_id"] is None
        assert subtask["position"] is None

    async def test_a_subtask_cannot_be_moved(self, signed_in: AsyncClient) -> None:
        """There is nowhere to move it to, and the refusal says where to go."""
        person = await _setup(signed_in)
        parent = await _create(signed_in, person)
        subtask = await _subtask(signed_in, parent["reference"], person)

        response = await _move(
            signed_in, subtask["reference"], (await _columns(signed_in))[-1]["id"]
        )

        assert response.status_code == 422, response.text
        error = response.json()["error"]
        assert "not on the board" in error["message"]
        assert error["details"]["parent"] == "ATL-1"

    async def test_splitting_a_card_leaves_the_column_alone(self, signed_in: AsyncClient) -> None:
        """A card's neighbours keep their positions: nothing was inserted."""
        person = await _setup(signed_in)
        first = await _create(signed_in, person, title="One")
        second = await _create(signed_in, person, title="Two")

        await _subtask(signed_in, first["reference"], person)

        board = {
            task["reference"]: task for task in (await signed_in.get("/projects/ATL/tasks")).json()
        }
        assert board[first["reference"]]["position"] == 0
        assert board[second["reference"]]["position"] == 1

    async def test_a_subtask_is_addressed_by_its_reference(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        parent = await _create(signed_in, person)
        await _subtask(signed_in, parent["reference"], person, title="Replay endpoint")

        response = await signed_in.get("/tasks/atl-1-1")

        assert response.status_code == 200, response.text
        assert response.json()["title"] == "Replay endpoint"

    async def test_a_subtask_cannot_be_split_again(self, signed_in: AsyncClient) -> None:
        """One level deep. A board that nests further is a tree."""
        person = await _setup(signed_in)
        parent = await _create(signed_in, person)
        subtask = await _subtask(signed_in, parent["reference"], person)

        response = await signed_in.post(
            f"/tasks/{subtask['reference']}/subtasks", json=_body(person)
        )

        assert response.status_code == 422, response.text
        assert "sub-task" in response.json()["error"]["message"]

    async def test_three_deep_a_reference_is_not_a_task(self, signed_in: AsyncClient) -> None:
        await _setup(signed_in)

        response = await signed_in.get("/tasks/ATL-1-1-1")

        assert response.status_code == 404

    async def test_the_parent_lists_its_subtasks(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        parent = await _create(signed_in, person)
        await _subtask(signed_in, parent["reference"], person, title="Replay endpoint")

        detail = (await signed_in.get(f"/tasks/{parent['reference']}")).json()
        listed = (await signed_in.get(f"/tasks/{parent['reference']}/subtasks")).json()

        assert [child["reference"] for child in detail["subtasks"]] == ["ATL-1-1"]
        assert [child["title"] for child in listed] == ["Replay endpoint"]

    async def test_deleting_a_parent_takes_its_subtasks(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        parent = await _create(signed_in, person)
        subtask = await _subtask(signed_in, parent["reference"], person)

        assert (await signed_in.delete(f"/tasks/{parent['id']}")).status_code == 200

        assert (await signed_in.get(f"/tasks/{subtask['id']}")).status_code == 404
        assert (await signed_in.get("/projects/ATL/tasks")).json() == []


class TestChecklist:
    async def test_items_are_added_in_order(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person)

        for title in ("Write the runbook", "Tell support"):
            response = await signed_in.post(
                f"/tasks/{task['reference']}/checklist", json={"title": title}
            )
            assert response.status_code == 201, response.text

        detail = (await signed_in.get(f"/tasks/{task['reference']}")).json()
        assert [item["title"] for item in detail["checklist"]] == [
            "Write the runbook",
            "Tell support",
        ]
        assert [item["position"] for item in detail["checklist"]] == [0, 1]
        assert detail["open_subtask_count"] == 2

    async def test_an_item_is_ticked_cancelled_and_reopened(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person)
        item = (
            await signed_in.post(f"/tasks/{task['reference']}/checklist", json={"title": "Tell QA"})
        ).json()

        for state in ("done", "cancelled", "open"):
            response = await signed_in.patch(f"/checklist/{item['id']}", json={"state": state})
            assert response.status_code == 200, response.text
            assert response.json()["state"] == state

    async def test_an_item_is_retitled(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person)
        item = (
            await signed_in.post(f"/tasks/{task['reference']}/checklist", json={"title": "Tell QA"})
        ).json()

        response = await signed_in.patch(f"/checklist/{item['id']}", json={"title": "Tell support"})

        assert response.json()["title"] == "Tell support"
        assert response.json()["state"] == "open"

    async def test_deleting_an_item_closes_the_gap(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person)
        items = [
            (
                await signed_in.post(f"/tasks/{task['reference']}/checklist", json={"title": title})
            ).json()
            for title in ("One", "Two", "Three")
        ]

        assert (await signed_in.delete(f"/checklist/{items[0]['id']}")).status_code == 200

        detail = (await signed_in.get(f"/tasks/{task['reference']}")).json()
        assert [(item["title"], item["position"]) for item in detail["checklist"]] == [
            ("Two", 0),
            ("Three", 1),
        ]

    async def test_a_blank_title_is_refused(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person)

        response = await signed_in.post(
            f"/tasks/{task['reference']}/checklist", json={"title": " "}
        )

        assert response.status_code == 422

    async def test_items_go_with_the_task(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person)
        item = (
            await signed_in.post(f"/tasks/{task['reference']}/checklist", json={"title": "Tell QA"})
        ).json()

        await signed_in.delete(f"/tasks/{task['id']}")

        assert (await signed_in.patch(f"/checklist/{item['id']}", json={})).status_code == 404


class TestTheGate:
    """Nothing reaches the last column with a sub-task still open."""

    async def test_an_open_checklist_item_holds_the_card_back(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person)
        await signed_in.post(f"/tasks/{task['reference']}/checklist", json={"title": "Tell QA"})

        response = await _move(signed_in, task["reference"], (await _columns(signed_in))[-1]["id"])

        assert response.status_code == 422, response.text
        error = response.json()["error"]
        assert "Tell QA" in error["message"]
        assert error["details"]["open_checklist_items"] == ["Tell QA"]

    async def test_ticking_the_item_lets_it_through(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person)
        item = (
            await signed_in.post(f"/tasks/{task['reference']}/checklist", json={"title": "Tell QA"})
        ).json()

        await signed_in.patch(f"/checklist/{item['id']}", json={"state": "done"})

        await _finish(signed_in, task["reference"])

    async def test_cancelling_the_item_lets_it_through(self, signed_in: AsyncClient) -> None:
        """Cancelled is settled: the work is not happening, so nothing waits."""
        person = await _setup(signed_in)
        task = await _create(signed_in, person)
        item = (
            await signed_in.post(f"/tasks/{task['reference']}/checklist", json={"title": "Tell QA"})
        ).json()

        await signed_in.patch(f"/checklist/{item['id']}", json={"state": "cancelled"})

        await _finish(signed_in, task["reference"])

    async def test_an_unfinished_subtask_holds_the_card_back(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        parent = await _create(signed_in, person)
        await _subtask(signed_in, parent["reference"], person)

        response = await _move(
            signed_in, parent["reference"], (await _columns(signed_in))[-1]["id"]
        )

        assert response.status_code == 422, response.text
        assert response.json()["error"]["details"]["open_subtasks"] == ["ATL-1-1"]

    async def test_finishing_the_subtask_lets_the_parent_through(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _setup(signed_in)
        parent = await _create(signed_in, person)
        subtask = await _subtask(signed_in, parent["reference"], person)

        ticked = await _tick(signed_in, subtask["reference"])
        assert ticked.status_code == 200, ticked.text
        assert ticked.json()["finished_at"] is not None

        await _finish(signed_in, parent["reference"])

    async def test_reopening_the_subtask_shuts_the_gate_again(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        parent = await _create(signed_in, person)
        subtask = await _subtask(signed_in, parent["reference"], person)
        await _tick(signed_in, subtask["reference"])

        reopened = await _tick(signed_in, subtask["reference"], finished=False)
        assert reopened.status_code == 200, reopened.text
        assert reopened.json()["finished_at"] is None

        response = await _move(
            signed_in, parent["reference"], (await _columns(signed_in))[-1]["id"]
        )
        assert response.status_code == 422, response.text

    async def test_ticking_a_finished_subtask_changes_nothing(self, signed_in: AsyncClient) -> None:
        """A second click is a client catching up, not a new event."""
        person = await _setup(signed_in)
        parent = await _create(signed_in, person)
        subtask = await _subtask(signed_in, parent["reference"], person)

        first = (await _tick(signed_in, subtask["reference"])).json()
        again = (await _tick(signed_in, subtask["reference"])).json()

        assert again["finished_at"] == first["finished_at"]
        history = (await signed_in.get(f"/tasks/{subtask['reference']}/history")).json()
        assert [entry["verb"] for entry in history["entries"]].count("task.finished") == 1

    async def test_a_card_is_not_finished_by_ticking(self, signed_in: AsyncClient) -> None:
        """A card's done is the board's answer, and the refusal says so."""
        person = await _setup(signed_in)
        task = await _create(signed_in, person)

        response = await _tick(signed_in, task["reference"])

        assert response.status_code == 422, response.text
        assert "last column" in response.json()["error"]["message"]

    async def test_cancelling_the_subtask_lets_the_parent_through(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _setup(signed_in)
        parent = await _create(signed_in, person)
        subtask = await _subtask(signed_in, parent["reference"], person)

        response = await signed_in.post(
            f"/tasks/{subtask['reference']}/status",
            json={"status": "cancelled", "reason": "The vendor withdrew the endpoint."},
        )
        assert response.status_code == 200, response.text

        await _finish(signed_in, parent["reference"])

    async def test_cancelling_still_needs_a_reason(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person)

        response = await signed_in.post(
            f"/tasks/{task['reference']}/status", json={"status": "cancelled"}
        )

        assert response.status_code == 422
        assert response.json()["error"]["details"]["field"] == "reason"

    async def test_a_card_already_finished_can_be_reordered(self, signed_in: AsyncClient) -> None:
        """The rule guards the way in, not the shuffling once inside."""
        person = await _setup(signed_in)
        task = await _create(signed_in, person)
        await _finish(signed_in, task["reference"])
        await signed_in.post(f"/tasks/{task['reference']}/checklist", json={"title": "Tell QA"})

        await _finish(signed_in, task["reference"])

    async def test_other_columns_are_unguarded(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        await signed_in.post(
            "/projects/ATL/columns",
            json={"name": "In progress", "description": "Someone is on it."},
        )
        task = await _create(signed_in, person)
        await signed_in.post(f"/tasks/{task['reference']}/checklist", json={"title": "Tell QA"})

        middle = (await _columns(signed_in))[1]["id"]
        response = await _move(signed_in, task["reference"], middle)

        assert response.status_code == 200, response.text

    async def test_the_board_says_how_much_is_open(self, signed_in: AsyncClient) -> None:
        """Two of three left, on the card, without opening it."""
        person = await _setup(signed_in)
        parent = await _create(signed_in, person)
        first = await _subtask(signed_in, parent["reference"], person)
        await _subtask(signed_in, parent["reference"], person)
        await signed_in.post(f"/tasks/{parent['reference']}/checklist", json={"title": "Tell QA"})
        await _tick(signed_in, first["reference"])

        board = {
            task["reference"]: task for task in (await signed_in.get("/projects/ATL/tasks")).json()
        }

        assert board["ATL-1"]["subtask_count"] == 3
        assert board["ATL-1"]["open_subtask_count"] == 2

    async def test_a_cancelled_subtask_is_counted_in_neither_half(
        self, signed_in: AsyncClient
    ) -> None:
        """Two sub-tasks with one dropped is two, not three with one done."""
        person = await _setup(signed_in)
        parent = await _create(signed_in, person)
        dropped = await _subtask(signed_in, parent["reference"], person)
        await _subtask(signed_in, parent["reference"], person)

        await signed_in.post(
            f"/tasks/{dropped['reference']}/status",
            json={"status": "cancelled", "reason": "The vendor withdrew the endpoint."},
        )

        detail = (await signed_in.get(f"/tasks/{parent['reference']}")).json()
        assert detail["subtask_count"] == 1
        assert detail["open_subtask_count"] == 1

    async def test_the_hub_counts_cards_but_flags_a_blocked_subtask(
        self, signed_in: AsyncClient
    ) -> None:
        """Two numbers, two questions: how big the board is, and what is stuck."""
        person = await _setup(signed_in)
        parent = await _create(signed_in, person)
        subtask = await _subtask(signed_in, parent["reference"], person)
        await signed_in.post(
            f"/tasks/{subtask['reference']}/status",
            json={"status": "blocked", "reason": "The vendor has not replied."},
        )

        summary = (await signed_in.get("/projects/ATL/summary")).json()

        assert summary["task_count"] == 1
        assert summary["blocked_count"] == 1

    async def test_the_card_carries_its_subtasks_owners(self, signed_in: AsyncClient) -> None:
        """The board no longer says where a sub-task is, so it says who is on it."""
        person = await _setup(signed_in)
        other = (
            await signed_in.post(
                "/people",
                json={
                    "name": "Ravi S",
                    "kind": "team",
                    "role": "Frontend engineer",
                    "responsibilities": "The board.",
                },
            )
        ).json()["id"]
        await signed_in.put("/projects/ATL/members", json={"person_ids": [person, other]})
        parent = await _create(signed_in, person)
        await _subtask(signed_in, parent["reference"], person)
        await _subtask(signed_in, parent["reference"], other)
        await _subtask(signed_in, parent["reference"], person)

        board = (await signed_in.get("/projects/ATL/tasks")).json()

        # In sub-number order, and each person once however many they own.
        assert [owner["name"] for owner in board[0]["subtask_assignees"]] == ["Aditi K", "Ravi S"]
