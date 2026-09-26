"""What a role lets its holder do, and the fence that makes it true.

CYLIST-45's tests describe a role as a name. These describe it as a fence, and
the two halves worth reading twice are :class:`TestTheFence` — which proves
that a narrowed role is refused at every kind of path Cylist has, including the
many that never mention the project — and :class:`TestNothingIsFencedByDefault`,
which proves that turning this on changed nothing for a board nobody has
narrowed.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from app.auth.permissions import Permission
from tests.conftest import INVITEE_PASSWORD, OWNER_NAME, open_account

ATLAS = {"key": "ATL", "name": "Atlas Billing Migration"}

ADITI = {
    "name": "Aditi K",
    "kind": "team",
    "title": "Backend engineer",
    "responsibilities": "Payments and webhooks.",
}
ADITI_EMAIL = "aditi@cylist.dev"

EVERYTHING = sorted(permission.value for permission in Permission)


def _card(assignee_id: str, title: str = "Stripe webhook idempotency") -> dict[str, Any]:
    """A card body with every field the API insists on."""
    return {
        "title": title,
        "description": "Duplicate deliveries create double payments. Dedupe on event id.",
        "type": "bug",
        "assignee_id": assignee_id,
    }


async def _member_id(client: AsyncClient, name: str) -> str:
    members = (await client.get("/projects/ATL/members")).json()["members"]
    return str(next(member["id"] for member in members if member["name"] == name))


async def _second_person_on_atlas(
    admin: AsyncClient, other: AsyncClient, *, role: str | None = None
) -> str:
    """Aditi, signed in on her own browser, on ATL and wearing ``role``.

    The setup almost every test here needs: two people, one of them the admin,
    and a role on the other whose permissions the test then narrows.
    """
    await admin.post("/projects", json=ATLAS)
    person, token = await open_account(admin, ADITI, ADITI_EMAIL)
    accepted = await other.post(
        "/auth/accept-invite", json={"token": token, "password": INVITEE_PASSWORD}
    )
    assert accepted.status_code == 200, accepted.text

    owner_id = await _member_id(admin, OWNER_NAME)
    await admin.put("/projects/ATL/members", json={"person_ids": [owner_id, person["id"]]})

    if role is not None:
        created = await admin.post("/projects/ATL/roles", json={"name": role})
        assert created.status_code == 201, created.text
        assigned = await admin.put(
            f"/projects/ATL/members/{person['id']}/role", json={"role": role}
        )
        assert assigned.status_code == 200, assigned.text

    return str(person["id"])


async def _allow(admin: AsyncClient, role: str, *permitted: Permission) -> None:
    """Make ``permitted`` exactly what this role may do."""
    response = await admin.put(
        f"/projects/ATL/roles/{role}/permissions",
        json={"permissions": [permission.value for permission in permitted]},
    )
    assert response.status_code == 200, response.text


async def _allow_everyone_else(admin: AsyncClient, *permitted: Permission) -> None:
    response = await admin.put(
        "/projects/ATL/permissions/everyone-else",
        json={"permissions": [permission.value for permission in permitted]},
    )
    assert response.status_code == 200, response.text


async def _line(client: AsyncClient, name: str) -> dict[str, Any]:
    """One row of the grid, by the name it is drawn under."""
    grid = (await client.get("/projects/ATL/permissions")).json()
    return next(role for role in grid["roles"] if role["name"] == name)


async def _a_card(admin: AsyncClient) -> str:
    owner_id = await _member_id(admin, OWNER_NAME)
    created = await admin.post("/projects/ATL/tasks", json=_card(owner_id))
    assert created.status_code == 201, created.text
    return str(created.json()["reference"])


class TestNothingIsFencedByDefault:
    """A board nobody has narrowed behaves as every board did before roles."""

    async def test_a_new_project_permits_everybody_everything(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)

        assert (await _line(signed_in, "Everyone else"))["permissions"] == EVERYTHING

    async def test_the_admin_role_holds_every_permission(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)

        assert (await _line(signed_in, "Admin"))["permissions"] == EVERYTHING

    async def test_a_role_with_nobody_narrowing_it_may_do_everything(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        """Handing somebody a role is never, by itself, a demotion."""
        await _second_person_on_atlas(signed_in, other_client, role="Reviewer")

        assert (await _line(signed_in, "Reviewer"))["permissions"] == EVERYTHING
        assert (
            await other_client.post(
                "/projects/ATL/tasks", json=_card(await _member_id(signed_in, OWNER_NAME), "A")
            )
        ).status_code == 201

    async def test_a_new_role_starts_with_what_the_baseline_allows(
        self, signed_in: AsyncClient
    ) -> None:
        """Not everything — whatever somebody here with no role could do."""
        await signed_in.post("/projects", json=ATLAS)
        await _allow_everyone_else(signed_in, Permission.COMMENTS)

        await signed_in.post("/projects/ATL/roles", json={"name": "QA"})

        assert (await _line(signed_in, "QA"))["permissions"] == ["comments"]


class TestTheGrid:
    async def test_it_describes_every_permission_it_can_grant(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)

        grid = (await signed_in.get("/projects/ATL/permissions")).json()

        assert sorted(entry["key"] for entry in grid["catalogue"]) == EVERYTHING
        assert all(entry["label"] and entry["summary"] for entry in grid["catalogue"])

    async def test_the_lines_run_admin_then_roles_then_everyone_else(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _second_person_on_atlas(signed_in, other_client, role="Reviewer")

        grid = (await signed_in.get("/projects/ATL/permissions")).json()

        assert [role["name"] for role in grid["roles"]] == ["Admin", "Reviewer", "Everyone else"]

    async def test_everyone_else_counts_the_members_nobody_has_described(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _second_person_on_atlas(signed_in, other_client)

        assert (await _line(signed_in, "Everyone else"))["member_count"] == 1
        assert (await _line(signed_in, "Admin"))["member_count"] == 1

    async def test_it_tells_the_reader_what_they_themselves_may_do(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        """What a client hides buttons by, so it never offers one that refuses."""
        await _second_person_on_atlas(signed_in, other_client, role="Reviewer")
        await _allow(signed_in, "Reviewer", Permission.COMMENTS)

        mine = (await other_client.get("/projects/ATL/permissions")).json()["mine"]

        assert mine == ["comments"]

    async def test_an_admin_may_do_everything_whatever_is_stored(
        self, signed_in: AsyncClient
    ) -> None:
        await signed_in.post("/projects", json=ATLAS)

        assert (await signed_in.get("/projects/ATL/permissions")).json()["mine"] == EVERYTHING

    async def test_it_says_outright_whether_the_reader_may_change_any_of_it(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        """The client must not work this out from the member list: three
        different callers may administer a project, and the bootstrap session
        — which is who sets the first one up — is on none of them."""
        await _second_person_on_atlas(signed_in, other_client, role="Reviewer")

        assert (await signed_in.get("/projects/ATL/permissions")).json()["may_manage"] is True
        assert (await other_client.get("/projects/ATL/permissions")).json()["may_manage"] is False

    async def test_anybody_who_can_read_the_project_can_read_the_grid(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        """A client that cannot see this cannot tell refused from broken."""
        await _second_person_on_atlas(signed_in, other_client, role="Reviewer")

        assert (await other_client.get("/projects/ATL/permissions")).status_code == 200


class TestSayingWhatARoleMayDo:
    async def test_an_admin_replaces_the_whole_row(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _second_person_on_atlas(signed_in, other_client, role="Reviewer")

        await _allow(signed_in, "Reviewer", Permission.COMMENTS, Permission.FILES)

        assert (await _line(signed_in, "Reviewer"))["permissions"] == ["comments", "files"]

    async def test_a_role_can_be_left_allowing_nothing(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _second_person_on_atlas(signed_in, other_client, role="Reviewer")

        await _allow(signed_in, "Reviewer")

        assert (await _line(signed_in, "Reviewer"))["permissions"] == []

    async def test_it_is_addressed_by_name_like_every_other_role_path(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _second_person_on_atlas(signed_in, other_client, role="Reviewer")

        response = await signed_in.put(
            "/projects/ATL/roles/reviewer/permissions", json={"permissions": ["goals"]}
        )

        assert response.status_code == 200, response.text
        assert response.json()["permissions"] == ["goals"]

    async def test_the_admin_role_is_not_configurable(self, signed_in: AsyncClient) -> None:
        """An endpoint that let somebody untick Membership on it would be an
        endpoint for locking a board."""
        await signed_in.post("/projects", json=ATLAS)

        response = await signed_in.put(
            "/projects/ATL/roles/Admin/permissions", json={"permissions": []}
        )

        assert response.status_code == 422
        assert "cannot be changed" in response.json()["error"]["message"]

    async def test_only_an_admin_may_say(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _second_person_on_atlas(signed_in, other_client, role="Reviewer")

        response = await other_client.put(
            "/projects/ATL/roles/Reviewer/permissions", json={"permissions": []}
        )

        assert response.status_code == 403
        assert "admin of ATL" in response.json()["error"]["message"]

    async def test_only_an_admin_may_say_what_everyone_else_does(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _second_person_on_atlas(signed_in, other_client)

        response = await other_client.put(
            "/projects/ATL/permissions/everyone-else", json={"permissions": []}
        )

        assert response.status_code == 403

    async def test_a_permission_cylist_does_not_have_is_refused(
        self, signed_in: AsyncClient
    ) -> None:
        await signed_in.post("/projects", json=ATLAS)

        response = await signed_in.put(
            "/projects/ATL/permissions/everyone-else",
            json={"permissions": ["approve_budgets"]},
        )

        assert response.status_code == 422

    async def test_saying_so_lands_on_the_project_s_day(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        await signed_in.post("/projects/ATL/roles", json={"name": "Reviewer"})

        await _allow(signed_in, "Reviewer", Permission.COMMENTS)

        report = (await signed_in.get("/projects/ATL/reports/day")).json()
        assert any("Reviewer" in entry["summary"] for entry in report["elsewhere"])


class TestTheFence:
    """A narrowed role is refused, at every shape of path Cylist has."""

    async def test_a_card_cannot_be_created_without_cards(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _second_person_on_atlas(signed_in, other_client, role="Reviewer")
        await _allow(signed_in, "Reviewer", Permission.COMMENTS)

        response = await other_client.post(
            "/projects/ATL/tasks", json=_card(await _member_id(signed_in, OWNER_NAME), "Migrate")
        )

        assert response.status_code == 403
        assert (
            response.json()["error"]["message"] == "Your role on ATL does not let you change cards."
        )

    async def test_a_card_addressed_by_its_reference_is_fenced_too(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        """`/tasks/ATL-1` never mentions the project, and is fenced all the same."""
        await _second_person_on_atlas(signed_in, other_client, role="Reviewer")
        reference = await _a_card(signed_in)
        await _allow(signed_in, "Reviewer", Permission.COMMENTS)

        moved = await other_client.patch(f"/tasks/{reference}", json={"title": "Renamed"})

        assert moved.status_code == 403

    async def test_a_tick_box_addressed_by_its_own_id_is_fenced_too(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        """The one path that names neither a project nor a card."""
        await _second_person_on_atlas(signed_in, other_client, role="Reviewer")
        reference = await _a_card(signed_in)
        item = (
            await signed_in.post(f"/tasks/{reference}/checklist", json={"title": "Back up"})
        ).json()
        await _allow(signed_in, "Reviewer", Permission.COMMENTS)

        response = await other_client.patch(f"/checklist/{item['id']}", json={"state": "done"})

        assert response.status_code == 403

    async def test_commenting_is_its_own_permission(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        """The whole reason it is separate: somebody who must not move a card
        is often exactly the person whose comment you want."""
        await _second_person_on_atlas(signed_in, other_client, role="Reviewer")
        reference = await _a_card(signed_in)
        await _allow(signed_in, "Reviewer", Permission.COMMENTS)

        said = await other_client.post(
            f"/tasks/{reference}/comments", json={"body": "The ledger totals do not tie."}
        )

        assert said.status_code == 201, said.text

    async def test_a_comment_needs_that_permission(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _second_person_on_atlas(signed_in, other_client, role="Reviewer")
        reference = await _a_card(signed_in)
        await _allow(signed_in, "Reviewer", Permission.TASKS)

        said = await other_client.post(f"/tasks/{reference}/comments", json={"body": "No."})

        assert said.status_code == 403
        assert (
            said.json()["error"]["message"] == "Your role on ATL does not let you comment on cards."
        )

    async def test_the_board_s_shape_is_fenced(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _second_person_on_atlas(signed_in, other_client, role="Reviewer")
        await _allow(signed_in, "Reviewer", Permission.TASKS)

        response = await other_client.post(
            "/projects/ATL/columns", json={"name": "Blocked", "description": "Stuck cards."}
        )

        assert response.status_code == 403

    async def test_goals_are_fenced(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _second_person_on_atlas(signed_in, other_client, role="Reviewer")
        await _allow(signed_in, "Reviewer", Permission.TASKS)

        response = await other_client.post(
            "/projects/ATL/goals",
            json={"name": "Search revamp", "owner_id": await _member_id(signed_in, OWNER_NAME)},
        )

        assert response.status_code == 403

    async def test_files_are_fenced(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _second_person_on_atlas(signed_in, other_client, role="Reviewer")
        await _allow(signed_in, "Reviewer", Permission.TASKS)

        response = await other_client.post("/projects/ATL/folders", json={"name": "Contracts"})

        assert response.status_code == 403

    async def test_membership_is_fenced(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _second_person_on_atlas(signed_in, other_client, role="Reviewer")
        await _allow(signed_in, "Reviewer", Permission.TASKS)

        response = await other_client.put("/projects/ATL/members", json={"person_ids": []})

        assert response.status_code == 403
        assert "change who is on this project" in response.json()["error"]["message"]

    async def test_the_project_itself_is_fenced(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _second_person_on_atlas(signed_in, other_client, role="Reviewer")
        await _allow(signed_in, "Reviewer", Permission.TASKS)

        response = await other_client.patch("/projects/ATL", json={"name": "Atlas"})

        assert response.status_code == 403

    async def test_an_admin_is_never_fenced(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        """Even with every other line of the grid emptied."""
        await _second_person_on_atlas(signed_in, other_client, role="Reviewer")
        await _allow(signed_in, "Reviewer")
        await _allow_everyone_else(signed_in)

        assert (
            await signed_in.post(
                "/projects/ATL/tasks", json=_card(await _member_id(signed_in, OWNER_NAME), "A")
            )
        ).status_code == 201

    async def test_it_says_which_board_and_what(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        """ "Forbidden" on a shared workspace is a question; this answers it."""
        await _second_person_on_atlas(signed_in, other_client, role="Reviewer")
        await _allow(signed_in, "Reviewer")

        response = await other_client.post(
            "/projects/ATL/goals",
            json={"name": "Search", "owner_id": await _member_id(signed_in, OWNER_NAME)},
        )

        assert (
            response.json()["error"]["message"] == "Your role on ATL does not let you change goals."
        )
        assert response.json()["error"]["details"] == {"project_key": "ATL", "permission": "goals"}

    async def test_a_role_fences_one_board_and_not_the_next(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        """Which is the whole reason roles are per project."""
        await _second_person_on_atlas(signed_in, other_client, role="Reviewer")
        await _allow(signed_in, "Reviewer")
        await signed_in.post("/projects", json={"key": "HRM", "name": "Hermes"})

        assert (
            await other_client.post(
                "/projects/ATL/tasks", json=_card(await _member_id(signed_in, OWNER_NAME), "A")
            )
        ).status_code == 403
        assert (
            await other_client.post(
                "/projects/HRM/tasks", json=_card(await _member_id(signed_in, OWNER_NAME), "A")
            )
        ).status_code == 201


class TestTheVault:
    async def test_changing_an_entry_is_fenced(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _second_person_on_atlas(signed_in, other_client, role="Reviewer")
        await _allow(signed_in, "Reviewer", Permission.VAULT_REVEAL)

        response = await other_client.post("/projects/ATL/vault/trees", json={"name": "Logins"})

        assert response.status_code == 403
        assert "change the vault" in response.json()["error"]["message"]

    async def test_a_node_addressed_by_its_own_id_is_fenced_too(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        """A node knows its tree and the tree knows the project — one hop."""
        await _second_person_on_atlas(signed_in, other_client, role="Reviewer")
        tree = (await signed_in.post("/projects/ATL/vault/trees", json={"name": "Logins"})).json()
        node = (
            await signed_in.post(
                "/vault/nodes", json={"tree_id": tree["id"], "name": "Twilio", "kind": "branch"}
            )
        ).json()
        await _allow(signed_in, "Reviewer")

        assert (
            await other_client.patch(f"/vault/nodes/{node['id']}", json={"name": "T"})
        ).status_code == 403

    async def test_a_new_node_is_fenced_by_the_tree_in_its_body(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        """The one write on the vault router whose path names nothing."""
        await _second_person_on_atlas(signed_in, other_client, role="Reviewer")
        tree = (await signed_in.post("/projects/ATL/vault/trees", json={"name": "Logins"})).json()
        await _allow(signed_in, "Reviewer")

        response = await other_client.post(
            "/vault/nodes", json={"tree_id": tree["id"], "name": "Twilio", "kind": "branch"}
        )

        assert response.status_code == 403

    async def test_revealing_needs_the_permission_as_well_as_the_scope(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        """A session carries `vault:reveal`; the role still has to allow it."""
        await _second_person_on_atlas(signed_in, other_client, role="Reviewer")
        tree = (await signed_in.post("/projects/ATL/vault/trees", json={"name": "Logins"})).json()
        node = (
            await signed_in.post(
                "/vault/nodes",
                json={
                    "tree_id": tree["id"],
                    "name": "Twilio console",
                    "kind": "secret",
                    "secret": {"value": "Tw!9xLp3#Qm"},
                },
            )
        ).json()
        await _allow(signed_in, "Reviewer", Permission.VAULT)

        refused = await other_client.post(f"/vault/nodes/{node['id']}/reveal")

        assert refused.status_code == 403
        assert "reveal secrets" in refused.json()["error"]["message"]

    async def test_and_is_allowed_once_the_role_says_so(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _second_person_on_atlas(signed_in, other_client, role="Reviewer")
        tree = (await signed_in.post("/projects/ATL/vault/trees", json={"name": "Logins"})).json()
        node = (
            await signed_in.post(
                "/vault/nodes",
                json={
                    "tree_id": tree["id"],
                    "name": "Twilio console",
                    "kind": "secret",
                    "secret": {"value": "Tw!9xLp3#Qm"},
                },
            )
        ).json()
        await _allow(signed_in, "Reviewer", Permission.VAULT_REVEAL)

        revealed = await other_client.post(f"/vault/nodes/{node['id']}/reveal")

        assert revealed.status_code == 200, revealed.text
        assert revealed.json()["value"] == "Tw!9xLp3#Qm"


class TestEverybodyElse:
    async def test_somebody_with_no_role_falls_to_the_baseline(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _second_person_on_atlas(signed_in, other_client)
        await _allow_everyone_else(signed_in, Permission.COMMENTS)

        response = await other_client.post(
            "/projects/ATL/tasks", json=_card(await _member_id(signed_in, OWNER_NAME), "Migrate")
        )

        assert response.status_code == 403

    async def test_a_role_answers_instead_of_the_baseline_once_they_have_one(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        person_id = await _second_person_on_atlas(signed_in, other_client)
        await _allow_everyone_else(signed_in)
        await signed_in.post("/projects/ATL/roles", json={"name": "Reviewer"})
        await _allow(signed_in, "Reviewer", Permission.TASKS)

        await signed_in.put(f"/projects/ATL/members/{person_id}/role", json={"role": "Reviewer"})

        assert (
            await other_client.post(
                "/projects/ATL/tasks", json=_card(await _member_id(signed_in, OWNER_NAME), "A")
            )
        ).status_code == 201

    async def test_somebody_not_on_the_project_gets_the_same_answer(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        """Membership has never been a fence, and this did not make it one."""
        await signed_in.post("/projects", json=ATLAS)
        _, token = await open_account(signed_in, ADITI, ADITI_EMAIL)
        await other_client.post(
            "/auth/accept-invite", json={"token": token, "password": INVITEE_PASSWORD}
        )
        await _allow_everyone_else(signed_in, Permission.COMMENTS)

        response = await other_client.post(
            "/projects/ATL/tasks", json=_card(await _member_id(signed_in, OWNER_NAME), "Migrate")
        )

        assert response.status_code == 403


class TestBothGatesStillStand:
    async def test_a_read_only_token_is_refused_before_a_role_is_consulted(
        self, signed_in: AsyncClient
    ) -> None:
        """A scope says what a credential may do anywhere; the two are not
        interchangeable, and holding every permission is not holding `write`."""
        await signed_in.post("/projects", json=ATLAS)
        issued = await signed_in.post("/tokens", json={"name": "reader", "scopes": ["read"]})
        assert issued.status_code == 201, issued.text

        response = await signed_in.post(
            "/projects/ATL/tasks",
            json={"title": "Migrate"},
            headers={"Authorization": f"Bearer {issued.json()['token']}"},
        )

        assert response.status_code == 403
        assert response.json()["error"]["details"]["missing_scopes"] == ["write"]


class TestDeletingARole:
    async def test_its_grants_go_with_it(self, signed_in: AsyncClient) -> None:
        """By the cascade on their key to the role, rather than by the service
        remembering to — see `TestARoleTakesItsRestrictionsWithIt` in
        test_access.py for the two tables it once forgot."""
        await signed_in.post("/projects", json=ATLAS)
        await signed_in.post("/projects/ATL/roles", json={"name": "QA"})
        await _allow(signed_in, "QA", Permission.TASKS)

        deleted = await signed_in.delete("/projects/ATL/roles/QA")

        assert deleted.status_code == 204, deleted.text
        assert [
            role["name"]
            for role in (await signed_in.get("/projects/ATL/permissions")).json()["roles"]
        ] == [
            "Admin",
            "Everyone else",
        ]

    async def test_and_a_role_created_again_does_not_inherit_them(
        self, signed_in: AsyncClient
    ) -> None:
        await signed_in.post("/projects", json=ATLAS)
        await _allow_everyone_else(signed_in, Permission.COMMENTS)
        await signed_in.post("/projects/ATL/roles", json={"name": "QA"})
        await _allow(signed_in, "QA", Permission.TASKS, Permission.FILES)
        await signed_in.delete("/projects/ATL/roles/QA")

        await signed_in.post("/projects/ATL/roles", json={"name": "QA"})

        assert (await _line(signed_in, "QA"))["permissions"] == ["comments"]


class TestTheBootstrapSession:
    async def test_it_is_not_fenced(self, bootstrapped: AsyncClient) -> None:
        """It belongs to nobody, so there is no role to read — and it is what
        opens the deployment's first account."""
        await bootstrapped.post("/projects", json=ATLAS)
        person = (await bootstrapped.post("/people", json=ADITI)).json()["id"]
        await bootstrapped.put("/projects/ATL/members", json={"person_ids": [person]})
        await _allow_everyone_else(bootstrapped)

        created = await bootstrapped.post("/projects/ATL/tasks", json=_card(person))

        assert created.status_code == 201, created.text

    async def test_and_the_screen_knows_it_may_change_things(
        self, bootstrapped: AsyncClient
    ) -> None:
        """It is who sets a deployment's first project up, and it is on no
        member list anywhere — so nothing a client could read off one would
        have told it that."""
        await bootstrapped.post("/projects", json=ATLAS)

        grid = (await bootstrapped.get("/projects/ATL/permissions")).json()

        assert grid["may_manage"] is True
