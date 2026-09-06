"""Tasks: numbering, where they land, how they move, how they are addressed."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BoardColumn, Person, Project, Task

ATLAS = {"key": "ATL", "name": "Atlas Billing Migration"}
HERMES = {"key": "HRM", "name": "Hermes Notifications"}
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
UNKNOWN_ID = "00000000-0000-7000-8000-000000000000"


async def _setup(client: AsyncClient, project: dict[str, str] = ATLAS) -> str:
    """Create a project with one member, and return that member's id."""
    await client.post("/projects", json=project)
    person = (await client.post("/people", json=ADITI)).json()["id"]
    await client.put(f"/projects/{project['key']}/members", json={"person_ids": [person]})
    return str(person)


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


async def _columns(client: AsyncClient, key: str = "ATL") -> list[dict[str, Any]]:
    return list((await client.get(f"/projects/{key}/columns")).json()["columns"])


class TestCreating:
    async def test_creates_a_card(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)

        task = await _create(signed_in, person)

        assert task["title"] == "Stripe webhook idempotency"
        assert task["status"] == "active"
        assert task["assignee"]["name"] == "Aditi K"
        assert task["comments"] == []

    async def test_priority_defaults_to_someday(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)

        task = await _create(signed_in, person)

        assert task["priority"] == "someday"

    async def test_priority_can_be_set_on_creation(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)

        task = await _create(signed_in, person, priority="urgent")

        assert task["priority"] == "urgent"

    async def test_an_unknown_priority_is_refused(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)

        response = await signed_in.post(
            "/projects/ATL/tasks", json=_task(person, priority="whenever")
        )

        assert response.status_code == 422

    async def test_sub_statuses_default_to_empty(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)

        task = await _create(signed_in, person)

        assert task["sub_statuses"] == []
        assert task["sub_status_index"] is None

    async def test_sub_statuses_can_be_set_on_creation(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)

        task = await _create(signed_in, person, sub_statuses=["Draft", "Review", "Done"])

        assert task["sub_statuses"] == ["Draft", "Review", "Done"]
        assert task["sub_status_index"] == 0

    async def test_more_than_four_sub_statuses_is_refused(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)

        response = await signed_in.post(
            "/projects/ATL/tasks",
            json=_task(person, sub_statuses=["A", "B", "C", "D", "E"]),
        )

        assert response.status_code == 422

    async def test_a_blank_sub_status_label_is_refused(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)

        response = await signed_in.post(
            "/projects/ATL/tasks", json=_task(person, sub_statuses=["Draft", "  "])
        )

        assert response.status_code == 422

    async def test_lands_in_the_first_column(self, signed_in: AsyncClient) -> None:
        """Work enters a board at one end. Only moving is unrestricted."""
        person = await _setup(signed_in)

        task = await _create(signed_in, person)

        assert task["column_id"] == (await _columns(signed_in))[0]["id"]
        assert task["position"] == 0

    async def test_a_column_in_the_body_is_ignored(self, signed_in: AsyncClient) -> None:
        """Choosing a column on creation is not offered, so naming one is noise."""
        person = await _setup(signed_in)
        last = (await _columns(signed_in))[-1]["id"]

        task = await _create(signed_in, person, column_id=last)

        assert task["column_id"] == (await _columns(signed_in))[0]["id"]

    async def test_later_cards_stack_underneath(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)

        first = await _create(signed_in, person)
        second = await _create(signed_in, person, title="Back-fill invoices")

        assert [first["position"], second["position"]] == [0, 1]

    async def test_refuses_an_assignee_who_is_not_on_the_project(
        self, signed_in: AsyncClient
    ) -> None:
        await _setup(signed_in)
        outsider = (await signed_in.post("/people", json=ROHAN)).json()["id"]

        response = await signed_in.post("/projects/ATL/tasks", json=_task(outsider))

        assert response.status_code == 422
        assert response.json()["error"]["details"]["non_member_person_ids"] == [outsider]

    async def test_refuses_an_assignee_who_does_not_exist(self, signed_in: AsyncClient) -> None:
        await _setup(signed_in)

        assert (
            await signed_in.post("/projects/ATL/tasks", json=_task(UNKNOWN_ID))
        ).status_code == 422

    async def test_what_a_card_cannot_be_created_without(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)

        for field in ("title", "description", "type", "assignee_id"):
            body = _task(person)
            del body[field]

            response = await signed_in.post("/projects/ATL/tasks", json=body)

            assert response.status_code == 422, f"{field} should be required"

    async def test_the_refs_are_optional_and_default_to_null(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)

        task = await _create(signed_in, person)

        assert task["jira_ref"] is None
        assert task["pr_ref"] is None

    async def test_a_card_can_be_created_with_no_due_date(self, signed_in: AsyncClient) -> None:
        """CYLIST-17. A date nobody chose is worse than no date at all."""
        person = await _setup(signed_in)
        body = _task(person)
        del body["due_date"]

        response = await signed_in.post("/projects/ATL/tasks", json=body)

        assert response.status_code == 201, response.text
        assert response.json()["due_date"] is None

    async def test_an_explicit_null_due_date_is_taken_as_no_date(
        self, signed_in: AsyncClient
    ) -> None:
        """A form that sends every field sends the empty one too."""
        person = await _setup(signed_in)

        task = await _create(signed_in, person, due_date=None)

        assert task["due_date"] is None

    async def test_an_empty_ref_is_stored_as_nothing_at_all(self, signed_in: AsyncClient) -> None:
        """An untouched form field must not become an empty Jira reference."""
        person = await _setup(signed_in)

        task = await _create(signed_in, person, jira_ref="  ", pr_ref="")

        assert task["jira_ref"] is None
        assert task["pr_ref"] is None

    async def test_a_pasted_jira_link_survives_whole(self, signed_in: AsyncClient) -> None:
        """A ref field takes the URL, not just the key.

        The deep link a Jira board hands out is past 64 characters before the
        company's own hostname, which is what revision 0009 widened the column
        for. The board shows only the key the link ends in, but it can only
        link to somewhere it still has the whole address of.
        """
        person = await _setup(signed_in)
        link = (
            "https://acme-engineering.atlassian.net"
            "/jira/software/projects/ATL/boards/2?selectedIssue=ATL-41"
        )
        assert len(link) > 64

        task = await _create(signed_in, person, jira_ref=link)

        assert task["jira_ref"] == link

    async def test_a_ref_past_the_column_is_refused(self, signed_in: AsyncClient) -> None:
        """422 on the way in, rather than a 500 out of the database."""
        person = await _setup(signed_in)

        response = await signed_in.post(
            "/projects/ATL/tasks", json={**_task(person), "jira_ref": "x" * 201}
        )

        assert response.status_code == 422

    async def test_it_is_recorded_against_the_project(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person)

        entries = (await signed_in.get("/activity", params={"entity_type": "task"})).json()

        assert entries[0]["verb"] == "task.created"
        assert entries[0]["payload"]["reference"] == task["reference"]


class TestNumbering:
    async def test_numbers_start_at_one_and_count_up(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)

        numbers = [(await _create(signed_in, person))["number"] for _ in range(3)]

        assert numbers == [1, 2, 3]

    async def test_the_reference_reads_as_key_and_number(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)

        assert (await _create(signed_in, person))["reference"] == "ATL-1"

    async def test_numbers_are_per_project(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        await _create(signed_in, person)
        await signed_in.post("/projects", json=HERMES)
        await signed_in.put("/projects/HRM/members", json={"person_ids": [person]})

        elsewhere = (await signed_in.post("/projects/HRM/tasks", json=_task(person))).json()

        assert elsewhere["reference"] == "HRM-1"

    async def test_a_deleted_number_is_never_handed_out_again(self, signed_in: AsyncClient) -> None:
        """ATL-1 outlives its card in commit messages, so it names nothing else."""
        person = await _setup(signed_in)
        first = await _create(signed_in, person)
        await signed_in.delete(f"/tasks/{first['id']}")

        assert (await _create(signed_in, person))["reference"] == "ATL-2"

    async def test_renaming_the_project_renames_every_reference(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person)
        await signed_in.patch("/projects/ATL", json={"key": "ATLAS"})

        assert (await signed_in.get(f"/tasks/{task['id']}")).json()["reference"] == "ATLAS-1"


class TestAddressing:
    async def test_can_be_fetched_by_id(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person)

        assert (await signed_in.get(f"/tasks/{task['id']}")).json()["reference"] == "ATL-1"

    async def test_can_be_fetched_by_reference(self, signed_in: AsyncClient) -> None:
        """So `cylist task ATL-1` needs no lookup first — this is what Phase 6 uses."""
        person = await _setup(signed_in)
        task = await _create(signed_in, person)

        assert (await signed_in.get("/tasks/ATL-1")).json()["id"] == task["id"]

    async def test_the_key_half_is_case_insensitive(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        await _create(signed_in, person)

        assert (await signed_in.get("/tasks/atl-1")).status_code == 200

    async def test_an_unknown_reference_is_a_clean_404(self, signed_in: AsyncClient) -> None:
        response = await signed_in.get("/tasks/ATL-99")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    async def test_a_reference_with_no_number_is_a_404_not_a_crash(
        self, signed_in: AsyncClient
    ) -> None:
        assert (await signed_in.get("/tasks/nonsense")).status_code == 404

    async def test_a_reference_with_a_word_where_the_number_goes_is_a_404(
        self, signed_in: AsyncClient
    ) -> None:
        assert (await signed_in.get("/tasks/ATL-abc")).status_code == 404

    async def test_the_reference_works_for_writes_too(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        await _create(signed_in, person)

        response = await signed_in.patch("/tasks/ATL-1", json={"title": "Renamed"})

        assert response.json()["title"] == "Renamed"


class TestUpdating:
    async def test_changes_only_the_fields_given(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person)

        updated = (
            await signed_in.patch(f"/tasks/{task['id']}", json={"title": "Dedupe webhooks"})
        ).json()

        assert updated["title"] == "Dedupe webhooks"
        assert updated["description"] == task["description"]

    async def test_priority_can_be_changed(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person)
        assert task["priority"] == "someday"

        updated = (await signed_in.patch(f"/tasks/{task['id']}", json={"priority": "asap"})).json()

        assert updated["priority"] == "asap"

    async def test_sub_statuses_can_be_added(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person)

        updated = (
            await signed_in.patch(
                f"/tasks/{task['id']}", json={"sub_statuses": ["Draft", "Review"]}
            )
        ).json()

        assert updated["sub_statuses"] == ["Draft", "Review"]
        assert updated["sub_status_index"] == 0

    async def test_sub_statuses_can_be_cleared(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person, sub_statuses=["Draft", "Review"])

        updated = (await signed_in.patch(f"/tasks/{task['id']}", json={"sub_statuses": []})).json()

        assert updated["sub_statuses"] == []
        assert updated["sub_status_index"] is None

    async def test_shrinking_the_list_pulls_the_current_stage_back(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person, sub_statuses=["Draft", "Review", "Testing", "Done"])
        far_along = (
            await signed_in.post(f"/tasks/{task['id']}/sub-status", json={"index": 2})
        ).json()
        assert far_along["sub_status_index"] == 2

        updated = (
            await signed_in.patch(
                f"/tasks/{task['id']}", json={"sub_statuses": ["Draft", "Review"]}
            )
        ).json()

        assert updated["sub_statuses"] == ["Draft", "Review"]
        assert updated["sub_status_index"] == 1

    async def test_reordering_stages_can_take_the_current_one_with_it(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person, sub_statuses=["Draft", "Review", "Done"])
        await signed_in.post(f"/tasks/{task['id']}/sub-status", json={"index": 1})

        updated = (
            await signed_in.patch(
                f"/tasks/{task['id']}",
                json={"sub_statuses": ["Review", "Draft", "Done"], "sub_status_index": 0},
            )
        ).json()

        assert updated["sub_statuses"] == ["Review", "Draft", "Done"]
        assert updated["sub_status_index"] == 0

    async def test_the_current_stage_can_be_set_without_touching_the_labels(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person, sub_statuses=["Draft", "Review", "Done"])

        updated = (
            await signed_in.patch(f"/tasks/{task['id']}", json={"sub_status_index": 2})
        ).json()

        assert updated["sub_statuses"] == ["Draft", "Review", "Done"]
        assert updated["sub_status_index"] == 2

    async def test_a_current_stage_past_the_new_list_is_refused(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person, sub_statuses=["Draft", "Review", "Done"])

        response = await signed_in.patch(
            f"/tasks/{task['id']}",
            json={"sub_statuses": ["Draft", "Review"], "sub_status_index": 2},
        )

        assert response.status_code == 422

    async def test_clearing_the_stages_ignores_a_current_stage_sent_with_it(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person, sub_statuses=["Draft", "Review"])

        updated = (
            await signed_in.patch(
                f"/tasks/{task['id']}", json={"sub_statuses": [], "sub_status_index": 0}
            )
        ).json()

        assert updated["sub_statuses"] == []
        assert updated["sub_status_index"] is None

    async def test_the_assignee_can_be_handed_over(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        rohan = (await signed_in.post("/people", json=ROHAN)).json()["id"]
        await signed_in.put("/projects/ATL/members", json={"person_ids": [person, rohan]})
        task = await _create(signed_in, person)

        updated = (
            await signed_in.patch(f"/tasks/{task['id']}", json={"assignee_id": rohan})
        ).json()

        assert updated["assignee"]["name"] == "Rohan S"

    async def test_refuses_an_assignee_who_is_not_on_the_project(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _setup(signed_in)
        outsider = (await signed_in.post("/people", json=ROHAN)).json()["id"]
        task = await _create(signed_in, person)

        response = await signed_in.patch(f"/tasks/{task['id']}", json={"assignee_id": outsider})

        assert response.status_code == 422

    async def test_a_ref_can_be_cleared(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person, jira_ref="ATL-41")

        updated = (await signed_in.patch(f"/tasks/{task['id']}", json={"jira_ref": None})).json()

        assert updated["jira_ref"] is None

    async def test_a_due_date_can_be_cleared(self, signed_in: AsyncClient) -> None:
        """CYLIST-17. Unlike a title, a date is a thing a card can be without."""
        person = await _setup(signed_in)
        task = await _create(signed_in, person)
        assert task["due_date"] == "2026-09-01"

        updated = (await signed_in.patch(f"/tasks/{task['id']}", json={"due_date": None})).json()

        assert updated["due_date"] is None

    async def test_a_due_date_can_be_put_on_a_card_that_had_none(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person, due_date=None)

        updated = (
            await signed_in.patch(f"/tasks/{task['id']}", json={"due_date": "2026-10-01"})
        ).json()

        assert updated["due_date"] == "2026-10-01"

    async def test_a_null_required_field_leaves_it_alone(self, signed_in: AsyncClient) -> None:
        """A client echoing a field back as null wanted no change, not an empty title."""
        person = await _setup(signed_in)
        task = await _create(signed_in, person)

        updated = (
            await signed_in.patch(f"/tasks/{task['id']}", json={"title": None, "type": "chore"})
        ).json()

        assert updated["title"] == task["title"]
        assert updated["type"] == "chore"

    async def test_status_is_not_changeable_here(self, signed_in: AsyncClient) -> None:
        """It has its own endpoint, which is the only one that can demand a reason."""
        person = await _setup(signed_in)
        task = await _create(signed_in, person)

        updated = (await signed_in.patch(f"/tasks/{task['id']}", json={"status": "blocked"})).json()

        assert updated["status"] == "active"


class TestSubStatus:
    async def test_moving_forwards_lands_on_the_stage_asked_for(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person, sub_statuses=["Draft", "Review", "Done"])

        moved = (await signed_in.post(f"/tasks/{task['id']}/sub-status", json={"index": 2})).json()

        assert moved["sub_status_index"] == 2

    async def test_moving_backwards_is_allowed(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person, sub_statuses=["Draft", "Review", "Done"])
        await signed_in.post(f"/tasks/{task['id']}/sub-status", json={"index": 2})

        back = (await signed_in.post(f"/tasks/{task['id']}/sub-status", json={"index": 0})).json()

        assert back["sub_status_index"] == 0

    async def test_a_stage_past_the_end_is_refused(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person, sub_statuses=["Draft", "Done"])

        response = await signed_in.post(f"/tasks/{task['id']}/sub-status", json={"index": 2})

        assert response.status_code == 422

    async def test_a_negative_stage_is_refused(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person, sub_statuses=["Draft", "Done"])

        response = await signed_in.post(f"/tasks/{task['id']}/sub-status", json={"index": -1})

        assert response.status_code == 422

    async def test_moving_without_sub_statuses_is_refused(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person)

        response = await signed_in.post(f"/tasks/{task['id']}/sub-status", json={"index": 0})

        assert response.status_code == 422


class TestDeleting:
    async def test_removes_the_card(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person)

        assert (await signed_in.delete(f"/tasks/{task['id']}")).status_code == 200
        assert (await signed_in.get(f"/tasks/{task['id']}")).status_code == 404

    async def test_closes_the_gap_it_leaves(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        first = await _create(signed_in, person)
        await _create(signed_in, person, title="Second")
        third = await _create(signed_in, person, title="Third")

        await signed_in.delete(f"/tasks/{first['id']}")

        remaining = (await signed_in.get("/projects/ATL/tasks")).json()
        assert [task["position"] for task in remaining] == [0, 1]
        assert remaining[1]["id"] == third["id"]

    async def test_it_is_recorded_against_the_project(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person)

        await signed_in.delete(f"/tasks/{task['id']}")

        entries = (await signed_in.get("/activity", params={"entity_type": "task"})).json()
        assert entries[0]["verb"] == "task.deleted"
        assert entries[0]["payload"] == {"reference": "ATL-1"}


class TestFinishedByTheLastColumn:
    """A card is done by being in the board's last column, and it says so.

    The board already answered "is this card done" by where the card was; what
    it could not answer is *when*, or that a card was ever done at all once it
    had been dragged back out. Both are `finished_at`.
    """

    async def test_arriving_in_the_last_column_finishes_a_card(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person)
        assert task["finished_at"] is None
        done = (await _columns(signed_in))[-1]["id"]

        moved = (
            await signed_in.post(
                f"/tasks/{task['id']}/move", json={"column_id": done, "position": 0}
            )
        ).json()

        assert moved["finished_at"] is not None

    async def test_leaving_it_reopens_the_card(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person)
        columns = await _columns(signed_in)
        await signed_in.post(
            f"/tasks/{task['id']}/move", json={"column_id": columns[-1]["id"], "position": 0}
        )

        moved = (
            await signed_in.post(
                f"/tasks/{task['id']}/move", json={"column_id": columns[0]["id"], "position": 0}
            )
        ).json()

        assert moved["finished_at"] is None

    async def test_a_move_within_the_last_column_keeps_the_original_time(
        self, signed_in: AsyncClient
    ) -> None:
        """Reordering is not a second finishing: the card never stopped being done."""
        person = await _setup(signed_in)
        first = await _create(signed_in, person, title="First")
        second = await _create(signed_in, person, title="Second")
        done = (await _columns(signed_in))[-1]["id"]
        await signed_in.post(f"/tasks/{second['id']}/move", json={"column_id": done, "position": 0})
        finished = (
            await signed_in.post(
                f"/tasks/{first['id']}/move", json={"column_id": done, "position": 0}
            )
        ).json()["finished_at"]

        moved = (
            await signed_in.post(
                f"/tasks/{first['id']}/move", json={"column_id": done, "position": 1}
            )
        ).json()

        assert moved["finished_at"] == finished

    async def test_the_column_that_was_last_stops_finishing_cards(
        self, signed_in: AsyncClient
    ) -> None:
        """Last is a position, not a column. A new column to the right takes it."""
        person = await _setup(signed_in)
        was_last = (await _columns(signed_in))[-1]["id"]
        await signed_in.post(
            "/projects/ATL/columns",
            json={"name": "In staging", "description": "Deployed where it can be looked at."},
        )
        task = await _create(signed_in, person)

        moved = (
            await signed_in.post(
                f"/tasks/{task['id']}/move", json={"column_id": was_last, "position": 0}
            )
        ).json()

        assert moved["finished_at"] is None

    async def test_a_card_is_still_not_finished_by_being_ticked_off(
        self, signed_in: AsyncClient
    ) -> None:
        """Done is where a card is. `finish` is the sub-task's gesture, and says so."""
        person = await _setup(signed_in)
        task = await _create(signed_in, person)

        refused = await signed_in.post(f"/tasks/{task['id']}/finish", json={"finished": True})

        assert refused.status_code == 422
        assert "last column" in refused.json()["error"]["message"]


class TestMoving:
    async def test_moves_a_card_to_another_column(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person)
        done = (await _columns(signed_in))[1]["id"]

        moved = (
            await signed_in.post(
                f"/tasks/{task['id']}/move", json={"column_id": done, "position": 0}
            )
        ).json()

        assert moved["column_id"] == done
        assert moved["position"] == 0

    async def test_a_card_can_go_backwards(self, signed_in: AsyncClient) -> None:
        """Nothing about moving is one-way; only creation is constrained."""
        person = await _setup(signed_in)
        task = await _create(signed_in, person)
        todo, done = (column["id"] for column in await _columns(signed_in))
        await signed_in.post(f"/tasks/{task['id']}/move", json={"column_id": done, "position": 0})

        moved = (
            await signed_in.post(
                f"/tasks/{task['id']}/move", json={"column_id": todo, "position": 0}
            )
        ).json()

        assert moved["column_id"] == todo

    async def test_changing_column_starts_the_stages_again(self, signed_in: AsyncClient) -> None:
        """Stages are progress through a column, so a new column has none yet."""
        person = await _setup(signed_in)
        task = await _create(signed_in, person, sub_statuses=["Draft", "Review", "Merged"])
        done = (await _columns(signed_in))[1]["id"]
        await signed_in.post(f"/tasks/{task['id']}/sub-status", json={"index": 2})

        moved = (
            await signed_in.post(
                f"/tasks/{task['id']}/move", json={"column_id": done, "position": 0}
            )
        ).json()

        assert moved["sub_status_index"] == 0

    async def test_moving_within_a_column_keeps_the_stage(self, signed_in: AsyncClient) -> None:
        """Nothing has been arrived at, so there is nothing to start again."""
        person = await _setup(signed_in)
        task = await _create(signed_in, person, sub_statuses=["Draft", "Review", "Merged"])
        todo = (await _columns(signed_in))[0]["id"]
        await signed_in.post(f"/tasks/{task['id']}/sub-status", json={"index": 2})

        moved = (
            await signed_in.post(
                f"/tasks/{task['id']}/move", json={"column_id": todo, "position": 0}
            )
        ).json()

        assert moved["sub_status_index"] == 2

    async def test_a_card_without_stages_is_unaffected_by_a_move(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person)
        done = (await _columns(signed_in))[1]["id"]

        moved = (
            await signed_in.post(
                f"/tasks/{task['id']}/move", json={"column_id": done, "position": 0}
            )
        ).json()

        assert moved["sub_status_index"] is None

    async def test_inserting_pushes_the_cards_below_it_down(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        first = await _create(signed_in, person, title="First")
        second = await _create(signed_in, person, title="Second")
        todo = (await _columns(signed_in))[0]["id"]

        await signed_in.post(f"/tasks/{second['id']}/move", json={"column_id": todo, "position": 0})

        board = (await signed_in.get("/projects/ATL/tasks")).json()
        assert [task["id"] for task in board] == [second["id"], first["id"]]
        assert [task["position"] for task in board] == [0, 1]

    async def test_a_position_past_the_end_lands_at_the_bottom(
        self, signed_in: AsyncClient
    ) -> None:
        """So "drop at the end" needs no length lookup first."""
        person = await _setup(signed_in)
        await _create(signed_in, person, title="First")
        second = await _create(signed_in, person, title="Second")
        todo = (await _columns(signed_in))[0]["id"]

        moved = (
            await signed_in.post(
                f"/tasks/{second['id']}/move", json={"column_id": todo, "position": 99}
            )
        ).json()

        assert moved["position"] == 1

    async def test_the_source_column_closes_its_gap(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        first = await _create(signed_in, person, title="First")
        await _create(signed_in, person, title="Second")
        done = (await _columns(signed_in))[1]["id"]

        await signed_in.post(f"/tasks/{first['id']}/move", json={"column_id": done, "position": 0})

        board = (await signed_in.get("/projects/ATL/tasks")).json()
        assert [(task["title"], task["position"]) for task in board] == [
            ("Second", 0),
            ("First", 0),
        ]

    async def test_refuses_a_column_on_another_board(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person)
        await signed_in.post("/projects", json=HERMES)
        theirs = (await _columns(signed_in, "HRM"))[0]["id"]

        response = await signed_in.post(
            f"/tasks/{task['id']}/move", json={"column_id": theirs, "position": 0}
        )

        assert response.status_code == 422

    async def test_an_unknown_column_is_a_clean_404(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person)

        response = await signed_in.post(
            f"/tasks/{task['id']}/move", json={"column_id": UNKNOWN_ID, "position": 0}
        )

        assert response.status_code == 404

    async def test_it_is_recorded_against_the_project(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person)
        done = (await _columns(signed_in))[1]["id"]

        await signed_in.post(f"/tasks/{task['id']}/move", json={"column_id": done, "position": 0})

        entries = (await signed_in.get("/activity", params={"entity_type": "task"})).json()
        assert entries[0]["verb"] == "task.moved"
        assert entries[0]["payload"]["column_id"] == done


class TestListing:
    async def test_returns_the_board_in_reading_order(self, signed_in: AsyncClient) -> None:
        """Columns left to right, cards top to bottom — ready to group and draw."""
        person = await _setup(signed_in)
        first = await _create(signed_in, person, title="First")
        await _create(signed_in, person, title="Second")
        done = (await _columns(signed_in))[1]["id"]
        await signed_in.post(f"/tasks/{first['id']}/move", json={"column_id": done, "position": 0})

        board = (await signed_in.get("/projects/ATL/tasks")).json()

        assert [task["title"] for task in board] == ["Second", "First"]

    async def test_is_scoped_to_one_project(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        await _create(signed_in, person)
        await signed_in.post("/projects", json=HERMES)
        await signed_in.put("/projects/HRM/members", json={"person_ids": [person]})
        await signed_in.post("/projects/HRM/tasks", json=_task(person, title="Elsewhere"))

        assert len((await signed_in.get("/projects/ATL/tasks")).json()) == 1

    async def test_an_empty_board_lists_nothing(self, signed_in: AsyncClient) -> None:
        await _setup(signed_in)

        assert (await signed_in.get("/projects/ATL/tasks")).json() == []


class TestCascade:
    async def test_deleting_a_project_row_takes_its_board_with_it(
        self, signed_in: AsyncClient, session: AsyncSession
    ) -> None:
        """Archiving is the normal path, but the foreign keys must still be sound.

        Reaches past the API deliberately: nothing routes to a hard delete, and
        the ordering of these cascades is exactly what could go wrong unnoticed.
        """
        person = await _setup(signed_in)
        await _create(signed_in, person)

        project = await session.scalar(select(Project).where(Project.key == "ATL"))
        assert project is not None
        await session.delete(project)
        await session.flush()

        assert list(await session.scalars(select(Task))) == []
        assert list(await session.scalars(select(BoardColumn))) == []

    async def test_a_person_holding_tasks_cannot_be_deleted_from_under_them(
        self, signed_in: AsyncClient, session: AsyncSession
    ) -> None:
        """People are archived rather than deleted, and the schema insists."""
        person_id = await _setup(signed_in)
        await _create(signed_in, person_id)

        person = await session.get(Person, UUID(person_id))
        assert person is not None
        await session.delete(person)

        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()
