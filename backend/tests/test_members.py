"""Project membership: the pool the assignee and 'waiting on' pickers draw from."""

from __future__ import annotations

from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Person, ProjectMember

ATLAS = {"key": "ATL", "name": "Atlas Billing Migration"}
TEAM = {
    "name": "Aditi K",
    "kind": "team",
    "role": "Backend engineer",
    "responsibilities": "Payments and webhooks.",
}
CLIENT = {
    "name": "Sanjay F",
    "kind": "client",
    "role": "Finance controller, Atlas",
    "responsibilities": "Approves tax and vendor accounts.",
}
UNKNOWN_ID = "00000000-0000-7000-8000-000000000000"


async def _project_and_people(client: AsyncClient) -> tuple[str, str]:
    await client.post("/projects", json=ATLAS)
    team = (await client.post("/people", json=TEAM)).json()["id"]
    person = (await client.post("/people", json=CLIENT)).json()["id"]
    return team, person


class TestSettingMembership:
    async def test_starts_empty(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)

        assert (await signed_in.get("/projects/ATL/members")).json() == {"members": []}

    async def test_puts_people_on_a_project(self, signed_in: AsyncClient) -> None:
        team, client_person = await _project_and_people(signed_in)

        response = await signed_in.put(
            "/projects/ATL/members", json={"person_ids": [team, client_person]}
        )

        assert response.status_code == 200
        assert [m["name"] for m in response.json()["members"]] == ["Aditi K", "Sanjay F"]

    async def test_replaces_rather_than_appends(self, signed_in: AsyncClient) -> None:
        team, client_person = await _project_and_people(signed_in)
        await signed_in.put("/projects/ATL/members", json={"person_ids": [team, client_person]})

        response = await signed_in.put("/projects/ATL/members", json={"person_ids": [team]})

        assert [m["name"] for m in response.json()["members"]] == ["Aditi K"]

    async def test_can_be_emptied(self, signed_in: AsyncClient) -> None:
        team, _ = await _project_and_people(signed_in)
        await signed_in.put("/projects/ATL/members", json={"person_ids": [team]})

        response = await signed_in.put("/projects/ATL/members", json={"person_ids": []})

        assert response.json() == {"members": []}

    async def test_removing_someone_leaves_them_in_the_directory(
        self, signed_in: AsyncClient
    ) -> None:
        team, _ = await _project_and_people(signed_in)
        await signed_in.put("/projects/ATL/members", json={"person_ids": [team]})

        await signed_in.put("/projects/ATL/members", json={"person_ids": []})

        assert len((await signed_in.get("/people")).json()) == 2

    async def test_duplicate_ids_are_collapsed(self, signed_in: AsyncClient) -> None:
        """A double-submitted form must not violate the composite primary key."""
        team, _ = await _project_and_people(signed_in)

        response = await signed_in.put("/projects/ATL/members", json={"person_ids": [team, team]})

        assert response.status_code == 200
        assert len(response.json()["members"]) == 1

    async def test_is_idempotent(self, signed_in: AsyncClient) -> None:
        team, client_person = await _project_and_people(signed_in)
        payload = {"person_ids": [team, client_person]}

        first = await signed_in.put("/projects/ATL/members", json=payload)
        second = await signed_in.put("/projects/ATL/members", json=payload)

        assert first.json() == second.json()


class TestValidation:
    async def test_refuses_an_unknown_person(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)

        response = await signed_in.put("/projects/ATL/members", json={"person_ids": [UNKNOWN_ID]})

        assert response.status_code == 422
        assert response.json()["error"]["details"]["unknown_person_ids"] == [UNKNOWN_ID]

    async def test_refuses_an_archived_person(self, signed_in: AsyncClient) -> None:
        team, _ = await _project_and_people(signed_in)
        await signed_in.delete(f"/people/{team}")

        response = await signed_in.put("/projects/ATL/members", json={"person_ids": [team]})

        assert response.status_code == 422
        assert response.json()["error"]["details"]["archived_person_ids"] == [team]

    async def test_a_rejected_change_leaves_membership_untouched(
        self, signed_in: AsyncClient
    ) -> None:
        team, _ = await _project_and_people(signed_in)
        await signed_in.put("/projects/ATL/members", json={"person_ids": [team]})

        await signed_in.put("/projects/ATL/members", json={"person_ids": [team, UNKNOWN_ID]})

        members = (await signed_in.get("/projects/ATL/members")).json()["members"]
        assert [m["name"] for m in members] == ["Aditi K"]


class TestCounts:
    async def test_the_grid_shows_how_many_people_are_on_each_project(
        self, signed_in: AsyncClient
    ) -> None:
        team, client_person = await _project_and_people(signed_in)
        await signed_in.put("/projects/ATL/members", json={"person_ids": [team, client_person]})

        listed = (await signed_in.get("/projects")).json()

        assert listed[0]["member_count"] == 2

    async def test_the_summary_splits_team_from_clients(self, signed_in: AsyncClient) -> None:
        team, client_person = await _project_and_people(signed_in)
        await signed_in.put("/projects/ATL/members", json={"person_ids": [team, client_person]})

        summary = (await signed_in.get("/projects/ATL/summary")).json()

        assert summary["team_count"] == 1
        assert summary["client_count"] == 1
        assert summary["member_count"] == 2

    async def test_summary_counts_are_zero_for_an_empty_project(
        self, signed_in: AsyncClient
    ) -> None:
        await signed_in.post("/projects", json=ATLAS)

        summary = (await signed_in.get("/projects/ATL/summary")).json()

        assert summary["team_count"] == 0
        assert summary["client_count"] == 0


class TestCascade:
    async def test_deleting_a_person_row_removes_their_memberships(
        self, signed_in: AsyncClient, session: AsyncSession
    ) -> None:
        """Archiving is the normal path, but the foreign key must still be sound.

        Reaches past the API deliberately: this asserts the schema's ON DELETE
        CASCADE, which no route exercises.
        """
        team, _ = await _project_and_people(signed_in)
        await signed_in.put("/projects/ATL/members", json={"person_ids": [team]})

        person = await session.get(Person, UUID(team))
        assert person is not None
        await session.delete(person)
        await session.flush()

        remaining = list(await session.scalars(select(ProjectMember)))
        assert remaining == []
