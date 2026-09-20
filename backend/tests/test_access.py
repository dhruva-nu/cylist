"""Access that knows which column, which upload and which goal.

CYLIST-46's first cut fenced whole areas: may this role touch cards, the
vault, goals. These are the finer answers an admin actually wants to give —
not "may they move cards" but "may they move cards into Done", not "may they
read the vault" but "may they read *this*".

Two properties are worth stating because everything here rests on them. The
per-object layer is made of **restrictions**, so a board nobody has narrowed
behaves exactly as it did (:class:`TestNothingIsNarrowedByDefault`). And a
thing above your clearance is answered as though it were never there, never as
a refusal (:class:`TestAThingYouCannotReadIsNotThere`) — a 403 on
``redundancy-list-final.xlsx`` has already told you the interesting part.
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

SECRET_VALUE = "Tw!9xLp3#Qm"


async def _member_id(client: AsyncClient, name: str) -> str:
    members = (await client.get("/projects/ATL/members")).json()["members"]
    return str(next(member["id"] for member in members if member["name"] == name))


async def _reviewer_on_atlas(admin: AsyncClient, other: AsyncClient) -> str:
    """Aditi on her own browser, on ATL, wearing a Reviewer role.

    The role starts allowing everything, the way a new role does — every test
    below narrows exactly the one thing it is about, so a failure names it.
    """
    await admin.post("/projects", json=ATLAS)
    person, token = await open_account(admin, ADITI, ADITI_EMAIL)
    accepted = await other.post(
        "/auth/accept-invite", json={"token": token, "password": INVITEE_PASSWORD}
    )
    assert accepted.status_code == 200, accepted.text

    owner_id = await _member_id(admin, OWNER_NAME)
    await admin.put("/projects/ATL/members", json={"person_ids": [owner_id, person["id"]]})
    await admin.post("/projects/ATL/roles", json={"name": "Reviewer"})
    assigned = await admin.put(
        f"/projects/ATL/members/{person['id']}/role", json={"role": "Reviewer"}
    )
    assert assigned.status_code == 200, assigned.text
    return str(person["id"])


async def _board(client: AsyncClient) -> list[dict[str, Any]]:
    return list((await client.get("/projects/ATL/columns")).json()["columns"])


async def _column(client: AsyncClient, name: str) -> dict[str, Any]:
    return next(column for column in await _board(client) if column["name"] == name)


async def _line(client: AsyncClient, name: str) -> dict[str, Any]:
    grid = (await client.get("/projects/ATL/permissions")).json()
    return next(role for role in grid["roles"] if role["name"] == name)


async def _restrict_columns(
    admin: AsyncClient, role: str, rules: dict[str, tuple[bool, bool]]
) -> None:
    """Set a role's whole workflow line, naming columns the way a human does."""
    board = await _board(admin)
    payload = [
        {
            "column_id": column["id"],
            "may_enter": rules.get(column["name"], (True, True))[0],
            "may_stage": rules.get(column["name"], (True, True))[1],
        }
        for column in board
    ]
    response = await admin.put(f"/projects/ATL/roles/{role}/columns", json={"columns": payload})
    assert response.status_code == 200, response.text


async def _set_clearance(admin: AsyncClient, role: str | None, level: str) -> None:
    path = (
        "/projects/ATL/permissions/everyone-else/clearance"
        if role is None
        else f"/projects/ATL/roles/{role}/clearance"
    )
    response = await admin.put(path, json={"clearance": level})
    assert response.status_code == 200, response.text


def _card(assignee_id: str, **overrides: Any) -> dict[str, Any]:
    return {
        "title": "Stripe webhook idempotency",
        "description": "Duplicate deliveries create double payments. Dedupe on event id.",
        "type": "bug",
        "assignee_id": assignee_id,
        **overrides,
    }


async def _a_card(admin: AsyncClient, **overrides: Any) -> dict[str, Any]:
    owner_id = await _member_id(admin, OWNER_NAME)
    created = await admin.post("/projects/ATL/tasks", json=_card(owner_id, **overrides))
    assert created.status_code == 201, created.text
    return dict(created.json())


async def _root_folder(client: AsyncClient) -> str:
    return str((await client.get("/projects/ATL/tree")).json()["id"])


async def _link(admin: AsyncClient, folder_id: str, name: str, **overrides: Any) -> dict[str, Any]:
    created = await admin.post(
        f"/folders/{folder_id}/links",
        json={"name": name, "url": "https://example.test/doc", **overrides},
    )
    assert created.status_code == 201, created.text
    return dict(created.json())


class TestNothingIsNarrowedByDefault:
    """A board nobody has classified behaves exactly as it did before."""

    async def test_a_role_may_work_anywhere_on_the_board(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _reviewer_on_atlas(signed_in, other_client)
        card = await _a_card(signed_in)
        last = (await _board(signed_in))[-1]

        moved = await other_client.post(
            f"/tasks/{card['reference']}/move", json={"column_id": last["id"]}
        )

        assert moved.status_code == 200, moved.text

    async def test_the_grid_reports_every_column_as_open(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        await signed_in.post("/projects/ATL/roles", json={"name": "Reviewer"})

        line = await _line(signed_in, "Reviewer")

        assert [column["name"] for column in line["columns"]] == [
            column["name"] for column in await _board(signed_in)
        ]
        assert all(column["may_enter"] and column["may_stage"] for column in line["columns"])

    async def test_a_role_is_cleared_for_everything(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        await signed_in.post("/projects/ATL/roles", json={"name": "Reviewer"})

        assert (await _line(signed_in, "Reviewer"))["clearance"] == "restricted"

    async def test_an_upload_is_internal(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        root = await _root_folder(signed_in)

        assert (await _link(signed_in, root, "Statement of work"))["sensitivity"] == "internal"


class TestWhereOnTheBoardARoleMayWork:
    async def test_a_card_cannot_be_moved_into_a_column_the_role_is_kept_out_of(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _reviewer_on_atlas(signed_in, other_client)
        card = await _a_card(signed_in)
        done = (await _board(signed_in))[-1]
        await _restrict_columns(signed_in, "Reviewer", {done["name"]: (False, True)})

        moved = await other_client.post(
            f"/tasks/{card['reference']}/move", json={"column_id": done["id"]}
        )

        assert moved.status_code == 403
        assert moved.json()["error"]["message"] == (
            f"Your role on ATL does not let you move cards into {done['name']}."
        )

    async def test_and_can_still_be_moved_everywhere_else(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        """The whole reason the column rules are not a longer flat list."""
        await _reviewer_on_atlas(signed_in, other_client)
        await signed_in.post(
            "/projects/ATL/columns", json={"name": "Review", "description": "Waiting on a look."}
        )
        card = await _a_card(signed_in)
        await _restrict_columns(signed_in, "Reviewer", {"Done": (False, True)})

        moved = await other_client.post(
            f"/tasks/{card['reference']}/move",
            json={"column_id": (await _column(other_client, "Review"))["id"]},
        )

        assert moved.status_code == 200, moved.text

    async def test_a_card_s_own_stages_need_the_staging_right(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _reviewer_on_atlas(signed_in, other_client)
        board = await _board(signed_in)
        card = await _a_card(signed_in, sub_statuses=["Drafted", "Reviewed"])
        await _restrict_columns(signed_in, "Reviewer", {board[0]["name"]: (True, False)})

        response = await other_client.post(
            f"/tasks/{card['reference']}/sub-status", json={"index": 1}
        )

        assert response.status_code == 403
        assert "sub-stages" in response.json()["error"]["message"]

    async def test_nor_can_a_new_card_be_given_stages_there(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        """Checked against the column the card will land in, not the first."""
        person_id = await _reviewer_on_atlas(signed_in, other_client)
        board = await _board(signed_in)
        await _restrict_columns(signed_in, "Reviewer", {board[0]["name"]: (True, False)})

        response = await other_client.post(
            "/projects/ATL/tasks", json=_card(person_id, sub_statuses=["Drafted"])
        )

        assert response.status_code == 403

    async def test_but_a_card_with_no_stages_is_fine(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        person_id = await _reviewer_on_atlas(signed_in, other_client)
        board = await _board(signed_in)
        await _restrict_columns(signed_in, "Reviewer", {board[0]["name"]: (True, False)})

        response = await other_client.post("/projects/ATL/tasks", json=_card(person_id))

        assert response.status_code == 201, response.text

    async def test_an_edit_that_sets_stages_is_refused(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _reviewer_on_atlas(signed_in, other_client)
        board = await _board(signed_in)
        card = await _a_card(signed_in)
        await _restrict_columns(signed_in, "Reviewer", {board[0]["name"]: (True, False)})

        response = await other_client.patch(
            f"/tasks/{card['reference']}", json={"sub_statuses": ["Drafted", "Reviewed"]}
        )

        assert response.status_code == 403

    async def test_an_edit_that_does_not_is_allowed(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        """Only the field that answers to it is asked about."""
        await _reviewer_on_atlas(signed_in, other_client)
        board = await _board(signed_in)
        card = await _a_card(signed_in)
        await _restrict_columns(signed_in, "Reviewer", {board[0]["name"]: (True, False)})

        response = await other_client.patch(
            f"/tasks/{card['reference']}", json={"title": "Renamed"}
        )

        assert response.status_code == 200, response.text

    async def test_a_template_stage_is_the_same_right(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        """A template's stage *is* the sub-stage policy for one column."""
        await _reviewer_on_atlas(signed_in, other_client)
        board = await _board(signed_in)
        await _restrict_columns(signed_in, "Reviewer", {board[1]["name"]: (True, False)})

        response = await other_client.post(
            "/projects/ATL/templates",
            json={
                "name": "Hotfix",
                "stages": [{"column_id": board[1]["id"], "sub_stage_labels": ["Drafted"]}],
            },
        )

        assert response.status_code == 403

    async def test_a_column_added_later_is_open(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        """The reason the table holds restrictions rather than grants: a
        grant-shaped one would lock every role out of a new column."""
        await _reviewer_on_atlas(signed_in, other_client)
        board = await _board(signed_in)
        await _restrict_columns(signed_in, "Reviewer", {board[-1]["name"]: (False, True)})
        added = await signed_in.post(
            "/projects/ATL/columns", json={"name": "Blocked", "description": "Stuck cards."}
        )
        card = await _a_card(signed_in)

        moved = await other_client.post(
            f"/tasks/{card['reference']}/move", json={"column_id": added.json()["id"]}
        )

        assert moved.status_code == 200, moved.text

    async def test_an_admin_works_anywhere(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _reviewer_on_atlas(signed_in, other_client)
        card = await _a_card(signed_in)
        done = (await _board(signed_in))[-1]
        await _restrict_columns(signed_in, "Reviewer", {done["name"]: (False, False)})

        moved = await signed_in.post(
            f"/tasks/{card['reference']}/move", json={"column_id": done["id"]}
        )

        assert moved.status_code == 200, moved.text

    async def test_the_admin_role_s_line_is_not_configurable(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        board = await _board(signed_in)

        response = await signed_in.put(
            "/projects/ATL/roles/Admin/columns",
            json={"columns": [{"column_id": board[0]["id"], "may_enter": False}]},
        )

        assert response.status_code == 422

    async def test_a_column_from_another_board_is_refused(self, signed_in: AsyncClient) -> None:
        """The foreign key would refuse a column that does not exist and say
        nothing about one that is somebody else's."""
        await signed_in.post("/projects", json=ATLAS)
        await signed_in.post("/projects", json={"key": "HRM", "name": "Hermes"})
        await signed_in.post("/projects/ATL/roles", json={"name": "Reviewer"})
        theirs = (await signed_in.get("/projects/HRM/columns")).json()["columns"][0]

        response = await signed_in.put(
            "/projects/ATL/roles/Reviewer/columns",
            json={"columns": [{"column_id": theirs["id"], "may_enter": False}]},
        )

        assert response.status_code == 422
        assert "not on ATL's board" in response.json()["error"]["message"]

    async def test_only_an_admin_may_set_the_line(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _reviewer_on_atlas(signed_in, other_client)

        response = await other_client.put(
            "/projects/ATL/roles/Reviewer/columns", json={"columns": []}
        )

        assert response.status_code == 403

    async def test_the_flat_permission_is_still_the_gate(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        """A role that may not touch cards is not asked about columns."""
        await _reviewer_on_atlas(signed_in, other_client)
        card = await _a_card(signed_in)
        await signed_in.put("/projects/ATL/roles/Reviewer/permissions", json={"permissions": []})
        board = await _board(signed_in)

        moved = await other_client.post(
            f"/tasks/{card['reference']}/move", json={"column_id": board[1]["id"]}
        )

        assert moved.status_code == 403
        assert moved.json()["error"]["message"] == "Your role on ATL does not let you change cards."


class TestAThingYouCannotReadIsNotThere:
    async def test_a_restricted_file_is_left_out_of_the_listing(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _reviewer_on_atlas(signed_in, other_client)
        root = await _root_folder(signed_in)
        await _link(signed_in, root, "Statement of work")
        await _link(signed_in, root, "Redundancy list", sensitivity="restricted")
        await _set_clearance(signed_in, "Reviewer", "internal")

        listed = (await other_client.get(f"/folders/{root}/children")).json()["items"]

        assert [item["name"] for item in listed] == ["Statement of work"]

    async def test_nor_is_it_fetchable_by_id(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _reviewer_on_atlas(signed_in, other_client)
        root = await _root_folder(signed_in)
        hidden = await _link(signed_in, root, "Redundancy list", sensitivity="restricted")
        await _set_clearance(signed_in, "Reviewer", "internal")

        response = await other_client.get(f"/items/{hidden['id']}")

        assert response.status_code == 404

    async def test_nor_renameable(self, signed_in: AsyncClient, other_client: AsyncClient) -> None:
        """A role that cannot be shown a file has no business renaming it."""
        await _reviewer_on_atlas(signed_in, other_client)
        root = await _root_folder(signed_in)
        hidden = await _link(signed_in, root, "Redundancy list", sensitivity="restricted")
        await _set_clearance(signed_in, "Reviewer", "internal")

        response = await other_client.patch(f"/items/{hidden['id']}", json={"name": "x"})

        assert response.status_code == 404

    async def test_nor_in_the_project_wide_list(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        """The flat listing the `>` tag completes from, which would otherwise
        be the way round the tree."""
        await _reviewer_on_atlas(signed_in, other_client)
        root = await _root_folder(signed_in)
        await _link(signed_in, root, "Redundancy list", sensitivity="restricted")
        await _set_clearance(signed_in, "Reviewer", "internal")

        listed = (await other_client.get("/projects/ATL/items")).json()

        assert [item["name"] for item in listed] == []

    async def test_raising_the_clearance_shows_it(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _reviewer_on_atlas(signed_in, other_client)
        root = await _root_folder(signed_in)
        hidden = await _link(signed_in, root, "Redundancy list", sensitivity="restricted")
        await _set_clearance(signed_in, "Reviewer", "internal")
        await _set_clearance(signed_in, "Reviewer", "restricted")

        assert (await other_client.get(f"/items/{hidden['id']}")).status_code == 200

    async def test_an_admin_reads_everything(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _reviewer_on_atlas(signed_in, other_client)
        root = await _root_folder(signed_in)
        hidden = await _link(signed_in, root, "Redundancy list", sensitivity="restricted")
        await _set_clearance(signed_in, "Reviewer", "public")

        assert (await signed_in.get(f"/items/{hidden['id']}")).status_code == 200

    async def test_a_folder_hands_its_default_to_what_is_put_in_it(
        self, signed_in: AsyncClient
    ) -> None:
        """So a folder of contracts stays restricted without anybody
        remembering to say so on each upload."""
        await signed_in.post("/projects", json=ATLAS)
        folder = (
            await signed_in.post(
                "/projects/ATL/folders",
                json={"name": "Contracts", "default_sensitivity": "restricted"},
            )
        ).json()

        added = await _link(signed_in, folder["id"], "Master agreement")

        assert added["sensitivity"] == "restricted"

    async def test_a_subfolder_inherits_it_too(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        parent = (
            await signed_in.post(
                "/projects/ATL/folders",
                json={"name": "Contracts", "default_sensitivity": "restricted"},
            )
        ).json()

        child = await signed_in.post(
            "/projects/ATL/folders", json={"name": "2026", "parent_id": parent["id"]}
        )

        assert child.json()["default_sensitivity"] == "restricted"

    async def test_the_project_root_can_carry_one(self, signed_in: AsyncClient) -> None:
        """The one edit the root accepts: it is where most files land, so a
        project that wanted everything restricted could not say it anywhere
        else."""
        await signed_in.post("/projects", json=ATLAS)
        root = await _root_folder(signed_in)

        response = await signed_in.patch(
            f"/folders/{root}", json={"default_sensitivity": "restricted"}
        )

        assert response.status_code == 200, response.text
        assert response.json()["default_sensitivity"] == "restricted"

    async def test_the_root_still_refuses_a_rename(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        root = await _root_folder(signed_in)

        assert (await signed_in.patch(f"/folders/{root}", json={"name": "x"})).status_code == 422


class TestTheVaultReadsTheSameWay:
    async def test_a_restricted_branch_takes_its_subtree_with_it(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        """A tree showing "Production" standing empty would have told the
        reader which branch is the interesting one."""
        await _reviewer_on_atlas(signed_in, other_client)
        tree = (await signed_in.post("/projects/ATL/vault/trees", json={"name": "Logins"})).json()
        branch = (
            await signed_in.post(
                "/vault/nodes",
                json={
                    "tree_id": tree["id"],
                    "name": "Production",
                    "kind": "branch",
                    "sensitivity": "restricted",
                },
            )
        ).json()
        inside = (
            await signed_in.post(
                "/vault/nodes",
                json={
                    "tree_id": tree["id"],
                    "parent_id": branch["id"],
                    "name": "Stripe",
                    "kind": "secret",
                    "secret": {"value": SECRET_VALUE},
                    "sensitivity": "public",
                },
            )
        ).json()
        await _set_clearance(signed_in, "Reviewer", "internal")

        seen = (await other_client.get(f"/vault/trees/{tree['id']}")).json()

        assert [node["name"] for node in seen["nodes"]] == []
        assert (await other_client.get(f"/vault/nodes/{inside['id']}")).status_code == 404

    async def test_a_node_inherits_its_branch_s_level(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        tree = (await signed_in.post("/projects/ATL/vault/trees", json={"name": "Logins"})).json()
        branch = (
            await signed_in.post(
                "/vault/nodes",
                json={
                    "tree_id": tree["id"],
                    "name": "Production",
                    "kind": "branch",
                    "sensitivity": "restricted",
                },
            )
        ).json()

        child = await signed_in.post(
            "/vault/nodes",
            json={"tree_id": tree["id"], "parent_id": branch["id"], "name": "DB", "kind": "branch"},
        )

        assert child.json()["sensitivity"] == "restricted"

    async def test_a_tree_hands_its_default_to_the_top_level(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        tree = (
            await signed_in.post(
                "/projects/ATL/vault/trees",
                json={"name": "Certificates", "default_sensitivity": "restricted"},
            )
        ).json()

        node = await signed_in.post(
            "/vault/nodes", json={"tree_id": tree["id"], "name": "Root CA", "kind": "branch"}
        )

        assert node.json()["sensitivity"] == "restricted"

    async def test_revealing_a_secret_you_cannot_see_is_a_404(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        """Holding `vault_reveal` is not being told the secret exists."""
        await _reviewer_on_atlas(signed_in, other_client)
        tree = (await signed_in.post("/projects/ATL/vault/trees", json={"name": "Logins"})).json()
        node = (
            await signed_in.post(
                "/vault/nodes",
                json={
                    "tree_id": tree["id"],
                    "name": "Twilio",
                    "kind": "secret",
                    "secret": {"value": SECRET_VALUE},
                    "sensitivity": "restricted",
                },
            )
        ).json()
        await _set_clearance(signed_in, "Reviewer", "internal")

        response = await other_client.post(f"/vault/nodes/{node['id']}/reveal")

        assert response.status_code == 404


class TestTheThreeGoalRights:
    async def test_putting_a_card_on_a_goal_is_its_own_right(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _reviewer_on_atlas(signed_in, other_client)
        owner_id = await _member_id(signed_in, OWNER_NAME)
        goal = (
            await signed_in.post(
                "/projects/ATL/goals", json={"name": "Ledger cutover", "owner_id": owner_id}
            )
        ).json()
        card = await _a_card(signed_in)
        await signed_in.put(
            "/projects/ATL/roles/Reviewer/permissions",
            json={"permissions": ["tasks", "goals"]},
        )

        response = await other_client.patch(
            f"/tasks/{card['reference']}", json={"goal_id": goal["id"]}
        )

        assert response.status_code == 403
        assert "which goal a card is on" in response.json()["error"]["message"]

    async def test_and_is_allowed_with_it(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        """Without `goals` at all — the daily act does not need the plan."""
        await _reviewer_on_atlas(signed_in, other_client)
        owner_id = await _member_id(signed_in, OWNER_NAME)
        goal = (
            await signed_in.post(
                "/projects/ATL/goals", json={"name": "Ledger cutover", "owner_id": owner_id}
            )
        ).json()
        card = await _a_card(signed_in)
        await signed_in.put(
            "/projects/ATL/roles/Reviewer/permissions",
            json={"permissions": ["tasks", "goal_assign"]},
        )

        response = await other_client.patch(
            f"/tasks/{card['reference']}", json={"goal_id": goal["id"]}
        )

        assert response.status_code == 200, response.text

    async def test_handing_a_goal_to_somebody_else_is_its_own_right(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        person_id = await _reviewer_on_atlas(signed_in, other_client)
        owner_id = await _member_id(signed_in, OWNER_NAME)
        goal = (
            await signed_in.post(
                "/projects/ATL/goals", json={"name": "Ledger cutover", "owner_id": owner_id}
            )
        ).json()
        await signed_in.put(
            "/projects/ATL/roles/Reviewer/permissions", json={"permissions": ["goals"]}
        )

        response = await other_client.patch(
            f"/goals/{goal['reference']}", json={"owner_id": person_id}
        )

        assert response.status_code == 403
        assert "whose goal" in response.json()["error"]["message"]

    async def test_while_the_rest_of_the_goal_needs_only_goals(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _reviewer_on_atlas(signed_in, other_client)
        owner_id = await _member_id(signed_in, OWNER_NAME)
        goal = (
            await signed_in.post(
                "/projects/ATL/goals", json={"name": "Ledger cutover", "owner_id": owner_id}
            )
        ).json()
        await signed_in.put(
            "/projects/ATL/roles/Reviewer/permissions", json={"permissions": ["goals"]}
        )

        response = await other_client.patch(
            f"/goals/{goal['reference']}", json={"name": "Ledger cutover, phase 2"}
        )

        assert response.status_code == 200, response.text

    async def test_sending_the_same_owner_back_is_not_a_reassignment(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        """A client that PUTs the whole goal back unchanged has not handed it
        to anybody."""
        await _reviewer_on_atlas(signed_in, other_client)
        owner_id = await _member_id(signed_in, OWNER_NAME)
        goal = (
            await signed_in.post(
                "/projects/ATL/goals", json={"name": "Ledger cutover", "owner_id": owner_id}
            )
        ).json()
        await signed_in.put(
            "/projects/ATL/roles/Reviewer/permissions", json={"permissions": ["goals"]}
        )

        response = await other_client.patch(
            f"/goals/{goal['reference']}", json={"name": "Renamed", "owner_id": owner_id}
        )

        assert response.status_code == 200, response.text

    async def test_creating_a_goal_names_its_owner_and_needs_only_goals(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        person_id = await _reviewer_on_atlas(signed_in, other_client)
        await signed_in.put(
            "/projects/ATL/roles/Reviewer/permissions", json={"permissions": ["goals"]}
        )

        response = await other_client.post(
            "/projects/ATL/goals", json={"name": "Search revamp", "owner_id": person_id}
        )

        assert response.status_code == 201, response.text

    async def test_all_three_are_on_the_grid(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)

        grid = (await signed_in.get("/projects/ATL/permissions")).json()

        keys = [entry["key"] for entry in grid["catalogue"]]
        assert Permission.GOALS in keys
        assert Permission.GOAL_ASSIGN in keys
        assert Permission.GOAL_OWNER in keys


class TestTheCountsAgreeWithTheListings:
    """A count is a listing summed up. One that disagreed would be the leak."""

    async def test_the_hub_counts_only_what_the_reader_can_open(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _reviewer_on_atlas(signed_in, other_client)
        root = await _root_folder(signed_in)
        await _link(signed_in, root, "Statement of work")
        await _link(signed_in, root, "Redundancy list", sensitivity="restricted")
        await _set_clearance(signed_in, "Reviewer", "internal")

        theirs = (await other_client.get("/projects/ATL/summary")).json()
        mine = (await signed_in.get("/projects/ATL/summary")).json()

        assert theirs["file_count"] == 1
        assert mine["file_count"] == 2

    async def test_and_the_secret_count_does_the_same(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _reviewer_on_atlas(signed_in, other_client)
        tree = (await signed_in.post("/projects/ATL/vault/trees", json={"name": "Logins"})).json()
        for name, level in (("Twilio", "internal"), ("Root key", "restricted")):
            await signed_in.post(
                "/vault/nodes",
                json={
                    "tree_id": tree["id"],
                    "name": name,
                    "kind": "secret",
                    "secret": {"value": SECRET_VALUE},
                    "sensitivity": level,
                },
            )
        await _set_clearance(signed_in, "Reviewer", "internal")

        theirs = (await other_client.get("/projects/ATL/summary")).json()

        assert theirs["vault_secret_count"] == 1
