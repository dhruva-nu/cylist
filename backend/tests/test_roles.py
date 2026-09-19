"""Roles: what somebody is on a project, and who gets to say.

Two things are being tested here and they are worth keeping apart. One is the
ordinary CRUD of a named thing on a project — create, rename, recolour, delete
— which looks like goals or templates and holds few surprises. The other is
the admin rule, which is the only authority this change introduces: who may
manage roles, and the several ways a project could otherwise end up with
nobody able to.
"""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Person, ProjectMember
from tests.conftest import INVITEE_PASSWORD, OWNER_NAME, open_account

ATLAS = {"key": "ATL", "name": "Atlas Billing Migration"}
HERMES = {"key": "HRM", "name": "Hermes Notifications"}

ADITI = {
    "name": "Aditi K",
    "kind": "team",
    "title": "Backend engineer",
    "responsibilities": "Payments and webhooks.",
}
ADITI_EMAIL = "aditi@cylist.dev"

SANJAY = {
    "name": "Sanjay F",
    "kind": "client",
    "title": "Finance controller, Atlas",
    "responsibilities": "Approves tax and vendor accounts.",
}

REVIEWER = {"name": "Reviewer", "description": "Signs work off before it ships."}
UNKNOWN_ID = "00000000-0000-7000-8000-000000000000"


async def _project_with(client: AsyncClient, person: dict[str, object]) -> str:
    """A project whose creator is its admin, plus one more member on it."""
    await client.post("/projects", json=ATLAS)
    added = (await client.post("/people", json=person)).json()["id"]
    owner = await _member_id(client, OWNER_NAME)
    await client.put("/projects/ATL/members", json={"person_ids": [owner, added]})
    return str(added)


async def _member_id(client: AsyncClient, name: str) -> str:
    """One member's id by name — the list is ordered by kind then name, so
    position says nothing about who created the project."""
    members = (await client.get("/projects/ATL/members")).json()["members"]
    return str(next(member["id"] for member in members if member["name"] == name))


async def _todays_sentences(client: AsyncClient) -> list[str]:
    """Everything the project's day report says happened away from a card."""
    report = (await client.get("/projects/ATL/reports/day")).json()
    return [entry["summary"] for entry in report["elsewhere"]]


async def _sign_in_as_a_second_person(admin: AsyncClient, other: AsyncClient) -> dict[str, object]:
    """Open an account for Aditi and put her browser behind it."""
    person, token = await open_account(admin, ADITI, ADITI_EMAIL)
    response = await other.post(
        "/auth/accept-invite", json={"token": token, "password": INVITEE_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return person


class TestWhatAProjectStartsWith:
    async def test_a_new_project_has_an_admin_role(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)

        roles = (await signed_in.get("/projects/ATL/roles")).json()

        assert [role["name"] for role in roles] == ["Admin"]
        assert roles[0]["is_admin"] is True
        assert roles[0]["member_count"] == 1

    async def test_whoever_created_it_wears_it(self, signed_in: AsyncClient) -> None:
        """The whole of "roles are created by whoever creates the project"."""
        await signed_in.post("/projects", json=ATLAS)

        members = (await signed_in.get("/projects/ATL/members")).json()["members"]

        assert [member["name"] for member in members] == [OWNER_NAME]
        assert members[0]["role"]["name"] == "Admin"

    async def test_a_member_added_afterwards_has_no_role(self, signed_in: AsyncClient) -> None:
        """Nothing is seeded onto a new member. A role is something said about
        them, and until an admin says it, the honest answer is null."""
        added = await _project_with(signed_in, ADITI)

        members = (await signed_in.get("/projects/ATL/members")).json()["members"]

        assert {m["id"]: m["role"] for m in members}[added] is None


class TestCreating:
    async def test_adds_a_role_with_a_colour_from_the_palette(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)

        response = await signed_in.post("/projects/ATL/roles", json=REVIEWER)

        assert response.status_code == 201
        assert response.json()["colour"].startswith("#")
        assert response.json()["member_count"] == 0

    async def test_the_admin_role_comes_first_then_the_rest_by_name(
        self, signed_in: AsyncClient
    ) -> None:
        await signed_in.post("/projects", json=ATLAS)
        for name in ("Reviewer", "Designer"):
            await signed_in.post("/projects/ATL/roles", json={"name": name})

        roles = (await signed_in.get("/projects/ATL/roles")).json()

        assert [role["name"] for role in roles] == ["Admin", "Designer", "Reviewer"]

    async def test_two_roles_cannot_share_a_name(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        await signed_in.post("/projects/ATL/roles", json=REVIEWER)

        response = await signed_in.post("/projects/ATL/roles", json=REVIEWER)

        assert response.status_code == 409

    async def test_the_clash_ignores_case(self, signed_in: AsyncClient) -> None:
        """Somebody who typed "reviewer" does not think they made a second one."""
        await signed_in.post("/projects", json=ATLAS)
        await signed_in.post("/projects/ATL/roles", json=REVIEWER)

        response = await signed_in.post("/projects/ATL/roles", json={"name": "reviewer"})

        assert response.status_code == 409

    async def test_two_projects_may_each_have_a_reviewer(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        await signed_in.post("/projects", json=HERMES)
        await signed_in.post("/projects/ATL/roles", json=REVIEWER)

        response = await signed_in.post("/projects/HRM/roles", json=REVIEWER)

        assert response.status_code == 201


class TestChanging:
    async def test_renames_and_recolours(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        role = (await signed_in.post("/projects/ATL/roles", json=REVIEWER)).json()

        response = await signed_in.patch(
            f"/projects/ATL/roles/{role['id']}",
            json={"name": "Approver", "colour": "#3B6FC2"},
        )

        assert response.status_code == 200
        assert response.json()["name"] == "Approver"
        assert response.json()["colour"] == "#3B6FC2"

    async def test_a_role_is_addressable_by_name(self, signed_in: AsyncClient) -> None:
        """So an agent can use the word it read off a badge."""
        await signed_in.post("/projects", json=ATLAS)
        await signed_in.post("/projects/ATL/roles", json=REVIEWER)

        response = await signed_in.patch(
            "/projects/ATL/roles/reviewer", json={"description": "Signs off."}
        )

        assert response.status_code == 200
        assert response.json()["description"] == "Signs off."

    async def test_the_admin_role_cannot_be_renamed(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)

        response = await signed_in.patch("/projects/ATL/roles/Admin", json={"name": "Boss"})

        assert response.status_code == 422

    async def test_the_admin_role_can_still_be_recoloured(self, signed_in: AsyncClient) -> None:
        """The rule is about the name, which prose depends on — not the badge."""
        await signed_in.post("/projects", json=ATLAS)

        response = await signed_in.patch("/projects/ATL/roles/Admin", json={"colour": "#B5533F"})

        assert response.status_code == 200

    async def test_a_role_on_another_project_is_not_found(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        await signed_in.post("/projects", json=HERMES)
        role = (await signed_in.post("/projects/ATL/roles", json=REVIEWER)).json()

        response = await signed_in.patch(
            f"/projects/HRM/roles/{role['id']}", json={"name": "Approver"}
        )

        assert response.status_code == 404


class TestDeleting:
    async def test_removes_a_role_nobody_holds(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        role = (await signed_in.post("/projects/ATL/roles", json=REVIEWER)).json()

        response = await signed_in.delete(f"/projects/ATL/roles/{role['id']}")

        assert response.status_code == 204
        assert [r["name"] for r in (await signed_in.get("/projects/ATL/roles")).json()] == ["Admin"]

    async def test_a_held_role_is_refused_and_names_the_holder(
        self, signed_in: AsyncClient
    ) -> None:
        """A badge should not vanish off somebody's name because a list was tidied."""
        added = await _project_with(signed_in, ADITI)
        role = (await signed_in.post("/projects/ATL/roles", json=REVIEWER)).json()
        await signed_in.put(f"/projects/ATL/members/{added}/role", json={"role": role["id"]})

        response = await signed_in.delete(f"/projects/ATL/roles/{role['id']}")

        assert response.status_code == 422
        assert "Aditi K" in response.json()["error"]["message"]
        assert response.json()["error"]["details"]["holder_person_ids"] == [added]

    async def test_the_admin_role_cannot_be_deleted(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)

        response = await signed_in.delete("/projects/ATL/roles/Admin")

        assert response.status_code == 422


class TestWearingOne:
    async def test_puts_a_role_on_a_member(self, signed_in: AsyncClient) -> None:
        added = await _project_with(signed_in, ADITI)
        await signed_in.post("/projects/ATL/roles", json=REVIEWER)

        response = await signed_in.put(
            f"/projects/ATL/members/{added}/role", json={"role": "Reviewer"}
        )

        assert response.status_code == 200
        assert response.json()["name"] == "Reviewer"

    async def test_the_member_list_carries_it(self, signed_in: AsyncClient) -> None:
        added = await _project_with(signed_in, ADITI)
        await signed_in.post("/projects/ATL/roles", json=REVIEWER)
        await signed_in.put(f"/projects/ATL/members/{added}/role", json={"role": "Reviewer"})

        members = (await signed_in.get("/projects/ATL/members")).json()["members"]

        assert {m["id"]: m["role"] and m["role"]["name"] for m in members}[added] == "Reviewer"

    async def test_null_takes_it_off_again(self, signed_in: AsyncClient) -> None:
        added = await _project_with(signed_in, ADITI)
        await signed_in.post("/projects/ATL/roles", json=REVIEWER)
        await signed_in.put(f"/projects/ATL/members/{added}/role", json={"role": "Reviewer"})

        response = await signed_in.put(f"/projects/ATL/members/{added}/role", json={"role": None})

        assert response.status_code == 200
        assert response.json() is None

    async def test_somebody_not_on_the_project_cannot_wear_its_role(
        self, signed_in: AsyncClient
    ) -> None:
        await signed_in.post("/projects", json=ATLAS)
        await signed_in.post("/projects/ATL/roles", json=REVIEWER)
        stranger = (await signed_in.post("/people", json=SANJAY)).json()["id"]

        response = await signed_in.put(
            f"/projects/ATL/members/{stranger}/role", json={"role": "Reviewer"}
        )

        assert response.status_code == 422

    async def test_another_projects_role_is_not_found(self, signed_in: AsyncClient) -> None:
        """The composite foreign key makes this impossible at the schema level;
        the service refuses it long before, and with a message."""
        added = await _project_with(signed_in, ADITI)
        await signed_in.post("/projects", json=HERMES)
        elsewhere = (await signed_in.post("/projects/HRM/roles", json=REVIEWER)).json()

        response = await signed_in.put(
            f"/projects/ATL/members/{added}/role", json={"role": elsewhere["id"]}
        )

        assert response.status_code == 404


class TestKeepingAnAdmin:
    async def test_the_last_admin_cannot_be_demoted(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        await signed_in.post("/projects/ATL/roles", json=REVIEWER)
        owner = await _member_id(signed_in, OWNER_NAME)

        response = await signed_in.put(
            f"/projects/ATL/members/{owner}/role", json={"role": "Reviewer"}
        )

        assert response.status_code == 422
        assert OWNER_NAME in response.json()["error"]["message"]

    async def test_a_second_admin_frees_the_first(self, signed_in: AsyncClient) -> None:
        added = await _project_with(signed_in, ADITI)
        await signed_in.post("/projects/ATL/roles", json=REVIEWER)
        owner = await _member_id(signed_in, OWNER_NAME)
        await signed_in.put(f"/projects/ATL/members/{added}/role", json={"role": "Admin"})

        response = await signed_in.put(
            f"/projects/ATL/members/{owner}/role", json={"role": "Reviewer"}
        )

        assert response.status_code == 200

    async def test_dropping_the_last_admin_passes_the_role_on(self, signed_in: AsyncClient) -> None:
        """A membership edit says who is on the board, not who runs it.

        Refusing it over a role would be answering a question nobody asked, so
        the role follows the project and lands on whoever has been on it
        longest.
        """
        added = await _project_with(signed_in, ADITI)

        await signed_in.put("/projects/ATL/members", json={"person_ids": [added]})

        members = (await signed_in.get("/projects/ATL/members")).json()["members"]
        assert [m["name"] for m in members] == ["Aditi K"]
        assert members[0]["role"]["name"] == "Admin"

    async def test_an_emptied_project_keeps_the_role_with_nobody_on_it(
        self, signed_in: AsyncClient
    ) -> None:
        await signed_in.post("/projects", json=ATLAS)

        await signed_in.put("/projects/ATL/members", json={"person_ids": []})

        roles = (await signed_in.get("/projects/ATL/roles")).json()
        assert roles[0]["member_count"] == 0

    async def test_the_next_person_on_an_adminless_project_takes_it(
        self, signed_in: AsyncClient
    ) -> None:
        await signed_in.post("/projects", json=ATLAS)
        await signed_in.put("/projects/ATL/members", json={"person_ids": []})
        added = (await signed_in.post("/people", json=ADITI)).json()["id"]

        await signed_in.put("/projects/ATL/members", json={"person_ids": [added]})

        members = (await signed_in.get("/projects/ATL/members")).json()["members"]
        assert members[0]["role"]["name"] == "Admin"

    async def test_an_existing_role_is_not_overwritten_to_fill_the_vacancy(
        self, signed_in: AsyncClient, session: AsyncSession
    ) -> None:
        """A vacancy is not a reason to quietly change what somebody is."""
        added = await _project_with(signed_in, ADITI)
        await signed_in.post("/projects/ATL/roles", json=REVIEWER)
        await signed_in.put(f"/projects/ATL/members/{added}/role", json={"role": "Reviewer"})

        await signed_in.put("/projects/ATL/members", json={"person_ids": [added]})

        members = (await signed_in.get("/projects/ATL/members")).json()["members"]
        assert members[0]["role"]["name"] == "Reviewer"


class TestMembershipKeepsRoles:
    async def test_saving_the_same_list_twice_keeps_everyones_role(
        self, signed_in: AsyncClient
    ) -> None:
        """The regression this change invites.

        ``PUT /members`` used to delete every row and write the list back, and
        the CLI's ``projects members --add`` still reads the list, appends a
        name and sends the whole thing. A rewrite would throw every role away
        on each of those.
        """
        added = await _project_with(signed_in, ADITI)
        await signed_in.post("/projects/ATL/roles", json=REVIEWER)
        await signed_in.put(f"/projects/ATL/members/{added}/role", json={"role": "Reviewer"})
        owner = await _member_id(signed_in, OWNER_NAME)

        await signed_in.put("/projects/ATL/members", json={"person_ids": [owner, added]})

        members = (await signed_in.get("/projects/ATL/members")).json()["members"]
        worn = {member["name"]: member["role"]["name"] for member in members}
        assert worn == {OWNER_NAME: "Admin", "Aditi K": "Reviewer"}

    async def test_adding_somebody_leaves_the_others_where_they_are(
        self, signed_in: AsyncClient, session: AsyncSession
    ) -> None:
        added = await _project_with(signed_in, ADITI)
        owner = await _member_id(signed_in, OWNER_NAME)
        joined = await session.scalar(
            select(ProjectMember.created_at).where(ProjectMember.person_id == owner)
        )
        third = (await signed_in.post("/people", json=SANJAY)).json()["id"]

        await signed_in.put("/projects/ATL/members", json={"person_ids": [owner, added, third]})

        assert (
            await session.scalar(
                select(ProjectMember.created_at).where(ProjectMember.person_id == owner)
            )
            == joined
        )

    async def test_removing_somebody_leaves_them_in_the_directory(
        self, signed_in: AsyncClient, session: AsyncSession
    ) -> None:
        added = await _project_with(signed_in, ADITI)
        owner = await _member_id(signed_in, OWNER_NAME)

        await signed_in.put("/projects/ATL/members", json={"person_ids": [owner]})

        assert await session.get(Person, added) is not None


class TestWhoMayManageThem:
    async def test_a_member_who_is_not_an_admin_is_refused(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await signed_in.post("/projects", json=ATLAS)
        person = await _sign_in_as_a_second_person(signed_in, other_client)
        owner = await _member_id(signed_in, OWNER_NAME)
        await signed_in.put("/projects/ATL/members", json={"person_ids": [owner, person["id"]]})

        response = await other_client.post("/projects/ATL/roles", json=REVIEWER)

        assert response.status_code == 403
        assert "admin of ATL" in response.json()["error"]["message"]

    async def test_but_they_can_read_them(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        """A badge nobody can look up is a badge nobody can read."""
        await signed_in.post("/projects", json=ATLAS)
        person = await _sign_in_as_a_second_person(signed_in, other_client)
        owner = await _member_id(signed_in, OWNER_NAME)
        await signed_in.put("/projects/ATL/members", json={"person_ids": [owner, person["id"]]})

        response = await other_client.get("/projects/ATL/roles")

        assert response.status_code == 200

    async def test_an_admin_of_one_project_is_not_an_admin_of_another(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        """Roles are per project, which is the whole reason they are."""
        await signed_in.post("/projects", json=ATLAS)
        person = await _sign_in_as_a_second_person(signed_in, other_client)
        await other_client.post("/projects", json=HERMES)

        assert (await other_client.post("/projects/HRM/roles", json=REVIEWER)).status_code == 201
        assert (await other_client.post("/projects/ATL/roles", json=REVIEWER)).status_code == 403
        assert person["id"]

    async def test_an_admins_agent_token_acts_as_them(
        self, signed_in: AsyncClient, client: AsyncClient
    ) -> None:
        """A token is its owner, which is the point of CYLIST-44.

        So an agent minted by an admin may manage roles, because that is the
        admin doing it through a program. Narrowing that further is what the
        `admin` scope is for, and it is not what this rule is about: this rule
        asks who somebody is on a board.
        """
        await signed_in.post("/projects", json=ATLAS)
        issued = (
            await signed_in.post("/tokens", json={"name": "board agent", "scopes": ["write"]})
        ).json()

        response = await client.post(
            "/projects/ATL/roles",
            json=REVIEWER,
            headers={"Authorization": f"Bearer {issued['token']}"},
        )

        assert response.status_code == 201

    async def test_a_read_token_cannot_write_one_either(
        self, signed_in: AsyncClient, client: AsyncClient
    ) -> None:
        await signed_in.post("/projects", json=ATLAS)
        issued = (
            await signed_in.post("/tokens", json={"name": "reader", "scopes": ["read"]})
        ).json()

        response = await client.post(
            "/projects/ATL/roles",
            json=REVIEWER,
            headers={"Authorization": f"Bearer {issued['token']}"},
        )

        assert response.status_code == 403

    async def test_an_admin_token_can_step_in_where_nobody_is_left(
        self, signed_in: AsyncClient, client: AsyncClient
    ) -> None:
        """The way back from a project whose only admin was archived.

        Archiving is not an ordinary route and does not consult the rules that
        keep an admin, so this is what stops a board being unadministrable for
        good.
        """
        await signed_in.post("/projects", json=ATLAS)
        await signed_in.put("/projects/ATL/members", json={"person_ids": []})
        issued = (
            await signed_in.post(
                "/tokens", json={"name": "deployment", "scopes": ["write", "admin"]}
            )
        ).json()

        response = await client.post(
            "/projects/ATL/roles",
            json=REVIEWER,
            headers={"Authorization": f"Bearer {issued['token']}"},
        )

        assert response.status_code == 201


class TestTheAuditTrail:
    """The day report is where a project-level change becomes a sentence."""

    async def test_a_new_role_reads_as_a_sentence(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)

        await signed_in.post("/projects/ATL/roles", json=REVIEWER)

        assert "Added the role “Reviewer”." in await _todays_sentences(signed_in)

    async def test_handing_one_out_names_both(self, signed_in: AsyncClient) -> None:
        added = await _project_with(signed_in, ADITI)
        await signed_in.post("/projects/ATL/roles", json=REVIEWER)

        await signed_in.put(f"/projects/ATL/members/{added}/role", json={"role": "Reviewer"})

        assert "Made Aditi K “Reviewer” on this project." in await _todays_sentences(signed_in)

    async def test_taking_one_off_says_so(self, signed_in: AsyncClient) -> None:
        added = await _project_with(signed_in, ADITI)
        await signed_in.post("/projects/ATL/roles", json=REVIEWER)
        await signed_in.put(f"/projects/ATL/members/{added}/role", json={"role": "Reviewer"})

        await signed_in.put(f"/projects/ATL/members/{added}/role", json={"role": None})

        assert "Took Aditi K's role off." in await _todays_sentences(signed_in)


class TestNotFound:
    async def test_an_unknown_role(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)

        response = await signed_in.patch("/projects/ATL/roles/Nobody", json={"description": "x"})

        assert response.status_code == 404

    async def test_an_unknown_person(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)

        response = await signed_in.put(
            f"/projects/ATL/members/{UNKNOWN_ID}/role", json={"role": "Admin"}
        )

        assert response.status_code == 404
