"""Projects: creation, addressing by key, updates and archiving."""

from __future__ import annotations

from httpx import AsyncClient

from app.config import Settings
from app.db import Database
from app.models.person import Person
from tests.conftest import INVITEE_PASSWORD, client_for, open_account

ADITI = {
    "name": "Aditi K",
    "kind": "team",
    "title": "Backend engineer",
    "responsibilities": "Payments and webhooks.",
}

ATLAS = {
    "key": "ATL",
    "name": "Atlas Billing Migration",
    "description": "Move invoicing onto the new FastAPI billing core.",
}
HERMES = {"key": "HRM", "name": "Hermes Notifications"}


class TestCreating:
    async def test_starts_a_project(self, signed_in: AsyncClient) -> None:
        response = await signed_in.post("/projects", json=ATLAS)

        assert response.status_code == 201
        body = response.json()
        assert body["key"] == "ATL"
        assert body["member_count"] == 2  # whoever created it, and the agent — see below
        assert body["archived_at"] is None

    async def test_uppercases_the_key(self, signed_in: AsyncClient) -> None:
        body = (await signed_in.post("/projects", json={**ATLAS, "key": "atl"})).json()

        assert body["key"] == "ATL"

    async def test_refuses_a_duplicate_key(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)

        response = await signed_in.post("/projects", json={**ATLAS, "name": "Something else"})

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"
        assert response.json()["error"]["details"]["key"] == "ATL"

    async def test_a_duplicate_key_does_not_leave_a_half_written_project(
        self, signed_in: AsyncClient
    ) -> None:
        await signed_in.post("/projects", json=ATLAS)
        await signed_in.post("/projects", json={**ATLAS, "name": "Something else"})

        assert len((await signed_in.get("/projects")).json()) == 1

    async def test_rejects_a_key_that_is_too_long(self, signed_in: AsyncClient) -> None:
        assert (
            await signed_in.post("/projects", json={**ATLAS, "key": "TOOLONG"})
        ).status_code == 422

    async def test_rejects_a_key_starting_with_a_digit(self, signed_in: AsyncClient) -> None:
        """Keys prefix task numbers, so 1AT-4 would be needlessly confusing."""
        assert (await signed_in.post("/projects", json={**ATLAS, "key": "1AT"})).status_code == 422

    async def test_description_defaults_to_empty(self, signed_in: AsyncClient) -> None:
        body = (await signed_in.post("/projects", json=HERMES)).json()

        assert body["description"] == ""


class TestAddressing:
    async def test_can_be_fetched_by_id(self, signed_in: AsyncClient) -> None:
        created = (await signed_in.post("/projects", json=ATLAS)).json()

        assert (await signed_in.get(f"/projects/{created['id']}")).json()["key"] == "ATL"

    async def test_can_be_fetched_by_key(self, signed_in: AsyncClient) -> None:
        """So an agent can call /projects/ATL without resolving a UUID first."""
        await signed_in.post("/projects", json=ATLAS)

        assert (await signed_in.get("/projects/ATL")).json()["key"] == "ATL"

    async def test_the_key_is_case_insensitive(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)

        assert (await signed_in.get("/projects/atl")).status_code == 200

    async def test_an_unknown_reference_is_a_clean_404(self, signed_in: AsyncClient) -> None:
        response = await signed_in.get("/projects/NOPE")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    async def test_a_malformed_uuid_is_treated_as_a_key_not_a_crash(
        self, signed_in: AsyncClient
    ) -> None:
        assert (await signed_in.get("/projects/not-a-uuid")).status_code == 404


class TestUpdating:
    async def test_changes_only_the_fields_given(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)

        updated = (await signed_in.patch("/projects/ATL", json={"name": "Atlas v2"})).json()

        assert updated["name"] == "Atlas v2"
        assert updated["description"] == ATLAS["description"]

    async def test_the_key_can_be_corrected(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)

        updated = (await signed_in.patch("/projects/ATL", json={"key": "atlas"})).json()

        assert updated["key"] == "ATLAS"
        assert (await signed_in.get("/projects/ATLAS")).status_code == 200

    async def test_refuses_a_key_another_project_already_has(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        await signed_in.post("/projects", json=HERMES)

        response = await signed_in.patch("/projects/HRM", json={"key": "ATL"})

        assert response.status_code == 409


class TestArchiving:
    async def test_archived_projects_drop_out_of_the_grid(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)

        assert (await signed_in.delete("/projects/ATL")).status_code == 200

        assert (await signed_in.get("/projects")).json() == []
        assert (
            len((await signed_in.get("/projects", params={"include_archived": True})).json()) == 1
        )

    async def test_an_archived_project_is_still_addressable(self, signed_in: AsyncClient) -> None:
        """Nothing is deleted, so its board, files and vault stay reachable."""
        await signed_in.post("/projects", json=ATLAS)
        await signed_in.delete("/projects/ATL")

        assert (await signed_in.get("/projects/ATL")).status_code == 200

    async def test_it_can_be_brought_back(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        await signed_in.delete("/projects/ATL")

        restored = (await signed_in.patch("/projects/ATL", json={"archived": False})).json()

        assert restored["archived_at"] is None
        assert len((await signed_in.get("/projects")).json()) == 1


class TestAuditTrail:
    async def test_creating_a_project_is_recorded_against_that_project(
        self, signed_in: AsyncClient
    ) -> None:
        created = (await signed_in.post("/projects", json=ATLAS)).json()

        entries = (await signed_in.get("/activity", params={"project": created["id"]})).json()

        assert [entry["verb"] for entry in entries] == ["project.created"]
        assert entries[0]["payload"] == {"key": "ATL", "name": ATLAS["name"]}


class TestTheCreatorJoinsTheirProject:
    """Whoever starts a project is on it from the moment it exists.

    It used to be whoever held ``person.is_me`` — one person, the same one on
    every project on the deployment. Now it is the caller, which is what makes
    two people able to start projects on one board and each end up on their
    own.

    The agent joins beside them: a board assigns work to its own members, so a
    project whose agent had to be added by hand could not be given a card to
    do until somebody did.
    """

    async def test_a_new_project_has_its_creator_on_it(
        self, signed_in: AsyncClient, owner: Person
    ) -> None:
        project = (await signed_in.post("/projects", json=ATLAS)).json()

        members = (await signed_in.get(f"/projects/{project['key']}/members")).json()["members"]
        assert str(owner.id) in [person["id"] for person in members]

    async def test_a_new_project_has_the_agent_on_it(self, signed_in: AsyncClient) -> None:
        project = (await signed_in.post("/projects", json=ATLAS)).json()

        assert project["member_count"] == 2
        members = (await signed_in.get(f"/projects/{project['key']}/members")).json()["members"]
        agents = [person for person in members if person["is_agent"]]
        assert [person["name"] for person in agents] == ["Agent"]

    async def test_every_project_shares_one_agent(self, signed_in: AsyncClient) -> None:
        """One machine in the directory, on as many boards as there are."""
        await signed_in.post("/projects", json=ATLAS)
        await signed_in.post("/projects", json=HERMES)

        agents = [
            person for person in (await signed_in.get("/people")).json() if person["is_agent"]
        ]

        assert len(agents) == 1
        on_both = [
            [
                person["id"]
                for person in (await signed_in.get(f"/projects/{key}/members")).json()["members"]
                if person["is_agent"]
            ]
            for key in ("ATL", "HRM")
        ]
        assert on_both == [[agents[0]["id"]], [agents[0]["id"]]]

    async def test_two_people_each_join_their_own(
        self, signed_in: AsyncClient, owner: Person, settings: Settings, database: Database
    ) -> None:
        aditi, token = await open_account(signed_in, ADITI, "aditi@cylist.dev")
        mine = (await signed_in.post("/projects", json=ATLAS)).json()

        async with client_for(settings, database) as other:
            await other.post(
                "/auth/accept-invite", json={"token": token, "password": INVITEE_PASSWORD}
            )
            theirs = (await other.post("/projects", json=HERMES)).json()

        on_mine = (await signed_in.get(f"/projects/{mine['key']}/members")).json()["members"]
        on_theirs = (await signed_in.get(f"/projects/{theirs['key']}/members")).json()["members"]

        assert [person["id"] for person in on_mine if not person["is_agent"]] == [str(owner.id)]
        assert [person["id"] for person in on_theirs if not person["is_agent"]] == [aditi["id"]]

    async def test_a_bootstrap_session_starts_a_project_with_nobody_on_it(
        self, bootstrapped: AsyncClient
    ) -> None:
        """The one caller who is not a person, and so cannot join anything.

        Creating the project still works — refusing would leave a fresh
        deployment unable to do the first thing anyone does on it — and
        whoever opens the first account adds themselves.
        """
        project = (await bootstrapped.post("/projects", json=ATLAS)).json()

        members = (await bootstrapped.get(f"/projects/{project['key']}/members")).json()["members"]
        assert [person["is_agent"] for person in members] == [True]

    async def test_the_creator_can_be_taken_off_afterwards(self, signed_in: AsyncClient) -> None:
        """And so can the agent: neither is a member the board cannot lose."""
        project = (await signed_in.post("/projects", json=ATLAS)).json()

        await signed_in.put(f"/projects/{project['key']}/members", json={"person_ids": []})

        assert (await signed_in.get(f"/projects/{project['key']}")).json()["member_count"] == 0
