"""A task's history: what changed, when, and who changed it."""

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
ROHAN = {
    "name": "Rohan S",
    "kind": "team",
    "role": "Backend engineer",
    "responsibilities": "Data migration and reporting.",
}


async def _setup(client: AsyncClient) -> list[str]:
    """A project with two members. Returns their ids."""
    await client.post("/projects", json=ATLAS)
    people = [(await client.post("/people", json=who)).json()["id"] for who in (ADITI, ROHAN)]
    await client.put("/projects/ATL/members", json={"person_ids": people})
    return people


def _task(assignee: str, **overrides: Any) -> dict[str, Any]:
    return {
        "title": "Stripe webhook idempotency",
        "description": "Duplicate deliveries create double payments. Dedupe on event id.",
        "type": "bug",
        "due_date": "2026-09-01",
        "assignee_id": assignee,
        **overrides,
    }


async def _create(client: AsyncClient, assignee: str, **overrides: Any) -> dict[str, Any]:
    response = await client.post("/projects/ATL/tasks", json=_task(assignee, **overrides))
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _page(client: AsyncClient, reference: str, **params: Any) -> dict[str, Any]:
    response = await client.get(f"/tasks/{reference}/history", params=params)
    assert response.status_code == 200, response.text
    return dict(response.json())


async def _history(client: AsyncClient, reference: str) -> list[dict[str, Any]]:
    """The first page's entries, which is all of them in most of these tests."""
    return list((await _page(client, reference))["entries"])


async def _columns(client: AsyncClient) -> list[dict[str, Any]]:
    return list((await client.get("/projects/ATL/columns")).json()["columns"])


class TestReading:
    async def test_a_new_card_has_one_entry(self, signed_in: AsyncClient) -> None:
        aditi, _ = await _setup(signed_in)

        task = await _create(signed_in, aditi)

        entries = await _history(signed_in, task["reference"])
        assert [entry["verb"] for entry in entries] == ["task.created"]
        assert entries[0]["summary"] == "Created this task."

    async def test_says_when_and_who(self, signed_in: AsyncClient) -> None:
        """The three things the history exists to answer, on every entry."""
        aditi, _ = await _setup(signed_in)
        task = await _create(signed_in, aditi)

        entry = (await _history(signed_in, task["reference"]))[0]

        assert entry["occurred_at"]
        assert entry["actor_label"] == "Web session"
        assert entry["channel"] == "web"

    async def test_an_agents_work_is_marked_as_an_agents(self, signed_in: AsyncClient) -> None:
        """A card must say whether a person or a bot moved it."""
        aditi, _ = await _setup(signed_in)
        task = await _create(signed_in, aditi)
        issued = await signed_in.post(
            "/tokens", json={"name": "board agent", "scopes": ["read", "write"]}
        )
        agent = {"Authorization": f"Bearer {issued.json()['token']}"}

        await signed_in.patch(
            f"/tasks/{task['reference']}", json={"title": "Dedupe on event id"}, headers=agent
        )

        entry = (await _history(signed_in, task["reference"]))[0]
        assert entry["actor_label"] == "board agent"
        assert entry["channel"] == "api"

    async def test_newest_first(self, signed_in: AsyncClient) -> None:
        aditi, _ = await _setup(signed_in)
        task = await _create(signed_in, aditi)

        await signed_in.patch(f"/tasks/{task['reference']}", json={"title": "Renamed"})

        assert [entry["verb"] for entry in await _history(signed_in, task["reference"])] == [
            "task.updated",
            "task.created",
        ]

    async def test_a_missing_task_is_a_clean_404(self, signed_in: AsyncClient) -> None:
        await _setup(signed_in)

        response = await signed_in.get("/tasks/ATL-99/history")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    async def test_a_sub_task_keeps_its_own(self, signed_in: AsyncClient) -> None:
        """A sub-task is a card; its parent's history is about the parent."""
        aditi, _ = await _setup(signed_in)
        parent = await _create(signed_in, aditi)
        child = (
            await signed_in.post(f"/tasks/{parent['reference']}/subtasks", json=_task(aditi))
        ).json()

        assert [entry["verb"] for entry in await _history(signed_in, child["reference"])] == [
            "task.subtask_created"
        ]
        assert [entry["verb"] for entry in await _history(signed_in, parent["reference"])] == [
            "task.created"
        ]


class TestWhatChanged:
    async def test_records_a_fields_old_and_new_value(self, signed_in: AsyncClient) -> None:
        aditi, _ = await _setup(signed_in)
        task = await _create(signed_in, aditi)

        await signed_in.patch(f"/tasks/{task['reference']}", json={"title": "Dedupe on event id"})

        entry = (await _history(signed_in, task["reference"]))[0]
        assert entry["changes"] == [
            {
                "field": "title",
                "label": "title",
                "from": "Stripe webhook idempotency",
                "to": "Dedupe on event id",
            }
        ]
        assert entry["summary"] == "Changed the title."

    async def test_reports_only_the_fields_that_moved(self, signed_in: AsyncClient) -> None:
        """A form sends every field back; a history that repeated them would
        say a card was rewritten each time one date changed."""
        aditi, _ = await _setup(signed_in)
        task = await _create(signed_in, aditi)

        await signed_in.patch(
            f"/tasks/{task['reference']}",
            json={
                "title": task["title"],
                "description": task["description"],
                "type": task["type"],
                "due_date": "2026-10-01",
            },
        )

        entry = (await _history(signed_in, task["reference"]))[0]
        assert [change["field"] for change in entry["changes"]] == ["due_date"]
        assert entry["changes"][0]["from"] == "2026-09-01"
        assert entry["changes"][0]["to"] == "2026-10-01"

    async def test_a_patch_that_changes_nothing_is_not_history(
        self, signed_in: AsyncClient
    ) -> None:
        """Saving a form without touching a field is not something that
        happened to the work. The audit feed still has it."""
        aditi, _ = await _setup(signed_in)
        task = await _create(signed_in, aditi)

        await signed_in.patch(f"/tasks/{task['reference']}", json={"title": task["title"]})

        assert [entry["verb"] for entry in await _history(signed_in, task["reference"])] == [
            "task.created"
        ]
        feed = (await signed_in.get("/activity", params={"entity_type": "task"})).json()
        assert feed[0]["verb"] == "task.updated"

    async def test_names_the_assignee_rather_than_their_id(self, signed_in: AsyncClient) -> None:
        """An id tells the reader nothing about who picked the work up."""
        aditi, rohan = await _setup(signed_in)
        task = await _create(signed_in, aditi)

        await signed_in.patch(f"/tasks/{task['reference']}", json={"assignee_id": rohan})

        entry = (await _history(signed_in, task["reference"]))[0]
        assert entry["changes"] == [
            {"field": "assignee", "label": "assignee", "from": "Aditi K", "to": "Rohan S"}
        ]

    async def test_lists_several_changes_in_one_sentence(self, signed_in: AsyncClient) -> None:
        aditi, _ = await _setup(signed_in)
        task = await _create(signed_in, aditi)

        await signed_in.patch(
            f"/tasks/{task['reference']}",
            json={"title": "Renamed", "priority": "urgent", "due_date": "2026-10-01"},
        )

        entry = (await _history(signed_in, task["reference"]))[0]
        assert entry["summary"] == "Changed the title, priority and due date."

    async def test_a_cleared_reference_reads_as_nothing(self, signed_in: AsyncClient) -> None:
        aditi, _ = await _setup(signed_in)
        task = await _create(signed_in, aditi, jira_ref="ATL-9")

        await signed_in.patch(f"/tasks/{task['reference']}", json={"jira_ref": None})

        entry = (await _history(signed_in, task["reference"]))[0]
        assert entry["changes"] == [
            {"field": "jira_ref", "label": "Jira reference", "from": "ATL-9", "to": None}
        ]


class TestMoving:
    async def test_names_both_columns(self, signed_in: AsyncClient) -> None:
        aditi, _ = await _setup(signed_in)
        task = await _create(signed_in, aditi)
        columns = await _columns(signed_in)

        await signed_in.post(
            f"/tasks/{task['reference']}/move",
            json={"column_id": columns[-1]["id"], "position": 0},
        )

        entry = (await _history(signed_in, task["reference"]))[0]
        assert entry["summary"] == "Moved from To do to Done."
        assert entry["changes"][0] == {
            "field": "column",
            "label": "column",
            "from": "To do",
            "to": "Done",
        }

    async def test_reordering_within_a_column_is_not_history(self, signed_in: AsyncClient) -> None:
        """A card's place in a stack is not a fact about the work, so dragging
        one up its own column leaves its history alone."""
        aditi, _ = await _setup(signed_in)
        task = await _create(signed_in, aditi)
        await _create(signed_in, aditi, title="Second")
        columns = await _columns(signed_in)

        await signed_in.post(
            f"/tasks/{task['reference']}/move",
            json={"column_id": columns[0]["id"], "position": 1},
        )

        assert [entry["verb"] for entry in await _history(signed_in, task["reference"])] == [
            "task.created"
        ]

    async def test_records_the_stage_a_move_reset(self, signed_in: AsyncClient) -> None:
        """Changing column restarts the stages, and that is a change nobody
        asked for — so it is one the history has to volunteer."""
        aditi, _ = await _setup(signed_in)
        task = await _create(signed_in, aditi, sub_statuses=["Draft", "Review"])
        columns = await _columns(signed_in)
        await signed_in.post(f"/tasks/{task['reference']}/sub-status", json={"index": 1})

        await signed_in.post(
            f"/tasks/{task['reference']}/move",
            json={"column_id": columns[-1]["id"], "position": 0},
        )

        entry = (await _history(signed_in, task["reference"]))[0]
        assert entry["changes"][1] == {
            "field": "sub_status_index",
            "label": "sub-status",
            "from": "Review",
            "to": "Draft",
        }


class TestEverythingElse:
    async def test_a_sub_status_step_names_the_stage(self, signed_in: AsyncClient) -> None:
        aditi, _ = await _setup(signed_in)
        task = await _create(signed_in, aditi, sub_statuses=["Draft", "Review"])

        await signed_in.post(f"/tasks/{task['reference']}/sub-status", json={"index": 1})

        entry = (await _history(signed_in, task["reference"]))[0]
        assert entry["summary"] == "Sub-status set to Review."
        assert entry["changes"] == [
            {"field": "sub_status_index", "label": "sub-status", "from": "Draft", "to": "Review"}
        ]

    async def test_a_status_change_carries_its_reason(self, signed_in: AsyncClient) -> None:
        aditi, _ = await _setup(signed_in)
        task = await _create(signed_in, aditi)

        await signed_in.post(
            f"/tasks/{task['reference']}/status",
            json={"status": "blocked", "reason": "Waiting on Stripe support."},
        )

        entry = (await _history(signed_in, task["reference"]))[0]
        assert entry["summary"] == "Status blocked — Waiting on Stripe support."
        assert entry["changes"] == [
            {"field": "status", "label": "status", "from": "active", "to": "blocked"}
        ]

    async def test_checklist_work_is_recorded(self, signed_in: AsyncClient) -> None:
        aditi, _ = await _setup(signed_in)
        task = await _create(signed_in, aditi)

        item = (
            await signed_in.post(
                f"/tasks/{task['reference']}/checklist", json={"title": "Add the index"}
            )
        ).json()
        await signed_in.patch(f"/checklist/{item['id']}", json={"state": "done"})

        summaries = [entry["summary"] for entry in await _history(signed_in, task["reference"])]
        assert summaries[:2] == [
            "Ticked off “Add the index”.",
            "Added “Add the index” to the checklist.",
        ]

    async def test_a_comment_appears_without_its_words(self, signed_in: AsyncClient) -> None:
        """What was said belongs on the timeline; the history says only that
        somebody said something, and when."""
        aditi, _ = await _setup(signed_in)
        task = await _create(signed_in, aditi)

        await signed_in.post(
            f"/tasks/{task['reference']}/comments",
            json={"body": "Stripe confirmed the retry window.", "author_id": aditi},
        )

        entry = (await _history(signed_in, task["reference"]))[0]
        assert entry["summary"] == "Added a comment."
        assert "Stripe confirmed" not in str(entry)


class TestPaging:
    async def test_ten_to_a_page_by_default(self, signed_in: AsyncClient) -> None:
        aditi, _ = await _setup(signed_in)
        task = await _create(signed_in, aditi)
        for number in range(12):
            await signed_in.patch(f"/tasks/{task['reference']}", json={"title": f"Take {number}"})

        page = await _page(signed_in, task["reference"])

        assert len(page["entries"]) == 10
        assert page == {**page, "total": 13, "page": 1, "pages": 2, "per_page": 10}

    async def test_the_second_page_carries_on_where_the_first_stopped(
        self, signed_in: AsyncClient
    ) -> None:
        aditi, _ = await _setup(signed_in)
        task = await _create(signed_in, aditi)
        for number in range(12):
            await signed_in.patch(f"/tasks/{task['reference']}", json={"title": f"Take {number}"})

        first = await _page(signed_in, task["reference"])
        second = await _page(signed_in, task["reference"], page=2)

        assert len(second["entries"]) == 3
        assert second["page"] == 2
        # Newest first, unbroken: the oldest entry is the card's creation.
        ids = [entry["id"] for entry in [*first["entries"], *second["entries"]]]
        assert len(set(ids)) == 13
        assert second["entries"][-1]["summary"] == "Created this task."

    async def test_the_page_size_can_be_asked_for(self, signed_in: AsyncClient) -> None:
        aditi, _ = await _setup(signed_in)
        task = await _create(signed_in, aditi)
        await signed_in.patch(f"/tasks/{task['reference']}", json={"title": "Renamed"})

        page = await _page(signed_in, task["reference"], per_page=1)

        assert [entry["verb"] for entry in page["entries"]] == ["task.updated"]
        assert page["pages"] == 2

    async def test_a_page_past_the_end_is_empty_rather_than_an_error(
        self, signed_in: AsyncClient
    ) -> None:
        aditi, _ = await _setup(signed_in)
        task = await _create(signed_in, aditi)

        page = await _page(signed_in, task["reference"], page=9)

        assert page["entries"] == []
        assert page["pages"] == 1
        assert page["total"] == 1

    async def test_page_zero_is_refused(self, signed_in: AsyncClient) -> None:
        aditi, _ = await _setup(signed_in)
        task = await _create(signed_in, aditi)

        response = await signed_in.get(f"/tasks/{task['reference']}/history", params={"page": 0})

        assert response.status_code == 422
