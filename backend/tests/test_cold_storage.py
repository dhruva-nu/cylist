"""Cold storage: the board's finished cards, taken off it into a CSV.

The last column only ever fills up. Cold storage empties it in one go — the
cards leave the board, a CSV of them is filed in the project's files — without
losing them: each still opens by its reference, still counts towards its goal,
and can be put back where it was.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from httpx import AsyncClient

from app.auth.permissions import Permission
from tests.conftest import INVITEE_PASSWORD, OWNER_NAME, cards_of, open_account, put_members

ATLAS = {"key": "ATL", "name": "Atlas Billing Migration"}
ADITI = {
    "name": "Aditi K",
    "kind": "team",
    "title": "Backend engineer",
    "responsibilities": "Payments and webhooks.",
}
ADITI_EMAIL = "aditi@example.com"


async def _setup(client: AsyncClient) -> str:
    await client.post("/projects", json=ATLAS)
    person = (await client.post("/people", json=ADITI)).json()["id"]
    await put_members(client, "ATL", [person])
    return str(person)


async def _create(client: AsyncClient, assignee: str, **overrides: Any) -> dict[str, Any]:
    response = await client.post(
        "/projects/ATL/tasks",
        json={
            "title": "Stripe webhook idempotency",
            "description": "Duplicate deliveries create double payments.",
            "type": "bug",
            "assignee_id": assignee,
            **overrides,
        },
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _columns(client: AsyncClient) -> list[dict[str, Any]]:
    return list((await client.get("/projects/ATL/columns")).json()["columns"])


async def _finish(client: AsyncClient, task: dict[str, Any], **body: Any) -> None:
    last = (await _columns(client))[-1]
    moved = await client.post(
        f"/tasks/{task['reference']}/move",
        json={"column_id": last["id"], "position": 99, **body},
    )
    assert moved.status_code == 200, moved.text


async def _store(client: AsyncClient) -> dict[str, Any]:
    response = await client.post("/projects/ATL/cold-storage")
    assert response.status_code == 200, response.text
    return dict(response.json())


async def _rows(client: AsyncClient, item_id: str) -> list[dict[str, str]]:
    download = await client.get(f"/items/{item_id}/download")
    assert download.status_code == 200, download.text
    text = download.content.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


async def _board_refs(client: AsyncClient) -> list[str]:
    return [
        task["reference"] for task in cards_of((await client.get("/projects/ATL/tasks")).json())
    ]


class TestStoring:
    async def test_the_last_column_leaves_the_board(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        shipped = await _create(signed_in, person, title="Shipped")
        also = await _create(signed_in, person, title="Also shipped")
        still_open = await _create(signed_in, person, title="Still open")
        await _finish(signed_in, shipped)
        await _finish(signed_in, also)

        run = await _store(signed_in)

        assert run["column"] == "Done"
        assert run["references"] == [shipped["reference"], also["reference"]]
        assert await _board_refs(signed_in) == [still_open["reference"]]
        last = (await _columns(signed_in))[-1]
        assert last["task_count"] == 0

    async def test_a_stored_card_still_opens(self, signed_in: AsyncClient) -> None:
        """Its reference outlives its place on the board, as it does a deletion."""
        person = await _setup(signed_in)
        card = await _create(signed_in, person)
        await _finish(signed_in, card)

        await _store(signed_in)

        found = (await signed_in.get(f"/tasks/{card['reference']}")).json()
        assert found["archived_at"] is not None
        assert found["column_id"] is None
        assert found["position"] is None
        assert found["finished_at"] is not None

    async def test_the_csv_lists_every_card(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        card = await _create(signed_in, person, title="Ledger export", priority="p1")
        await _finish(signed_in, card)

        run = await _store(signed_in)

        assert run["file"]["name"].startswith("ATL-cold-storage-")
        assert run["file"]["name"].endswith(".csv")
        assert run["file"]["mime"] == "text/csv"
        (row,) = await _rows(signed_in, run["file"]["id"])
        assert row["reference"] == card["reference"]
        assert row["title"] == "Ledger export"
        assert row["priority"] == "P1"
        assert row["column"] == "Done"
        assert row["assignee"] == "Aditi K"
        assert row["description"] == "Duplicate deliveries create double payments."
        assert row["finished_at"]
        assert row["stored_at"]

    async def test_the_csv_is_filed_in_a_cold_storage_folder(self, signed_in: AsyncClient) -> None:
        """One folder at the top of the files, reused run after run."""
        person = await _setup(signed_in)
        await _finish(signed_in, await _create(signed_in, person))
        first = await _store(signed_in)
        await _finish(signed_in, await _create(signed_in, person))
        second = await _store(signed_in)

        assert first["file"]["folder_id"] == second["file"]["folder_id"]
        assert first["file"]["name"] != second["file"]["name"]
        folders = (await signed_in.get("/projects/ATL/folders")).json()["items"]
        assert [folder["name"] for folder in folders].count("Cold storage") == 1

    async def test_a_formula_in_a_title_is_written_as_text(self, signed_in: AsyncClient) -> None:
        """Whoever opens the export should not run what somebody typed into a card."""
        person = await _setup(signed_in)
        await _finish(signed_in, await _create(signed_in, person, title="=HYPERLINK(1)"))

        run = await _store(signed_in)

        (row,) = await _rows(signed_in, run["file"]["id"])
        assert row["title"] == "'=HYPERLINK(1)"

    async def test_an_empty_last_column_is_refused(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        await _create(signed_in, person)

        refused = await signed_in.post("/projects/ATL/cold-storage")

        assert refused.status_code == 422
        assert "nothing to put in cold storage" in refused.json()["error"]["message"]

    async def test_the_outcome_goes_into_the_csv(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        last = (await _columns(signed_in))[-1]
        await signed_in.patch(f"/columns/{last['id']}", json={"outcomes": ["Done", "Cancelled"]})
        await _finish(signed_in, await _create(signed_in, person), outcome="Cancelled")

        run = await _store(signed_in)

        (row,) = await _rows(signed_in, run["file"]["id"])
        assert row["outcome"] == "Cancelled"

    async def test_stored_cards_are_listed(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        first = await _create(signed_in, person)
        await _finish(signed_in, first)
        await _store(signed_in)
        second = await _create(signed_in, person)
        await _finish(signed_in, second)
        await _store(signed_in)

        stored = (await signed_in.get("/projects/ATL/cold-storage")).json()["items"]

        assert [task["reference"] for task in stored] == [
            second["reference"],
            first["reference"],
        ]

    async def test_a_goal_still_counts_a_stored_card_as_done(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        goal = (
            await signed_in.post("/projects/ATL/goals", json={"name": "Search", "owner_id": person})
        ).json()
        card = await _create(signed_in, person, goal_id=goal["id"])
        await _create(signed_in, person, goal_id=goal["id"])
        await _finish(signed_in, card)

        await _store(signed_in)

        progress = (await signed_in.get(f"/goals/{goal['reference']}")).json()["progress"]
        assert progress["total"] == 2
        assert progress["done"] == 1
        assert progress["open"] == 1

    async def test_the_history_says_where_it_went(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        card = await _create(signed_in, person)
        await _finish(signed_in, card)

        run = await _store(signed_in)

        history = (await signed_in.get(f"/tasks/{card['reference']}/history")).json()
        assert history["entries"][0]["verb"] == "task.archived"
        assert history["entries"][0]["summary"] == (
            f"Moved to cold storage, in {run['file']['name']}."
        )


class TestAStoredCard:
    async def test_cannot_be_moved(self, signed_in: AsyncClient) -> None:
        """Back to the board is a restore, not a drag from nowhere."""
        person = await _setup(signed_in)
        card = await _create(signed_in, person)
        await _finish(signed_in, card)
        await _store(signed_in)
        first = (await _columns(signed_in))[0]

        refused = await signed_in.post(
            f"/tasks/{card['reference']}/move", json={"column_id": first["id"], "position": 0}
        )

        assert refused.status_code == 422
        assert "cold storage" in refused.json()["error"]["message"]

    async def test_cannot_be_split(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        card = await _create(signed_in, person)
        await _finish(signed_in, card)
        await _store(signed_in)

        refused = await signed_in.post(
            f"/tasks/{card['reference']}/subtasks",
            json={
                "title": "More",
                "description": "Late work.",
                "type": "chore",
                "assignee_id": person,
            },
        )

        assert refused.status_code == 422
        assert "cold storage" in refused.json()["error"]["message"]

    async def test_can_still_be_deleted(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        card = await _create(signed_in, person)
        await _finish(signed_in, card)
        await _store(signed_in)

        deleted = await signed_in.delete(f"/tasks/{card['reference']}")

        assert deleted.status_code == 200
        assert (await signed_in.get("/projects/ATL/cold-storage")).json()["items"] == []


class TestRestoring:
    async def test_it_goes_back_to_the_top_of_the_last_column(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        card = await _create(signed_in, person)
        await _finish(signed_in, card)
        await _store(signed_in)
        already_there = await _create(signed_in, person)
        await _finish(signed_in, already_there)

        restored = await signed_in.post(f"/tasks/{card['reference']}/restore")

        assert restored.status_code == 200, restored.text
        body = restored.json()
        last = (await _columns(signed_in))[-1]
        assert body["column_id"] == last["id"]
        assert body["position"] == 0
        assert body["archived_at"] is None
        assert body["finished_at"] is not None
        assert await _board_refs(signed_in) == [card["reference"], already_there["reference"]]
        assert (await signed_in.get("/projects/ATL/cold-storage")).json()["items"] == []

    async def test_it_keeps_the_outcome_it_ended_on(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        last = (await _columns(signed_in))[-1]
        await signed_in.patch(f"/columns/{last['id']}", json={"outcomes": ["Done", "In prod"]})
        card = await _create(signed_in, person)
        await _finish(signed_in, card, outcome="In prod")
        await _store(signed_in)

        restored = (await signed_in.post(f"/tasks/{card['reference']}/restore")).json()

        assert restored["outcome"] == "In prod"

    async def test_it_lands_in_whatever_is_last_now(self, signed_in: AsyncClient) -> None:
        """Last is a position, and the board may have grown while the card was away."""
        person = await _setup(signed_in)
        card = await _create(signed_in, person)
        await _finish(signed_in, card)
        await _store(signed_in)
        added = await signed_in.post(
            "/projects/ATL/columns", json={"name": "In prod", "description": "Released."}
        )

        restored = (await signed_in.post(f"/tasks/{card['reference']}/restore")).json()

        assert restored["column_id"] == added.json()["id"]

    async def test_a_card_on_the_board_is_refused(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        card = await _create(signed_in, person)

        refused = await signed_in.post(f"/tasks/{card['reference']}/restore")

        assert refused.status_code == 422

    async def test_the_history_says_it_came_back(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        card = await _create(signed_in, person)
        await _finish(signed_in, card)
        await _store(signed_in)

        await signed_in.post(f"/tasks/{card['reference']}/restore")

        history = (await signed_in.get(f"/tasks/{card['reference']}/history")).json()
        assert history["entries"][0]["summary"] == "Restored from cold storage to Done."


class TestWhoMay:
    async def _aditi_as_reviewer(self, admin: AsyncClient, other: AsyncClient) -> None:
        await admin.post("/projects", json=ATLAS)
        person, token = await open_account(admin, ADITI, ADITI_EMAIL)
        accepted = await other.post(
            "/auth/accept-invite", json={"token": token, "password": INVITEE_PASSWORD}
        )
        assert accepted.status_code == 200, accepted.text
        members = (await admin.get("/projects/ATL/members")).json()["members"]
        owner = next(member["id"] for member in members if member["name"] == OWNER_NAME)
        await put_members(admin, "ATL", [owner, person["id"]])
        await admin.post("/projects/ATL/roles", json={"name": "Reviewer"})
        await admin.put(f"/projects/ATL/members/{person['id']}/role", json={"role": "Reviewer"})

        card = await _create(admin, person["id"])
        await _finish(admin, card)

    async def test_storing_needs_files_as_well_as_tasks(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        """It writes a file, so a role kept out of the files is kept out of it."""
        await self._aditi_as_reviewer(signed_in, other_client)
        await signed_in.put(
            "/projects/ATL/roles/Reviewer/permissions",
            json={"permissions": [Permission.TASKS.value]},
        )

        refused = await other_client.post("/projects/ATL/cold-storage")

        assert refused.status_code == 403

    async def test_listing_needs_nothing_but_read(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await self._aditi_as_reviewer(signed_in, other_client)
        await signed_in.put("/projects/ATL/roles/Reviewer/permissions", json={"permissions": []})
        await _store(signed_in)

        listed = await other_client.get("/projects/ATL/cold-storage")

        assert listed.status_code == 200
        assert len(listed.json()["items"]) == 1


class TestWhatAStoredCardStillSays:
    """Review fixes: a stored card is off the board but not forgotten."""

    async def test_the_hub_counts_only_cards_in_columns(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        await _finish(signed_in, await _create(signed_in, person))
        await _create(signed_in, person)
        await _store(signed_in)

        summary = (await signed_in.get("/projects/ATL/summary")).json()
        in_columns = sum(column["task_count"] for column in await _columns(signed_in))

        assert summary["task_count"] == in_columns == 1

    async def test_a_goal_page_lists_its_stored_cards(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        goal = (
            await signed_in.post("/projects/ATL/goals", json={"name": "Search", "owner_id": person})
        ).json()
        stored = await _create(signed_in, person, goal_id=goal["id"], title="Stored")
        on_board = await _create(signed_in, person, goal_id=goal["id"], title="On board")
        await _finish(signed_in, stored)
        await _store(signed_in)

        page = (await signed_in.get(f"/goals/{goal['reference']}")).json()

        assert [task["reference"] for task in page["tasks"]] == [
            on_board["reference"],
            stored["reference"],
        ]
        assert page["progress"]["total"] == len(page["tasks"])

    async def test_a_stored_card_still_says_how_it_ended(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        last = (await _columns(signed_in))[-1]
        await signed_in.patch(f"/columns/{last['id']}", json={"outcomes": ["Done", "In prod"]})
        card = await _create(signed_in, person)
        await _finish(signed_in, card, outcome="In prod")
        await _store(signed_in)

        listed = (await signed_in.get("/projects/ATL/cold-storage")).json()["items"]
        opened = (await signed_in.get(f"/tasks/{card['reference']}")).json()

        assert [task["outcome"] for task in listed] == ["In prod"]
        assert opened["outcome"] == "In prod"
