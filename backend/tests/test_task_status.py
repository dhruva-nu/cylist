"""Status changes and the timeline they are written to.

A card that is red without saying why is a question rather than information,
so the reason is a rule and not a convention. These tests pin that rule down
along with what it does to the "waiting on" tags and the comment history.
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
SANJAY = {
    "name": "Sanjay F",
    "kind": "client",
    "role": "Finance controller, Atlas",
    "responsibilities": "Approves anything touching tax or vendor accounts.",
}
LENA = {
    "name": "Lena W",
    "kind": "client",
    "role": "Legal counsel, Atlas",
    "responsibilities": "Signs off licences and contracts.",
}
UNKNOWN_ID = "00000000-0000-7000-8000-000000000000"

CREDENTIALS = "Waiting for Avalara sandbox credentials from finance."


async def _person(client: AsyncClient, details: dict[str, str]) -> str:
    return str((await client.post("/people", json=details)).json()["id"])


async def _board(client: AsyncClient, *directory: dict[str, str]) -> tuple[str, list[str]]:
    """A project with a task on it. Returns the task id and the member ids."""
    await client.post("/projects", json=ATLAS)
    members = [await _person(client, details) for details in (ADITI, *directory)]
    await client.put("/projects/ATL/members", json={"person_ids": members})

    task = (
        await client.post(
            "/projects/ATL/tasks",
            json={
                "title": "Tax rate lookup by region",
                "description": "Integrate the Avalara sandbox and cache rates for 24h.",
                "type": "feature",
                "due_date": "2026-09-03",
                "assignee_id": members[0],
            },
        )
    ).json()
    return str(task["id"]), members


async def _set_status(client: AsyncClient, task_id: str, **body: Any) -> Any:
    return await client.post(f"/tasks/{task_id}/status", json=body)


def _status_changes(task: dict[str, Any]) -> list[dict[str, Any]]:
    return [entry for entry in task["comments"] if entry["kind"] == "status_change"]


class TestReasonIsRequired:
    async def test_going_on_hold_without_a_reason_is_refused(self, signed_in: AsyncClient) -> None:
        task_id, _ = await _board(signed_in)

        response = await _set_status(signed_in, task_id, status="hold")

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "unprocessable"
        assert response.json()["error"]["details"] == {"field": "reason"}

    async def test_becoming_blocked_without_a_reason_is_refused(
        self, signed_in: AsyncClient
    ) -> None:
        task_id, _ = await _board(signed_in)

        assert (await _set_status(signed_in, task_id, status="blocked")).status_code == 422

    async def test_whitespace_is_not_a_reason(self, signed_in: AsyncClient) -> None:
        task_id, _ = await _board(signed_in)

        response = await _set_status(signed_in, task_id, status="hold", reason="   \n ")

        assert response.status_code == 422

    async def test_a_refused_change_leaves_the_task_active(self, signed_in: AsyncClient) -> None:
        task_id, _ = await _board(signed_in)

        await _set_status(signed_in, task_id, status="blocked")

        task = (await signed_in.get(f"/tasks/{task_id}")).json()
        assert task["status"] == "active"
        assert task["comments"] == []

    async def test_going_back_to_active_needs_no_reason(self, signed_in: AsyncClient) -> None:
        """Nothing is in the way any more, so there is nothing to explain."""
        task_id, _ = await _board(signed_in)
        await _set_status(signed_in, task_id, status="hold", reason=CREDENTIALS)

        response = await _set_status(signed_in, task_id, status="active")

        assert response.status_code == 200
        assert response.json()["status"] == "active"


class TestTheStatusChangeComment:
    async def test_the_change_is_written_to_the_timeline(self, signed_in: AsyncClient) -> None:
        """One card, one story: status changes are comments, not a second log."""
        task_id, _ = await _board(signed_in)

        task = (await _set_status(signed_in, task_id, status="hold", reason=CREDENTIALS)).json()

        assert [entry["kind"] for entry in task["comments"]] == ["status_change"]
        assert task["comments"][0]["body"] == f"On hold — {CREDENTIALS}"

    async def test_it_carries_where_the_task_came_from_and_went(
        self, signed_in: AsyncClient
    ) -> None:
        task_id, members = await _board(signed_in, SANJAY)
        await _set_status(signed_in, task_id, status="hold", reason=CREDENTIALS)

        task = (
            await _set_status(
                signed_in,
                task_id,
                status="blocked",
                reason="Legal have not approved the font licence.",
                waiting_on=[members[1]],
            )
        ).json()

        assert _status_changes(task)[-1]["meta"] == {
            "from": "hold",
            "to": "blocked",
            "reason": "Legal have not approved the font licence.",
            "tagged": [members[1]],
        }

    async def test_it_has_no_author(self, signed_in: AsyncClient) -> None:
        """Cylist wrote it, not a person, and the timeline should say so."""
        task_id, _ = await _board(signed_in)

        task = (await _set_status(signed_in, task_id, status="hold", reason=CREDENTIALS)).json()

        assert task["comments"][0]["author"] is None

    async def test_returning_to_active_is_recorded_too(self, signed_in: AsyncClient) -> None:
        task_id, _ = await _board(signed_in)
        await _set_status(signed_in, task_id, status="hold", reason=CREDENTIALS)

        task = (await _set_status(signed_in, task_id, status="active")).json()

        assert [entry["body"] for entry in task["comments"]] == [
            f"On hold — {CREDENTIALS}",
            "Active",
        ]

    async def test_an_optional_note_on_unblocking_is_kept(self, signed_in: AsyncClient) -> None:
        task_id, _ = await _board(signed_in)
        await _set_status(signed_in, task_id, status="blocked", reason="Licence not approved.")

        task = (
            await _set_status(signed_in, task_id, status="active", reason="Licence came through.")
        ).json()

        assert task["comments"][-1]["body"] == "Active — Licence came through."

    async def test_the_history_accumulates(self, signed_in: AsyncClient) -> None:
        task_id, _ = await _board(signed_in)

        await _set_status(signed_in, task_id, status="hold", reason="Paused for the cutover.")
        await _set_status(signed_in, task_id, status="active")
        task = (
            await _set_status(signed_in, task_id, status="blocked", reason="Sandbox is down.")
        ).json()

        assert [entry["meta"]["to"] for entry in _status_changes(task)] == [
            "hold",
            "active",
            "blocked",
        ]

    async def test_it_is_recorded_in_the_audit_trail(self, signed_in: AsyncClient) -> None:
        task_id, _ = await _board(signed_in)

        await _set_status(signed_in, task_id, status="hold", reason=CREDENTIALS)

        entries = (await signed_in.get("/activity", params={"entity_type": "task"})).json()
        assert entries[0]["verb"] == "task.status_changed"
        assert entries[0]["payload"]["to"] == "hold"
        assert entries[0]["payload"]["reason"] == CREDENTIALS


class TestWaitingOn:
    async def test_tags_the_people_a_stalled_task_needs(self, signed_in: AsyncClient) -> None:
        task_id, members = await _board(signed_in, SANJAY, LENA)

        task = (
            await _set_status(
                signed_in,
                task_id,
                status="blocked",
                reason="Font licence not approved.",
                waiting_on=members[1:],
            )
        ).json()

        assert [person["name"] for person in task["waiting_on"]] == ["Lena W", "Sanjay F"]

    async def test_a_second_change_replaces_the_tags_rather_than_adding_to_them(
        self, signed_in: AsyncClient
    ) -> None:
        """It answers "who is holding this up today", not "who ever did"."""
        task_id, members = await _board(signed_in, SANJAY, LENA)
        await _set_status(
            signed_in, task_id, status="hold", reason=CREDENTIALS, waiting_on=[members[1]]
        )

        task = (
            await _set_status(
                signed_in,
                task_id,
                status="blocked",
                reason="Now it is legal.",
                waiting_on=[members[2]],
            )
        ).json()

        assert [person["name"] for person in task["waiting_on"]] == ["Lena W"]

    async def test_going_active_clears_them(self, signed_in: AsyncClient) -> None:
        task_id, members = await _board(signed_in, SANJAY)
        await _set_status(
            signed_in, task_id, status="hold", reason=CREDENTIALS, waiting_on=[members[1]]
        )

        task = (await _set_status(signed_in, task_id, status="active")).json()

        assert task["waiting_on"] == []

    async def test_tags_sent_alongside_active_are_ignored(self, signed_in: AsyncClient) -> None:
        task_id, members = await _board(signed_in, SANJAY)

        task = (
            await _set_status(signed_in, task_id, status="active", waiting_on=[members[1]])
        ).json()

        assert task["waiting_on"] == []
        assert task["comments"][0]["meta"]["tagged"] == []

    async def test_stalling_with_nobody_tagged_is_allowed(self, signed_in: AsyncClient) -> None:
        """Not every hold is somebody's fault; the reason is the required part."""
        task_id, _ = await _board(signed_in)

        task = (
            await _set_status(signed_in, task_id, status="hold", reason="Deprioritised.")
        ).json()

        assert task["status"] == "hold"
        assert task["waiting_on"] == []

    async def test_refuses_somebody_who_is_not_on_the_project(self, signed_in: AsyncClient) -> None:
        task_id, _ = await _board(signed_in)
        outsider = await _person(signed_in, LENA)

        response = await _set_status(
            signed_in, task_id, status="blocked", reason="Legal.", waiting_on=[outsider]
        )

        assert response.status_code == 422
        assert response.json()["error"]["details"]["non_member_person_ids"] == [outsider]

    async def test_refuses_somebody_who_does_not_exist(self, signed_in: AsyncClient) -> None:
        task_id, _ = await _board(signed_in)

        response = await _set_status(
            signed_in, task_id, status="hold", reason="Waiting.", waiting_on=[UNKNOWN_ID]
        )

        assert response.status_code == 422

    async def test_a_rejected_tag_leaves_the_task_untouched(self, signed_in: AsyncClient) -> None:
        task_id, members = await _board(signed_in, SANJAY)
        await _set_status(
            signed_in, task_id, status="hold", reason=CREDENTIALS, waiting_on=[members[1]]
        )
        outsider = await _person(signed_in, LENA)

        await _set_status(
            signed_in, task_id, status="blocked", reason="Legal.", waiting_on=[outsider]
        )

        task = (await signed_in.get(f"/tasks/{task_id}")).json()
        assert task["status"] == "hold"
        assert [person["name"] for person in task["waiting_on"]] == ["Sanjay F"]

    async def test_the_same_person_twice_is_one_tag(self, signed_in: AsyncClient) -> None:
        """A double-submitted form must not violate the composite primary key."""
        task_id, members = await _board(signed_in, SANJAY)

        task = (
            await _set_status(
                signed_in,
                task_id,
                status="hold",
                reason=CREDENTIALS,
                waiting_on=[members[1], members[1]],
            )
        ).json()

        assert len(task["waiting_on"]) == 1

    async def test_tags_show_up_on_the_board_listing(self, signed_in: AsyncClient) -> None:
        """The card needs the name, not just the id, to say "Waiting on @Sanjay"."""
        task_id, members = await _board(signed_in, SANJAY)
        await _set_status(
            signed_in, task_id, status="hold", reason=CREDENTIALS, waiting_on=[members[1]]
        )

        board = (await signed_in.get("/projects/ATL/tasks")).json()

        assert board[0]["waiting_on"][0]["name"] == "Sanjay F"


class TestSummaryCounts:
    async def test_the_hub_counts_stalled_work(self, signed_in: AsyncClient) -> None:
        task_id, _ = await _board(signed_in)
        await _set_status(signed_in, task_id, status="blocked", reason="Sandbox is down.")

        summary = (await signed_in.get("/projects/ATL/summary")).json()

        assert summary["task_count"] == 1
        assert summary["blocked_count"] == 1
        assert summary["on_hold_count"] == 0

    async def test_the_counts_follow_the_status_back(self, signed_in: AsyncClient) -> None:
        task_id, _ = await _board(signed_in)
        await _set_status(signed_in, task_id, status="blocked", reason="Sandbox is down.")
        await _set_status(signed_in, task_id, status="active")

        summary = (await signed_in.get("/projects/ATL/summary")).json()

        assert summary["blocked_count"] == 0


class TestComments:
    async def test_adds_one_to_the_timeline(self, signed_in: AsyncClient) -> None:
        task_id, members = await _board(signed_in)

        response = await signed_in.post(
            f"/tasks/{task_id}/comments",
            json={"body": "Can we keep amounts as integer minor units?", "author_id": members[0]},
        )

        assert response.status_code == 201
        assert response.json()["kind"] == "comment"
        assert response.json()["author"]["name"] == "Aditi K"

    async def test_an_agent_may_leave_one_unattributed(self, signed_in: AsyncClient) -> None:
        task_id, _ = await _board(signed_in)

        response = await signed_in.post(
            f"/tasks/{task_id}/comments", json={"body": "Rebased onto main."}
        )

        assert response.status_code == 201
        assert response.json()["author"] is None

    async def test_refuses_an_author_who_is_not_on_the_project(
        self, signed_in: AsyncClient
    ) -> None:
        task_id, _ = await _board(signed_in)
        outsider = await _person(signed_in, LENA)

        response = await signed_in.post(
            f"/tasks/{task_id}/comments", json={"body": "Hello.", "author_id": outsider}
        )

        assert response.status_code == 422

    async def test_a_blank_comment_is_refused(self, signed_in: AsyncClient) -> None:
        task_id, _ = await _board(signed_in)

        assert (
            await signed_in.post(f"/tasks/{task_id}/comments", json={"body": "   "})
        ).status_code == 422

    async def test_comments_and_status_changes_share_one_timeline(
        self, signed_in: AsyncClient
    ) -> None:
        task_id, members = await _board(signed_in)
        await signed_in.post(
            f"/tasks/{task_id}/comments",
            json={"body": "Starting on this.", "author_id": members[0]},
        )
        await _set_status(signed_in, task_id, status="hold", reason=CREDENTIALS)
        await signed_in.post(f"/tasks/{task_id}/comments", json={"body": "Chased finance."})

        timeline = (await signed_in.get(f"/tasks/{task_id}/comments")).json()

        assert [entry["kind"] for entry in timeline] == [
            "comment",
            "status_change",
            "comment",
        ]

    async def test_the_card_carries_how_long_its_timeline_is(self, signed_in: AsyncClient) -> None:
        task_id, _ = await _board(signed_in)
        await signed_in.post(f"/tasks/{task_id}/comments", json={"body": "One."})
        await _set_status(signed_in, task_id, status="hold", reason=CREDENTIALS)

        board = (await signed_in.get("/projects/ATL/tasks")).json()

        assert board[0]["comment_count"] == 2

    async def test_deleting_a_task_takes_its_timeline_with_it(self, signed_in: AsyncClient) -> None:
        task_id, _ = await _board(signed_in)
        await signed_in.post(f"/tasks/{task_id}/comments", json={"body": "One."})

        assert (await signed_in.delete(f"/tasks/{task_id}")).status_code == 200
        assert (await signed_in.get(f"/tasks/{task_id}/comments")).status_code == 404

    async def test_it_is_recorded_in_the_audit_trail(self, signed_in: AsyncClient) -> None:
        task_id, _ = await _board(signed_in)

        await signed_in.post(f"/tasks/{task_id}/comments", json={"body": "Chased finance."})

        entries = (await signed_in.get("/activity", params={"entity_type": "task"})).json()
        assert entries[0]["verb"] == "task.commented"
