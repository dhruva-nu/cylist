"""Goals: the epic a card belongs to.

What is checked here is the shape of a goal rather than the storing of one:
that a card carries its goal's colour so the board can draw it, that a goal is
numbered and named unambiguously on its project, that its progress is counted
from its cards rather than kept beside them, and that the one rule a goal
enforces — it cannot be achieved over open cards — refuses in language that
names what is holding it open.
"""

from __future__ import annotations

from httpx import AsyncClient

ATLAS = {"key": "ATL", "name": "Atlas Billing Migration"}
TEAM = {
    "name": "Aditi K",
    "kind": "team",
    "role": "Backend engineer",
    "responsibilities": "Payments and webhooks.",
}
OUTSIDER = {
    "name": "Sam Client",
    "kind": "client",
    "role": "Sponsor",
    "responsibilities": "Signs things off.",
}
UNKNOWN_ID = "00000000-0000-7000-8000-000000000000"


async def _project(client: AsyncClient) -> str:
    """A project with a member, ready to hold goals and cards."""
    await client.post("/projects", json=ATLAS)
    person = (await client.post("/people", json=TEAM)).json()["id"]
    await client.put("/projects/ATL/members", json={"person_ids": [person]})
    return str(person)


async def _goal(client: AsyncClient, person: str, **extra: object) -> dict[str, object]:
    response = await client.post(
        "/projects/ATL/goals",
        json={"name": "Search revamp", "owner_id": person, **extra},
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _task(client: AsyncClient, person: str, **extra: object) -> dict[str, object]:
    response = await client.post(
        "/projects/ATL/tasks",
        json={
            "title": "Stripe webhook idempotency",
            "description": "Duplicate deliveries create double payments.",
            "type": "bug",
            "assignee_id": person,
            **extra,
        },
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _last_column(client: AsyncClient) -> str:
    body = (await client.get("/projects/ATL/columns")).json()
    return str(body["columns"][-1]["id"])


class TestStartingAGoal:
    """Naming a goal, numbering it, and giving it a colour."""

    async def test_goal_is_numbered_and_coloured(self, signed_in: AsyncClient) -> None:
        """``ATL-G1``, and a colour off the palette when none was asked for."""
        person = await _project(signed_in)
        goal = await _goal(signed_in, person)

        assert goal["reference"] == "ATL-G1"
        assert goal["number"] == 1
        assert goal["status"] == "open"
        assert goal["colour"].startswith("#")
        assert len(goal["colour"]) == 7
        assert goal["tasks"] == []

    async def test_goal_numbering_does_not_share_the_task_counter(
        self, signed_in: AsyncClient
    ) -> None:
        """A card is ATL-1 and a goal is ATL-G1, and neither takes the other's number."""
        person = await _project(signed_in)
        first = await _goal(signed_in, person)
        task = await _task(signed_in, person)
        second = await _goal(signed_in, person, name="Billing hardening")

        assert first["reference"] == "ATL-G1"
        assert task["reference"] == "ATL-1"
        assert second["reference"] == "ATL-G2"

    async def test_a_chosen_colour_is_kept(self, signed_in: AsyncClient) -> None:
        person = await _project(signed_in)
        goal = await _goal(signed_in, person, colour="#B5533F")
        assert goal["colour"] == "#B5533F"

    async def test_two_goals_cannot_share_a_name(self, signed_in: AsyncClient) -> None:
        """However it was capitalised: a card wears the name, and two alike are
        two answers to what the card is for."""
        person = await _project(signed_in)
        await _goal(signed_in, person)

        response = await signed_in.post(
            "/projects/ATL/goals", json={"name": "SEARCH REVAMP", "owner_id": person}
        )
        assert response.status_code == 409, response.text
        assert "already has a goal" in response.json()["error"]["message"]

    async def test_owner_must_be_a_project_member(self, signed_in: AsyncClient) -> None:
        await _project(signed_in)
        outsider = (await signed_in.post("/people", json=OUTSIDER)).json()["id"]

        response = await signed_in.post(
            "/projects/ATL/goals", json={"name": "Search revamp", "owner_id": outsider}
        )
        assert response.status_code == 422, response.text


class TestLinkingCards:
    """Putting a card on a goal, taking it off, and who may carry one."""

    async def test_a_card_carries_its_goals_colour(self, signed_in: AsyncClient) -> None:
        """The whole point of the colour: a card can draw its own rail without
        the goal list fetched beside it."""
        person = await _project(signed_in)
        goal = await _goal(signed_in, person, colour="#3B6FC2")
        task = await _task(signed_in, person, goal_id=goal["id"])

        assert task["goal_id"] == goal["id"]
        assert task["goal_reference"] == "ATL-G1"
        assert task["goal_name"] == "Search revamp"
        assert task["goal_colour"] == "#3B6FC2"

        board = (await signed_in.get("/projects/ATL/tasks")).json()
        assert board[0]["goal_colour"] == "#3B6FC2"

    async def test_a_card_with_no_goal_says_so_in_nulls(self, signed_in: AsyncClient) -> None:
        person = await _project(signed_in)
        task = await _task(signed_in, person)

        assert task["goal_id"] is None
        assert task["goal_colour"] is None

    async def test_a_card_can_be_linked_and_unlinked(self, signed_in: AsyncClient) -> None:
        """Null clears rather than meaning "leave alone" — the goal is a label,
        and a label comes off."""
        person = await _project(signed_in)
        goal = await _goal(signed_in, person)
        task = await _task(signed_in, person)

        linked = await signed_in.patch(f"/tasks/{task['reference']}", json={"goal_id": goal["id"]})
        assert linked.status_code == 200, linked.text
        assert linked.json()["goal_reference"] == "ATL-G1"

        unlinked = await signed_in.patch(f"/tasks/{task['reference']}", json={"goal_id": None})
        assert unlinked.status_code == 200, unlinked.text
        assert unlinked.json()["goal_id"] is None

    async def test_linking_a_card_is_written_to_its_history(self, signed_in: AsyncClient) -> None:
        """By name, not by id: a history that says which UUID was set tells the
        reader nothing about what the work is for."""
        person = await _project(signed_in)
        goal = await _goal(signed_in, person)
        task = await _task(signed_in, person)
        await signed_in.patch(f"/tasks/{task['reference']}", json={"goal_id": goal["id"]})

        history = (await signed_in.get(f"/tasks/{task['reference']}/history")).json()
        changes = [change for entry in history["entries"] for change in entry.get("changes", [])]
        assert {"goal"} == {change["field"] for change in changes}
        assert changes[0]["to"] == "Search revamp"

    async def test_a_goal_from_another_project_is_refused(self, signed_in: AsyncClient) -> None:
        person = await _project(signed_in)
        goal = await _goal(signed_in, person)

        await signed_in.post("/projects", json={"key": "HRM", "name": "Hermes"})
        await signed_in.put("/projects/HRM/members", json={"person_ids": [person]})
        response = await signed_in.post(
            "/projects/HRM/tasks",
            json={
                "title": "Onboarding emails",
                "description": "Welcome mail bounces.",
                "type": "chore",
                "assignee_id": person,
                "goal_id": goal["id"],
            },
        )
        assert response.status_code == 422, response.text
        assert "not on this project" in response.json()["error"]["message"]

    async def test_a_subtask_cannot_be_put_on_a_goal(self, signed_in: AsyncClient) -> None:
        """A sub-task belongs to its card, and its card belongs to the goal."""
        person = await _project(signed_in)
        goal = await _goal(signed_in, person)
        task = await _task(signed_in, person)

        response = await signed_in.post(
            f"/tasks/{task['reference']}/subtasks",
            json={
                "title": "Write the migration",
                "description": "Add the index.",
                "type": "chore",
                "assignee_id": person,
                "goal_id": goal["id"],
            },
        )
        assert response.status_code == 422, response.text
        assert "sub-task" in response.json()["error"]["message"]

    async def test_deleting_a_goal_leaves_its_cards(self, signed_in: AsyncClient) -> None:
        """A goal is a label. Taking it off is not a reason to lose the card."""
        person = await _project(signed_in)
        goal = await _goal(signed_in, person)
        task = await _task(signed_in, person, goal_id=goal["id"])

        deleted = await signed_in.delete(f"/goals/{goal['reference']}")
        assert deleted.status_code == 200, deleted.text

        still_there = await signed_in.get(f"/tasks/{task['reference']}")
        assert still_there.status_code == 200
        assert still_there.json()["goal_id"] is None


class TestProgress:
    """What is left on a goal, counted from its cards rather than stored."""

    async def test_progress_counts_the_cards(self, signed_in: AsyncClient) -> None:
        """Done is where a card is, cancelled is what it is, and open is the rest."""
        person = await _project(signed_in)
        goal = await _goal(signed_in, person)
        done = await _task(signed_in, person, goal_id=goal["id"])
        dropped = await _task(signed_in, person, goal_id=goal["id"])
        await _task(signed_in, person, goal_id=goal["id"])

        await signed_in.post(
            f"/tasks/{done['reference']}/move",
            json={"column_id": await _last_column(signed_in), "position": 0},
        )
        await signed_in.post(
            f"/tasks/{dropped['reference']}/status",
            json={"status": "cancelled", "reason": "Handled upstream."},
        )

        progress = (await signed_in.get(f"/goals/{goal['reference']}")).json()["progress"]
        assert progress == {
            "total": 3,
            "done": 1,
            "cancelled": 1,
            "open": 1,
            "blocked": 0,
            "on_hold": 0,
        }

    async def test_progress_flags_stalled_cards(self, signed_in: AsyncClient) -> None:
        person = await _project(signed_in)
        goal = await _goal(signed_in, person)
        stuck = await _task(signed_in, person, goal_id=goal["id"])
        await signed_in.post(
            f"/tasks/{stuck['reference']}/status",
            json={
                "status": "blocked",
                "reason": "Waiting on the sandbox key.",
                "waiting_on": [person],
            },
        )

        progress = (await signed_in.get(f"/goals/{goal['reference']}")).json()["progress"]
        assert progress["open"] == 1
        assert progress["blocked"] == 1

    async def test_a_goal_with_no_cards_is_all_zeros(self, signed_in: AsyncClient) -> None:
        """Not a missing count: a caller should never have to ask whether the
        absence means none or not counted."""
        person = await _project(signed_in)
        goal = await _goal(signed_in, person)
        assert goal["progress"]["total"] == 0
        assert goal["progress"]["open"] == 0

    async def test_a_goals_page_lists_its_cards_in_board_order(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _project(signed_in)
        goal = await _goal(signed_in, person)
        first = await _task(signed_in, person, goal_id=goal["id"])
        second = await _task(signed_in, person, goal_id=goal["id"])
        await _task(signed_in, person)  # on no goal, so not on the page

        await signed_in.post(
            f"/tasks/{second['reference']}/move",
            json={"column_id": await _last_column(signed_in), "position": 0},
        )

        body = (await signed_in.get(f"/goals/{goal['reference']}")).json()
        assert [task["reference"] for task in body["tasks"]] == [
            first["reference"],
            second["reference"],
        ]


class TestAchievingAGoal:
    """The one rule a goal enforces, and the two ways it can settle."""

    async def test_a_goal_cannot_be_achieved_over_open_cards(self, signed_in: AsyncClient) -> None:
        """And the refusal names them, so the next move is obvious."""
        person = await _project(signed_in)
        goal = await _goal(signed_in, person)
        open_card = await _task(signed_in, person, goal_id=goal["id"])

        response = await signed_in.patch(f"/goals/{goal['reference']}", json={"status": "achieved"})
        assert response.status_code == 422, response.text
        error = response.json()["error"]
        assert open_card["reference"] in error["message"]
        assert error["details"]["open_tasks"] == [open_card["reference"]]

    async def test_a_goal_is_achieved_once_its_cards_are_settled(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _project(signed_in)
        goal = await _goal(signed_in, person)
        finished = await _task(signed_in, person, goal_id=goal["id"])
        cancelled = await _task(signed_in, person, goal_id=goal["id"])

        await signed_in.post(
            f"/tasks/{finished['reference']}/move",
            json={"column_id": await _last_column(signed_in), "position": 0},
        )
        await signed_in.post(
            f"/tasks/{cancelled['reference']}/status",
            json={"status": "cancelled", "reason": "Not needed."},
        )

        response = await signed_in.patch(f"/goals/{goal['reference']}", json={"status": "achieved"})
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "achieved"
        assert response.json()["achieved_at"] is not None

    async def test_a_goal_can_be_dropped_with_work_outstanding(
        self, signed_in: AsyncClient
    ) -> None:
        """Giving up on a goal is exactly what you do while its work is unfinished."""
        person = await _project(signed_in)
        goal = await _goal(signed_in, person)
        await _task(signed_in, person, goal_id=goal["id"])

        response = await signed_in.patch(f"/goals/{goal['reference']}", json={"status": "dropped"})
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "dropped"
        assert response.json()["achieved_at"] is None

    async def test_reopening_a_goal_clears_the_moment_it_was_achieved(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _project(signed_in)
        goal = await _goal(signed_in, person)
        await signed_in.patch(f"/goals/{goal['reference']}", json={"status": "achieved"})

        reopened = await signed_in.patch(f"/goals/{goal['reference']}", json={"status": "open"})
        assert reopened.json()["status"] == "open"
        assert reopened.json()["achieved_at"] is None


class TestListingGoals:
    """The order a goals page reads in, and what a picker is offered."""

    async def test_open_goals_come_first_then_the_nearest_dated(
        self, signed_in: AsyncClient
    ) -> None:
        """A list you can plan from rather than one in alphabetical order."""
        person = await _project(signed_in)
        await _goal(signed_in, person, name="Undated work")
        await _goal(signed_in, person, name="Later", target_date="2026-12-01")
        await _goal(signed_in, person, name="Sooner", target_date="2026-10-01")
        settled = await _goal(signed_in, person, name="Already done")
        await signed_in.patch(f"/goals/{settled['reference']}", json={"status": "achieved"})

        names = [goal["name"] for goal in (await signed_in.get("/projects/ATL/goals")).json()]
        assert names == ["Sooner", "Later", "Undated work", "Already done"]

    async def test_open_only_leaves_out_settled_goals(self, signed_in: AsyncClient) -> None:
        """What a picker wants: it should not offer work that has stopped."""
        person = await _project(signed_in)
        await _goal(signed_in, person)
        dropped = await _goal(signed_in, person, name="Abandoned")
        await signed_in.patch(f"/goals/{dropped['reference']}", json={"status": "dropped"})

        body = (await signed_in.get("/projects/ATL/goals", params={"open_only": True})).json()
        assert [goal["name"] for goal in body] == ["Search revamp"]

    async def test_the_hub_counts_goals(self, signed_in: AsyncClient) -> None:
        person = await _project(signed_in)
        await _goal(signed_in, person)
        dropped = await _goal(signed_in, person, name="Abandoned")
        await signed_in.patch(f"/goals/{dropped['reference']}", json={"status": "dropped"})

        summary = (await signed_in.get("/projects/ATL/summary")).json()
        assert summary["goal_count"] == 2
        assert summary["open_goal_count"] == 1


class TestAddressingAGoal:
    """A goal is reached by reference, which a rename does not move."""

    async def test_a_goal_is_addressable_by_reference_or_id(self, signed_in: AsyncClient) -> None:
        person = await _project(signed_in)
        goal = await _goal(signed_in, person)

        by_reference = await signed_in.get("/goals/atl-g1")
        by_id = await signed_in.get(f"/goals/{goal['id']}")
        assert by_reference.status_code == 200, by_reference.text
        assert by_reference.json()["id"] == goal["id"] == by_id.json()["id"]

    async def test_a_reference_that_is_not_one_says_what_one_looks_like(
        self, signed_in: AsyncClient
    ) -> None:
        await _project(signed_in)
        response = await signed_in.get("/goals/ATL-41")
        assert response.status_code == 404, response.text
        assert "ATL-G1" in response.json()["error"]["message"]

    async def test_an_unknown_goal_is_a_404(self, signed_in: AsyncClient) -> None:
        await _project(signed_in)
        assert (await signed_in.get(f"/goals/{UNKNOWN_ID}")).status_code == 404

    async def test_renaming_a_goal_keeps_its_reference(self, signed_in: AsyncClient) -> None:
        """Which is why the URL is the reference and not the name."""
        person = await _project(signed_in)
        goal = await _goal(signed_in, person)

        renamed = await signed_in.patch(
            f"/goals/{goal['reference']}", json={"name": "Search and filters"}
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["reference"] == "ATL-G1"
        assert (await signed_in.get("/goals/ATL-G1")).json()["name"] == "Search and filters"

    async def test_target_date_can_be_taken_off(self, signed_in: AsyncClient) -> None:
        person = await _project(signed_in)
        goal = await _goal(signed_in, person, target_date="2026-10-01")

        cleared = await signed_in.patch(f"/goals/{goal['reference']}", json={"target_date": None})
        assert cleared.json()["target_date"] is None
