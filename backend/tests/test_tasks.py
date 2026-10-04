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
from tests.conftest import OWNER_NAME

ATLAS = {"key": "ATL", "name": "Atlas Billing Migration"}
HERMES = {"key": "HRM", "name": "Hermes Notifications"}
ADITI = {
    "name": "Aditi K",
    "kind": "team",
    "title": "Backend engineer",
    "responsibilities": "Payments and webhooks.",
}
ROHAN = {
    "name": "Rohan S",
    "kind": "team",
    "title": "Backend engineer",
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


def _no_assignee(**overrides: Any) -> dict[str, Any]:
    """A card that says nothing about who owns it."""
    body = _task("", **overrides)
    del body["assignee_id"]
    return body


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

    async def test_priority_defaults_to_p3(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)

        task = await _create(signed_in, person)

        assert task["priority"] == "p3"

    async def test_priority_can_be_set_on_creation(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)

        task = await _create(signed_in, person, priority="p0")

        assert task["priority"] == "p0"

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
        """``assignee_id`` is not among them since CYLIST-47 — see
        :class:`TestWhoTheCardLandsOn`, which is where the card goes without
        one."""
        person = await _setup(signed_in)

        for field in ("title", "description", "type"):
            body = _task(person)
            del body[field]

            response = await signed_in.post("/projects/ATL/tasks", json=body)

            assert response.status_code == 422, f"{field} should be required"

    async def test_the_refs_are_optional_and_default_to_null(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)

        task = await _create(signed_in, person)

        assert task["jira_ref"] is None
        assert task["pr_refs"] == []

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

        task = await _create(signed_in, person, jira_ref="  ", pr_refs=["", "   "])

        assert task["jira_ref"] is None
        assert task["pr_refs"] == []

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


class TestWhoTheCardLandsOn:
    """CYLIST-47: a card goes to whoever wrote it unless it names somebody.

    A card names somebody or it is not a card, so an omitted ``assignee_id``
    is not a card belonging to nobody — it is the question "whose, then?",
    and the answer is the person writing it. Handing it on is a ``PATCH``
    away, and covered under :class:`TestUpdating`.

    These tests create the project and leave its membership alone, so the
    caller is on it. ``_setup`` elsewhere in this file replaces the member
    list with one other person, which is exactly the case the last two cover.
    """

    async def test_a_card_with_no_assignee_goes_to_whoever_created_it(
        self, signed_in: AsyncClient
    ) -> None:
        await signed_in.post("/projects", json=ATLAS)

        response = await signed_in.post("/projects/ATL/tasks", json=_no_assignee())

        assert response.status_code == 201, response.text
        assert response.json()["assignee"]["name"] == OWNER_NAME

    async def test_naming_somebody_else_still_puts_it_on_them(self, signed_in: AsyncClient) -> None:
        """The default is a default, not a rule about who may own work."""
        await signed_in.post("/projects", json=ATLAS)
        aditi = (await signed_in.post("/people", json=ADITI)).json()["id"]
        await signed_in.put("/projects/ATL/members", json={"person_ids": [aditi]})

        task = await _create(signed_in, aditi)

        assert task["assignee"]["name"] == "Aditi K"

    async def test_a_card_can_be_handed_to_the_agent(self, signed_in: AsyncClient) -> None:
        """Which is the whole reason the agent is in the directory."""
        await signed_in.post("/projects", json=ATLAS)
        members = (await signed_in.get("/projects/ATL/members")).json()["members"]
        agent = next(person for person in members if person["is_agent"])

        task = await _create(signed_in, agent["id"])

        assert task["assignee"]["name"] == "Agent"

    async def test_a_subtask_with_no_assignee_goes_to_whoever_split_the_card(
        self, signed_in: AsyncClient
    ) -> None:
        await signed_in.post("/projects", json=ATLAS)
        parent = (await signed_in.post("/projects/ATL/tasks", json=_no_assignee())).json()

        response = await signed_in.post(f"/tasks/{parent['id']}/subtasks", json=_no_assignee())

        assert response.status_code == 201, response.text
        assert response.json()["assignee"]["name"] == OWNER_NAME

    async def test_a_creator_who_is_not_on_the_project_has_to_name_somebody(
        self, signed_in: AsyncClient
    ) -> None:
        """The default steps aside rather than putting a card on an outsider.

        ``_setup`` leaves the board with one member who is not the caller, so
        there is somebody to name — the message says to name them.
        """
        await _setup(signed_in)

        response = await signed_in.post("/projects/ATL/tasks", json=_no_assignee())

        assert response.status_code == 422
        assert "not on ATL" in response.json()["error"]["message"]

    async def test_a_bootstrap_session_has_to_name_somebody(
        self, bootstrapped: AsyncClient
    ) -> None:
        """The one caller who is not anybody: there is nobody to default to."""
        await bootstrapped.post("/projects", json=ATLAS)
        members = (await bootstrapped.get("/projects/ATL/members")).json()["members"]

        response = await bootstrapped.post("/projects/ATL/tasks", json=_no_assignee())

        assert response.status_code == 422
        assert response.json()["error"]["message"].startswith("Say who this card is for.")
        # And the agent it could have guessed at is right there, unnamed: a
        # card put on a machine because nobody said otherwise would be work
        # nobody agreed to do.
        assert [person["is_agent"] for person in members] == [True]


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
        assert task["priority"] == "p3"

        updated = (await signed_in.patch(f"/tasks/{task['id']}", json={"priority": "p1"})).json()

        assert updated["priority"] == "p1"

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


class TestMovingToAnotherProject:
    async def test_moves_a_card_onto_another_board(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        await signed_in.post("/projects", json=HERMES)
        await signed_in.put("/projects/HRM/members", json={"person_ids": [person]})
        task = await _create(signed_in, person)

        moved = (
            await signed_in.post(f"/tasks/{task['id']}/project", json={"project_id": "HRM"})
        ).json()

        assert moved["reference"].startswith("HRM-")
        board = (await signed_in.get("/projects/HRM/tasks")).json()
        assert [card["id"] for card in board] == [task["id"]]
        source_board = (await signed_in.get("/projects/ATL/tasks")).json()
        assert source_board == []

    async def test_lands_in_the_named_column(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        await signed_in.post("/projects", json=HERMES)
        await signed_in.put("/projects/HRM/members", json={"person_ids": [person]})
        task = await _create(signed_in, person)
        their_done = (await _columns(signed_in, "HRM"))[1]["id"]

        moved = (
            await signed_in.post(
                f"/tasks/{task['id']}/project",
                json={"project_id": "HRM", "column_id": their_done},
            )
        ).json()

        assert moved["column_id"] == their_done

    async def test_clears_goal_and_template_and_resets_stages(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _setup(signed_in)
        await signed_in.post("/projects", json=HERMES)
        await signed_in.put("/projects/HRM/members", json={"person_ids": [person]})
        task = await _create(signed_in, person, sub_statuses=["Draft", "Review"])
        await signed_in.post(f"/tasks/{task['id']}/sub-status", json={"index": 1})

        moved = (
            await signed_in.post(f"/tasks/{task['id']}/project", json={"project_id": "HRM"})
        ).json()

        assert moved["goal_id"] is None
        assert moved["template_id"] is None
        assert moved["sub_statuses"] == []
        assert moved["sub_status_index"] is None

    async def test_refuses_its_own_project(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person)

        response = await signed_in.post(
            f"/tasks/{task['id']}/project", json={"project_id": "ATL"}
        )

        assert response.status_code == 422

    async def test_refuses_an_assignee_not_on_the_destination(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _setup(signed_in)
        await signed_in.post("/projects", json=HERMES)
        task = await _create(signed_in, person)

        response = await signed_in.post(
            f"/tasks/{task['id']}/project", json={"project_id": "HRM"}
        )

        assert response.status_code == 422

    async def test_refuses_a_column_from_a_third_board(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        await signed_in.post("/projects", json=HERMES)
        await signed_in.put("/projects/HRM/members", json={"person_ids": [person]})
        task = await _create(signed_in, person)
        atl_column = (await _columns(signed_in, "ATL"))[0]["id"]

        response = await signed_in.post(
            f"/tasks/{task['id']}/project",
            json={"project_id": "HRM", "column_id": atl_column},
        )

        assert response.status_code == 422

    async def test_a_sub_task_cannot_be_moved(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        await signed_in.post("/projects", json=HERMES)
        await signed_in.put("/projects/HRM/members", json={"person_ids": [person]})
        task = await _create(signed_in, person)
        sub = (
            await signed_in.post(
                f"/tasks/{task['id']}/subtasks", json=_task(person, title="Part one")
            )
        ).json()

        response = await signed_in.post(
            f"/tasks/{sub['id']}/project", json={"project_id": "HRM"}
        )

        assert response.status_code == 422

    async def test_it_is_recorded_against_the_project(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        await signed_in.post("/projects", json=HERMES)
        await signed_in.put("/projects/HRM/members", json={"person_ids": [person]})
        task = await _create(signed_in, person)

        await signed_in.post(f"/tasks/{task['id']}/project", json={"project_id": "HRM"})

        entries = (await signed_in.get("/activity", params={"entity_type": "task"})).json()
        assert entries[0]["verb"] == "task.project_changed"


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


class TestColumnDueDates:
    """A card can be dated per column, not only at the end of the board.

    The board a card is on is what these dates are read against: which of them
    are behind the card, and which one it is working towards now.
    """

    @staticmethod
    async def _three(client: AsyncClient) -> list[dict[str, Any]]:
        """A board of To do → Review → Done, so there is a middle to date."""
        await client.post(
            "/projects/ATL/columns",
            json={"name": "Review", "description": "Written and waiting on a second pair of eyes."},
        )
        board = await _columns(client)
        by_name = {column["name"]: column["id"] for column in board}
        ordered = [by_name["To do"], by_name["Review"], by_name["Done"]]
        await client.put("/projects/ATL/columns/order", json={"column_ids": ordered})
        return list((await client.get("/projects/ATL/columns")).json()["columns"])

    async def test_dates_are_read_in_board_order_and_named(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        todo, review, _ = await self._three(signed_in)

        task = await _create(
            signed_in,
            person,
            column_due_dates=[
                {"column_id": review["id"], "due_date": "2026-08-20"},
                {"column_id": todo["id"], "due_date": "2026-08-10"},
            ],
        )

        assert [
            (entry["column_name"], entry["due_date"]) for entry in task["column_due_dates"]
        ] == [
            ("To do", "2026-08-10"),
            ("Review", "2026-08-20"),
        ]

    async def test_a_column_the_card_sits_in_is_already_met(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        todo, review, _ = await self._three(signed_in)

        task = await _create(
            signed_in,
            person,
            column_due_dates=[
                {"column_id": todo["id"], "due_date": "2026-08-10"},
                {"column_id": review["id"], "due_date": "2026-08-20"},
            ],
        )

        assert [entry["met"] for entry in task["column_due_dates"]] == [True, False]

    async def test_the_next_date_is_the_soonest_still_owed(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        _, review, _ = await self._three(signed_in)

        task = await _create(
            signed_in,
            person,
            due_date="2026-09-01",
            column_due_dates=[{"column_id": review["id"], "due_date": "2026-08-20"}],
        )

        assert task["next_due_date"] == "2026-08-20"

    async def test_reaching_the_column_settles_its_date(self, signed_in: AsyncClient) -> None:
        """The card's own comment on CYLIST-25: the prompt goes away when it is met."""
        person = await _setup(signed_in)
        _, review, _ = await self._three(signed_in)
        task = await _create(
            signed_in,
            person,
            due_date="2026-09-01",
            column_due_dates=[{"column_id": review["id"], "due_date": "2026-08-20"}],
        )

        moved = (
            await signed_in.post(
                f"/tasks/{task['id']}/move", json={"column_id": review["id"], "position": 0}
            )
        ).json()

        assert moved["column_due_dates"][0]["met"] is True
        assert moved["next_due_date"] == "2026-09-01"

    async def test_a_card_in_the_last_column_owes_nothing(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        _, review, done = await self._three(signed_in)
        task = await _create(
            signed_in,
            person,
            due_date="2026-09-01",
            column_due_dates=[{"column_id": review["id"], "due_date": "2026-08-20"}],
        )

        moved = (
            await signed_in.post(
                f"/tasks/{task['id']}/move", json={"column_id": done["id"], "position": 0}
            )
        ).json()

        assert moved["next_due_date"] is None

    async def test_the_last_column_is_the_cards_own_due_date(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        done = (await _columns(signed_in))[-1]["id"]

        response = await signed_in.post(
            "/projects/ATL/tasks",
            json=_task(person, column_due_dates=[{"column_id": done, "due_date": "2026-08-20"}]),
        )

        assert response.status_code == 422
        assert "due_date" in response.json()["error"]["message"]

    async def test_a_column_named_twice_is_refused(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        todo = (await _columns(signed_in))[0]["id"]

        response = await signed_in.post(
            "/projects/ATL/tasks",
            json=_task(
                person,
                column_due_dates=[
                    {"column_id": todo, "due_date": "2026-08-10"},
                    {"column_id": todo, "due_date": "2026-08-11"},
                ],
            ),
        )

        assert response.status_code == 422

    async def test_a_column_on_another_board_is_refused(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person)
        await signed_in.post("/projects", json=HERMES)
        theirs = (await _columns(signed_in, "HRM"))[0]["id"]

        response = await signed_in.patch(
            f"/tasks/{task['id']}",
            json={"column_due_dates": [{"column_id": theirs, "due_date": "2026-08-20"}]},
        )

        assert response.status_code == 422

    async def test_a_subtask_passes_through_no_columns(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        todo = (await _columns(signed_in))[0]["id"]
        task = await _create(signed_in, person)

        response = await signed_in.post(
            f"/tasks/{task['reference']}/subtasks",
            json=_task(
                person,
                title="Write the migration",
                column_due_dates=[{"column_id": todo, "due_date": "2026-08-20"}],
            ),
        )

        assert response.status_code == 422

    async def test_the_whole_set_is_replaced(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        todo, review, _ = await self._three(signed_in)
        task = await _create(
            signed_in,
            person,
            column_due_dates=[{"column_id": todo["id"], "due_date": "2026-08-10"}],
        )

        updated = (
            await signed_in.patch(
                f"/tasks/{task['id']}",
                json={"column_due_dates": [{"column_id": review["id"], "due_date": "2026-08-20"}]},
            )
        ).json()

        assert [entry["column_name"] for entry in updated["column_due_dates"]] == ["Review"]

    async def test_an_empty_list_takes_them_all_off(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        todo = (await _columns(signed_in))[0]["id"]
        task = await _create(
            signed_in, person, column_due_dates=[{"column_id": todo, "due_date": "2026-08-10"}]
        )

        updated = (
            await signed_in.patch(f"/tasks/{task['id']}", json={"column_due_dates": []})
        ).json()

        assert updated["column_due_dates"] == []

    async def test_leaving_them_out_leaves_them_alone(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        todo = (await _columns(signed_in))[0]["id"]
        task = await _create(
            signed_in, person, column_due_dates=[{"column_id": todo, "due_date": "2026-08-10"}]
        )

        updated = (
            await signed_in.patch(f"/tasks/{task['id']}", json={"title": "Dedupe webhooks"})
        ).json()

        assert len(updated["column_due_dates"]) == 1

    async def test_a_deleted_column_takes_its_dates_with_it(
        self, signed_in: AsyncClient, session: AsyncSession
    ) -> None:
        """A deadline for a column that no longer exists is a date with nothing
        to be due in — unlike the card in a column, which blocks the delete."""
        person = await _setup(signed_in)
        _, review, _ = await self._three(signed_in)
        task = await _create(
            signed_in,
            person,
            column_due_dates=[{"column_id": review["id"], "due_date": "2026-08-20"}],
        )

        assert (await signed_in.delete(f"/columns/{review['id']}")).status_code == 200
        refreshed = (await signed_in.get(f"/tasks/{task['id']}")).json()
        assert refreshed["column_due_dates"] == []


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


class TestThePullRequestsOnACard:
    """CYLIST-63. One card's work routinely lands as more than one pull request.

    The list is sent whole, the way ``sub_statuses`` and ``column_due_dates``
    are: adding one is a longer list, removing one is a shorter list, and
    clearing them is the empty list. There is no add-one endpoint because
    there is no ordering question a client cannot already answer for itself.
    """

    async def test_a_card_can_be_created_naming_several(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)

        task = await _create(signed_in, person, pr_refs=["#212", "#219"])

        assert task["pr_refs"] == ["#212", "#219"]

    async def test_the_order_they_were_given_in_is_kept(self, signed_in: AsyncClient) -> None:
        """A card's first pull request is usually its main one, and no other
        order is better than the one somebody chose."""
        person = await _setup(signed_in)

        task = await _create(signed_in, person, pr_refs=["#9", "#1", "#5"])

        assert task["pr_refs"] == ["#9", "#1", "#5"]

    async def test_one_can_be_added_to_a_card_that_has_one(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person, pr_refs=["#212"])

        updated = (
            await signed_in.patch(f"/tasks/{task['id']}", json={"pr_refs": ["#212", "#219"]})
        ).json()

        assert updated["pr_refs"] == ["#212", "#219"]

    async def test_one_can_be_removed_and_the_rest_stay(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person, pr_refs=["#212", "#219", "#231"])

        updated = (
            await signed_in.patch(f"/tasks/{task['id']}", json={"pr_refs": ["#212", "#231"]})
        ).json()

        assert updated["pr_refs"] == ["#212", "#231"]

    async def test_they_can_all_be_taken_off(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person, pr_refs=["#212", "#219"])

        updated = (await signed_in.patch(f"/tasks/{task['id']}", json={"pr_refs": []})).json()

        assert updated["pr_refs"] == []

    async def test_leaving_the_field_out_leaves_them_alone(self, signed_in: AsyncClient) -> None:
        """A PATCH of the title must not quietly drop the card's pull requests."""
        person = await _setup(signed_in)
        task = await _create(signed_in, person, pr_refs=["#212"])

        updated = (
            await signed_in.patch(f"/tasks/{task['id']}", json={"title": "Dedupe webhooks"})
        ).json()

        assert updated["pr_refs"] == ["#212"]

    async def test_a_null_is_read_as_an_echo_rather_than_as_clear_them(
        self, signed_in: AsyncClient
    ) -> None:
        """Unlike ``jira_ref``: the empty list already says "none", so a null
        here is a client sending back a field it never filled in."""
        person = await _setup(signed_in)
        task = await _create(signed_in, person, pr_refs=["#212"])

        updated = (await signed_in.patch(f"/tasks/{task['id']}", json={"pr_refs": None})).json()

        assert updated["pr_refs"] == ["#212"]

    async def test_the_same_one_twice_is_stored_once(self, signed_in: AsyncClient) -> None:
        """Showing it twice would only invite the reader to look for a
        difference between them."""
        person = await _setup(signed_in)

        task = await _create(signed_in, person, pr_refs=["#212", "#219", "#212"])

        assert task["pr_refs"] == ["#212", "#219"]

    async def test_a_blank_entry_is_dropped_rather_than_refused(
        self, signed_in: AsyncClient
    ) -> None:
        """An empty row in the dialog's list is a box nobody has filled in
        yet, not a reason to refuse the whole save."""
        person = await _setup(signed_in)

        task = await _create(signed_in, person, pr_refs=["#212", "   ", ""])

        assert task["pr_refs"] == ["#212"]

    async def test_a_pasted_pull_request_url_survives_whole(self, signed_in: AsyncClient) -> None:
        """The board shows `#219`, but it can only link somewhere it still has
        the whole address of."""
        person = await _setup(signed_in)
        link = "https://github.com/acme-engineering/atlas-billing/pull/219"

        task = await _create(signed_in, person, pr_refs=[link])

        assert task["pr_refs"] == [link]

    async def test_refuses_more_than_twenty(self, signed_in: AsyncClient) -> None:
        """A card that took twenty pull requests was more than one card."""
        person = await _setup(signed_in)

        response = await signed_in.post(
            "/projects/ATL/tasks",
            json=_task(person, pr_refs=[f"#{number}" for number in range(21)]),
        )

        assert response.status_code == 422, response.text

    async def test_the_card_carries_them_on_the_board_listing(self, signed_in: AsyncClient) -> None:
        """Read back off the list query as well as the card, because the board
        draws a mark per pull request without opening anything."""
        person = await _setup(signed_in)
        await _create(signed_in, person, pr_refs=["#212", "#219"])

        listed = (await signed_in.get("/projects/ATL/tasks")).json()

        assert [task["pr_refs"] for task in listed] == [["#212", "#219"]]

    async def test_a_sub_task_can_name_its_own(self, signed_in: AsyncClient) -> None:
        """A sub-task is work with a reference of its own, so it has its own
        pull requests too."""
        person = await _setup(signed_in)
        parent = await _create(signed_in, person)

        response = await signed_in.post(
            f"/tasks/{parent['id']}/subtasks",
            json=_task(person, pr_refs=["#44"]),
        )

        assert response.status_code == 201, response.text
        assert response.json()["pr_refs"] == ["#44"]

    async def test_adding_one_is_written_on_the_cards_history(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        task = await _create(signed_in, person, pr_refs=["#212"])

        await signed_in.patch(f"/tasks/{task['id']}", json={"pr_refs": ["#212", "#219"]})
        history = (await signed_in.get(f"/tasks/{task['id']}/history")).json()["entries"]

        changed = [
            change
            for entry in history
            for change in entry["changes"]
            if change["field"] == "pr_refs"
        ]
        assert changed, "the change is reported"
        assert changed[0]["label"] == "pull requests"
        assert changed[0]["from"] == ["#212"]
        assert changed[0]["to"] == ["#212", "#219"]
