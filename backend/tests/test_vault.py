"""The vault over HTTP: trees, nodes, and the one endpoint that reveals.

The tests worth reading twice are :class:`TestSecretsNeverLeak` and
:class:`TestScopes`. Everything else describes a tree widget; those two are
the reason the phase exists.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import Database
from app.main import create_app
from app.models import VaultNode, VaultSecret, VaultTree
from tests.conftest import OWNER_PASSWORD

ATLAS = {"key": "ATL", "name": "Atlas Billing Migration"}
TWILIO_SECRET = "Tw!9xLp3#Qm"
UNKNOWN_ID = "00000000-0000-7000-8000-000000000000"


async def _tree(client: AsyncClient, name: str = "Logins") -> str:
    await client.post("/projects", json=ATLAS)
    created = await client.post("/projects/ATL/vault/trees", json={"name": name})
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


async def _branch(client: AsyncClient, tree_id: str, name: str, parent: str | None = None) -> str:
    response = await client.post(
        "/vault/nodes",
        json={"tree_id": tree_id, "parent_id": parent, "name": name, "kind": "branch"},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def _secret(
    client: AsyncClient,
    tree_id: str,
    name: str = "Twilio console",
    parent: str | None = None,
    **secret: Any,
) -> dict[str, Any]:
    response = await client.post(
        "/vault/nodes",
        json={
            "tree_id": tree_id,
            "parent_id": parent,
            "name": name,
            "kind": "secret",
            "secret": {"value": TWILIO_SECRET, **secret},
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


class TestTrees:
    async def test_a_project_starts_with_no_trees(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)

        assert (await signed_in.get("/projects/ATL/vault/trees")).json() == []

    async def test_creates_a_tree(self, signed_in: AsyncClient) -> None:
        response = await signed_in.post("/projects", json=ATLAS)
        assert response.status_code == 201

        created = await signed_in.post("/projects/ATL/vault/trees", json={"name": "Logins"})

        assert created.status_code == 201
        assert created.json()["name"] == "Logins"
        assert created.json()["node_count"] == 0
        assert created.json()["secret_count"] == 0

    async def test_a_tree_can_be_reached_through_the_project_key(
        self, signed_in: AsyncClient
    ) -> None:
        """So an agent can call /projects/ATL/vault/trees without a UUID first."""
        await _tree(signed_in)

        by_key = await signed_in.get("/projects/atl/vault/trees")

        assert by_key.status_code == 200
        assert [tree["name"] for tree in by_key.json()] == ["Logins"]

    async def test_refuses_two_trees_with_the_same_name(self, signed_in: AsyncClient) -> None:
        await _tree(signed_in)

        response = await signed_in.post("/projects/ATL/vault/trees", json={"name": "Logins"})

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"

    async def test_trees_come_back_in_the_order_they_were_made(
        self, signed_in: AsyncClient
    ) -> None:
        await _tree(signed_in, "Logins")
        await signed_in.post("/projects/ATL/vault/trees", json={"name": "Links"})
        await signed_in.post("/projects/ATL/vault/trees", json={"name": "Certificates"})

        listed = (await signed_in.get("/projects/ATL/vault/trees")).json()

        assert [tree["name"] for tree in listed] == ["Logins", "Links", "Certificates"]
        assert [tree["position"] for tree in listed] == [0, 1, 2]

    async def test_a_tree_can_be_renamed(self, signed_in: AsyncClient) -> None:
        tree_id = await _tree(signed_in)

        renamed = await signed_in.patch(f"/vault/trees/{tree_id}", json={"name": "Accounts"})

        assert renamed.json()["name"] == "Accounts"

    async def test_renaming_onto_a_taken_name_is_a_conflict(self, signed_in: AsyncClient) -> None:
        tree_id = await _tree(signed_in, "Logins")
        await signed_in.post("/projects/ATL/vault/trees", json={"name": "Links"})

        response = await signed_in.patch(f"/vault/trees/{tree_id}", json={"name": "Links"})

        assert response.status_code == 409

    async def test_deleting_a_tree_takes_its_nodes_with_it(
        self, signed_in: AsyncClient, session: AsyncSession
    ) -> None:
        tree_id = await _tree(signed_in)
        branch = await _branch(signed_in, tree_id, "Messaging")
        await _secret(signed_in, tree_id, parent=branch)

        assert (await signed_in.delete(f"/vault/trees/{tree_id}")).status_code == 200

        assert list(await session.scalars(select(VaultNode))) == []
        assert list(await session.scalars(select(VaultSecret))) == []

    async def test_an_unknown_tree_is_a_clean_404(self, signed_in: AsyncClient) -> None:
        response = await signed_in.get(f"/vault/trees/{UNKNOWN_ID}")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


class TestNodes:
    async def test_creates_a_branch(self, signed_in: AsyncClient) -> None:
        tree_id = await _tree(signed_in)

        response = await signed_in.post(
            "/vault/nodes", json={"tree_id": tree_id, "name": "Messaging", "kind": "branch"}
        )

        assert response.status_code == 201
        assert response.json()["kind"] == "branch"
        assert response.json()["secret"] is None

    async def test_creates_a_secret(self, signed_in: AsyncClient) -> None:
        tree_id = await _tree(signed_in)

        node = await _secret(signed_in, tree_id, username="ops@think41.com", notes="Rotate.")

        assert node["kind"] == "secret"
        assert node["secret"]["username"] == "ops@think41.com"
        assert node["secret"]["notes"] == "Rotate."
        assert node["secret"]["key_version"] == 1

    async def test_nests_to_arbitrary_depth(self, signed_in: AsyncClient) -> None:
        tree_id = await _tree(signed_in)
        node_id: str | None = None
        for level in range(6):
            node_id = await _branch(signed_in, tree_id, f"level {level}", node_id)
        await _secret(signed_in, tree_id, parent=node_id)

        tree = (await signed_in.get(f"/vault/trees/{tree_id}")).json()

        depth, current = 0, tree["nodes"]
        while current:
            depth += 1
            current = current[0]["children"]
        assert depth == 7
        assert tree["node_count"] == 7
        assert tree["secret_count"] == 1

    async def test_a_branch_cannot_be_given_a_secret(self, signed_in: AsyncClient) -> None:
        tree_id = await _tree(signed_in)

        response = await signed_in.post(
            "/vault/nodes",
            json={
                "tree_id": tree_id,
                "name": "Messaging",
                "kind": "branch",
                "secret": {"value": TWILIO_SECRET},
            },
        )

        assert response.status_code == 422

    async def test_a_secret_node_must_come_with_one(self, signed_in: AsyncClient) -> None:
        tree_id = await _tree(signed_in)

        response = await signed_in.post(
            "/vault/nodes", json={"tree_id": tree_id, "name": "Twilio", "kind": "secret"}
        )

        assert response.status_code == 422

    async def test_a_secret_cannot_be_a_parent(self, signed_in: AsyncClient) -> None:
        """A secret holds a credential; nothing hangs beneath it."""
        tree_id = await _tree(signed_in)
        leaf = await _secret(signed_in, tree_id)

        response = await signed_in.post(
            "/vault/nodes",
            json={
                "tree_id": tree_id,
                "parent_id": leaf["id"],
                "name": "Nested",
                "kind": "branch",
            },
        )

        assert response.status_code == 422

    async def test_a_parent_in_another_tree_is_refused(self, signed_in: AsyncClient) -> None:
        first = await _tree(signed_in, "Logins")
        second = (await signed_in.post("/projects/ATL/vault/trees", json={"name": "Links"})).json()[
            "id"
        ]
        branch = await _branch(signed_in, first, "Messaging")

        response = await signed_in.post(
            "/vault/nodes",
            json={"tree_id": second, "parent_id": branch, "name": "Odd", "kind": "branch"},
        )

        assert response.status_code == 422

    async def test_a_node_can_be_renamed(self, signed_in: AsyncClient) -> None:
        tree_id = await _tree(signed_in)
        node = await _secret(signed_in, tree_id)

        renamed = await signed_in.patch(f"/vault/nodes/{node['id']}", json={"name": "Twilio prod"})

        assert renamed.json()["name"] == "Twilio prod"

    async def test_deleting_a_branch_removes_its_whole_subtree(
        self, signed_in: AsyncClient, session: AsyncSession
    ) -> None:
        tree_id = await _tree(signed_in)
        branch = await _branch(signed_in, tree_id, "Messaging")
        inner = await _branch(signed_in, tree_id, "Twilio", branch)
        await _secret(signed_in, tree_id, parent=inner)

        assert (await signed_in.delete(f"/vault/nodes/{branch}")).status_code == 200

        assert list(await session.scalars(select(VaultNode))) == []
        assert list(await session.scalars(select(VaultSecret))) == []

    async def test_an_unknown_node_is_a_clean_404(self, signed_in: AsyncClient) -> None:
        assert (await signed_in.get(f"/vault/nodes/{UNKNOWN_ID}")).status_code == 404


class TestSiblingNames:
    async def test_two_siblings_cannot_share_a_name(self, signed_in: AsyncClient) -> None:
        tree_id = await _tree(signed_in)
        branch = await _branch(signed_in, tree_id, "Messaging")

        response = await signed_in.post(
            "/vault/nodes",
            json={"tree_id": tree_id, "parent_id": branch, "name": "Twilio", "kind": "branch"},
        )
        assert response.status_code == 201

        duplicate = await signed_in.post(
            "/vault/nodes",
            json={"tree_id": tree_id, "parent_id": branch, "name": "Twilio", "kind": "branch"},
        )

        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["details"]["name"] == "Twilio"

    async def test_top_level_siblings_are_covered_too(self, signed_in: AsyncClient) -> None:
        """A NULL parent_id is where a plain UNIQUE would silently let two
        through, so this is the case worth pinning down."""
        tree_id = await _tree(signed_in)
        await _branch(signed_in, tree_id, "Messaging")

        response = await signed_in.post(
            "/vault/nodes", json={"tree_id": tree_id, "name": "Messaging", "kind": "branch"}
        )

        assert response.status_code == 409

    async def test_the_same_name_under_different_parents_is_fine(
        self, signed_in: AsyncClient
    ) -> None:
        tree_id = await _tree(signed_in)
        first = await _branch(signed_in, tree_id, "Staging")
        second = await _branch(signed_in, tree_id, "Production")

        await _secret(signed_in, tree_id, "Twilio", parent=first)
        response = await signed_in.post(
            "/vault/nodes",
            json={
                "tree_id": tree_id,
                "parent_id": second,
                "name": "Twilio",
                "kind": "secret",
                "secret": {"value": TWILIO_SECRET},
            },
        )

        assert response.status_code == 201

    async def test_renaming_onto_a_sibling_is_a_conflict(self, signed_in: AsyncClient) -> None:
        tree_id = await _tree(signed_in)
        await _branch(signed_in, tree_id, "Staging")
        second = await _branch(signed_in, tree_id, "Production")

        response = await signed_in.patch(f"/vault/nodes/{second}", json={"name": "Staging"})

        assert response.status_code == 409

    async def test_renaming_a_node_to_its_own_name_is_not_a_conflict(
        self, signed_in: AsyncClient
    ) -> None:
        tree_id = await _tree(signed_in)
        branch = await _branch(signed_in, tree_id, "Staging")

        assert (
            await signed_in.patch(f"/vault/nodes/{branch}", json={"name": "Staging"})
        ).status_code == 200

    async def test_the_database_refuses_a_duplicate_the_service_never_saw(
        self, signed_in: AsyncClient, session: AsyncSession
    ) -> None:
        """The service checks first for a readable message; the constraint is
        what makes the rule true regardless of who is writing."""
        tree_id = await _tree(signed_in)
        await _branch(signed_in, tree_id, "Messaging")

        session.add(VaultNode(tree_id=UUID(tree_id), name="Messaging", kind="branch", position=9))

        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
        else:  # pragma: no cover - only if the constraint went missing
            raise AssertionError("the unique constraint did not fire")


class TestMoving:
    async def test_moves_a_node_under_a_new_parent(self, signed_in: AsyncClient) -> None:
        tree_id = await _tree(signed_in)
        branch = await _branch(signed_in, tree_id, "Messaging")
        node = await _secret(signed_in, tree_id)

        moved = await signed_in.post(
            f"/vault/nodes/{node['id']}/move", json={"parent_id": branch, "position": 0}
        )

        assert moved.status_code == 200
        assert moved.json()["parent_id"] == branch
        assert moved.json()["position"] == 0

    async def test_moves_a_node_back_to_the_top_level(self, signed_in: AsyncClient) -> None:
        tree_id = await _tree(signed_in)
        branch = await _branch(signed_in, tree_id, "Messaging")
        node = await _secret(signed_in, tree_id, parent=branch)

        moved = await signed_in.post(
            f"/vault/nodes/{node['id']}/move", json={"parent_id": None, "position": 0}
        )

        assert moved.json()["parent_id"] is None

    async def test_reorders_within_one_parent(self, signed_in: AsyncClient) -> None:
        tree_id = await _tree(signed_in)
        for name in ("first", "second", "third"):
            await _branch(signed_in, tree_id, name)
        third = (await signed_in.get(f"/vault/trees/{tree_id}")).json()["nodes"][2]["id"]

        await signed_in.post(f"/vault/nodes/{third}/move", json={"position": 0})

        tree = (await signed_in.get(f"/vault/trees/{tree_id}")).json()
        assert [node["name"] for node in tree["nodes"]] == ["third", "first", "second"]
        assert [node["position"] for node in tree["nodes"]] == [0, 1, 2]

    async def test_a_position_past_the_end_lands_last(self, signed_in: AsyncClient) -> None:
        tree_id = await _tree(signed_in)
        for name in ("first", "second", "third"):
            await _branch(signed_in, tree_id, name)
        first = (await signed_in.get(f"/vault/trees/{tree_id}")).json()["nodes"][0]["id"]

        await signed_in.post(f"/vault/nodes/{first}/move", json={"position": 99})

        tree = (await signed_in.get(f"/vault/trees/{tree_id}")).json()
        assert [node["name"] for node in tree["nodes"]] == ["second", "third", "first"]

    async def test_the_old_parent_is_renumbered(self, signed_in: AsyncClient) -> None:
        tree_id = await _tree(signed_in)
        origin = await _branch(signed_in, tree_id, "Origin")
        destination = await _branch(signed_in, tree_id, "Destination")
        for name in ("a", "b", "c"):
            await _branch(signed_in, tree_id, name, origin)
        middle = (await signed_in.get(f"/vault/nodes/{origin}")).json()["children"][1]["id"]

        await signed_in.post(
            f"/vault/nodes/{middle}/move", json={"parent_id": destination, "position": 0}
        )

        left = (await signed_in.get(f"/vault/nodes/{origin}")).json()["children"]
        assert [node["name"] for node in left] == ["a", "c"]
        assert [node["position"] for node in left] == [0, 1]

    async def test_a_node_cannot_be_moved_into_its_own_subtree(
        self, signed_in: AsyncClient
    ) -> None:
        """Allowing it would cut the branch, and everything under it, adrift
        from the tree — the rows would still exist and never be listed again."""
        tree_id = await _tree(signed_in)
        outer = await _branch(signed_in, tree_id, "Outer")
        middle = await _branch(signed_in, tree_id, "Middle", outer)
        inner = await _branch(signed_in, tree_id, "Inner", middle)

        response = await signed_in.post(
            f"/vault/nodes/{outer}/move", json={"parent_id": inner, "position": 0}
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "unprocessable"
        assert response.json()["error"]["details"]["node_id"] == outer

    async def test_a_node_cannot_be_moved_into_its_direct_child(
        self, signed_in: AsyncClient
    ) -> None:
        tree_id = await _tree(signed_in)
        outer = await _branch(signed_in, tree_id, "Outer")
        child = await _branch(signed_in, tree_id, "Child", outer)

        response = await signed_in.post(
            f"/vault/nodes/{outer}/move", json={"parent_id": child, "position": 0}
        )

        assert response.status_code == 422

    async def test_a_node_cannot_be_its_own_parent(self, signed_in: AsyncClient) -> None:
        tree_id = await _tree(signed_in)
        branch = await _branch(signed_in, tree_id, "Outer")

        response = await signed_in.post(
            f"/vault/nodes/{branch}/move", json={"parent_id": branch, "position": 0}
        )

        assert response.status_code == 422

    async def test_a_rejected_move_leaves_the_tree_alone(self, signed_in: AsyncClient) -> None:
        tree_id = await _tree(signed_in)
        outer = await _branch(signed_in, tree_id, "Outer")
        inner = await _branch(signed_in, tree_id, "Inner", outer)

        await signed_in.post(f"/vault/nodes/{outer}/move", json={"parent_id": inner})

        tree = (await signed_in.get(f"/vault/trees/{tree_id}")).json()
        assert [node["name"] for node in tree["nodes"]] == ["Outer"]
        assert [node["name"] for node in tree["nodes"][0]["children"]] == ["Inner"]

    async def test_moving_a_secret_under_a_secret_is_refused(self, signed_in: AsyncClient) -> None:
        tree_id = await _tree(signed_in)
        first = await _secret(signed_in, tree_id, "Twilio")
        second = await _secret(signed_in, tree_id, "SendGrid")

        response = await signed_in.post(
            f"/vault/nodes/{second['id']}/move", json={"parent_id": first["id"], "position": 0}
        )

        assert response.status_code == 422

    async def test_a_name_clash_at_the_destination_is_a_conflict(
        self, signed_in: AsyncClient
    ) -> None:
        tree_id = await _tree(signed_in)
        destination = await _branch(signed_in, tree_id, "Destination")
        await _branch(signed_in, tree_id, "Twilio", destination)
        moving = await _branch(signed_in, tree_id, "Twilio")

        response = await signed_in.post(
            f"/vault/nodes/{moving}/move", json={"parent_id": destination, "position": 0}
        )

        assert response.status_code == 409

    async def test_a_parent_outside_the_tree_is_refused(self, signed_in: AsyncClient) -> None:
        first = await _tree(signed_in, "Logins")
        second = (await signed_in.post("/projects/ATL/vault/trees", json={"name": "Links"})).json()[
            "id"
        ]
        elsewhere = await _branch(signed_in, second, "Elsewhere")
        node = await _branch(signed_in, first, "Here")

        response = await signed_in.post(
            f"/vault/nodes/{node}/move", json={"parent_id": elsewhere, "position": 0}
        )

        assert response.status_code == 422


class TestSecretsNeverLeak:
    """The plaintext must appear in exactly one place: the reveal response."""

    async def test_not_in_the_create_response(self, signed_in: AsyncClient) -> None:
        tree_id = await _tree(signed_in)

        response = await signed_in.post(
            "/vault/nodes",
            json={
                "tree_id": tree_id,
                "name": "Twilio",
                "kind": "secret",
                "secret": {"value": TWILIO_SECRET, "username": "ops@think41.com"},
            },
        )

        assert TWILIO_SECRET not in response.text
        assert "value" not in response.json()["secret"]

    async def test_not_in_the_tree_listing(self, signed_in: AsyncClient) -> None:
        tree_id = await _tree(signed_in)
        await _secret(signed_in, tree_id, notes="Full access; rotate on offboarding.")

        listed = await signed_in.get("/projects/ATL/vault/trees")
        detail = await signed_in.get(f"/vault/trees/{tree_id}")

        assert TWILIO_SECRET not in listed.text
        assert TWILIO_SECRET not in detail.text
        assert "rotate on offboarding" in detail.text  # notes are not encrypted

    async def test_not_in_a_single_node_read(self, signed_in: AsyncClient) -> None:
        tree_id = await _tree(signed_in)
        node = await _secret(signed_in, tree_id)

        response = await signed_in.get(f"/vault/nodes/{node['id']}")

        assert TWILIO_SECRET not in response.text

    async def test_not_in_the_activity_log(self, signed_in: AsyncClient) -> None:
        """The audit feed is readable with `read` alone, so a credential in a
        payload would hand it to every token in the system."""
        tree_id = await _tree(signed_in)
        node = await _secret(signed_in, tree_id)
        await signed_in.patch(
            f"/vault/nodes/{node['id']}", json={"secret": {"value": "a-new-password"}}
        )
        await signed_in.post(f"/vault/nodes/{node['id']}/reveal")

        feed = await signed_in.get("/activity")

        assert TWILIO_SECRET not in feed.text
        assert "a-new-password" not in feed.text

    async def test_not_in_an_error_message(self, signed_in: AsyncClient) -> None:
        """A failure must not become the leak that success was designed to avoid."""
        tree_id = await _tree(signed_in)
        first = await _secret(signed_in, tree_id, "Twilio")

        clash = await signed_in.post(
            "/vault/nodes",
            json={
                "tree_id": tree_id,
                "name": "Twilio",
                "kind": "secret",
                "secret": {"value": TWILIO_SECRET},
            },
        )
        branch = await _branch(signed_in, tree_id, "Messaging")
        wrong_kind = await signed_in.post(f"/vault/nodes/{branch}/reveal")
        bad_move = await signed_in.post(
            f"/vault/nodes/{first['id']}/move", json={"parent_id": UNKNOWN_ID}
        )

        assert clash.status_code == 409
        assert wrong_kind.status_code == 422
        assert bad_move.status_code == 422
        for response in (clash, wrong_kind, bad_move):
            assert TWILIO_SECRET not in response.text

    async def test_not_in_the_openapi_examples(self, signed_in: AsyncClient) -> None:
        """Nothing but SecretRevealed may carry a `value`, or a client could be
        generated that expects one where none is coming."""
        schema = (await signed_in.get("/openapi.json")).json()["components"]["schemas"]

        assert "value" in schema["SecretRevealed"]["properties"]
        assert "value" not in schema["SecretRead"]["properties"]
        assert "secret" not in schema["SecretRead"]["properties"]


class TestRevealing:
    async def test_returns_the_stored_credential(self, signed_in: AsyncClient) -> None:
        tree_id = await _tree(signed_in)
        node = await _secret(signed_in, tree_id)

        response = await signed_in.post(f"/vault/nodes/{node['id']}/reveal")

        assert response.status_code == 200
        assert response.json()["value"] == TWILIO_SECRET
        assert response.json()["name"] == "Twilio console"

    async def test_a_unicode_credential_survives_the_round_trip(
        self, signed_in: AsyncClient
    ) -> None:
        tree_id = await _tree(signed_in)
        secret = "pässwörd — 日本語 · 🔐"
        node = (
            await signed_in.post(
                "/vault/nodes",
                json={
                    "tree_id": tree_id,
                    "name": "Odd one",
                    "kind": "secret",
                    "secret": {"value": secret},
                },
            )
        ).json()

        response = await signed_in.post(f"/vault/nodes/{node['id']}/reveal")

        assert response.json()["value"] == secret

    async def test_a_branch_has_nothing_to_reveal(self, signed_in: AsyncClient) -> None:
        tree_id = await _tree(signed_in)
        branch = await _branch(signed_in, tree_id, "Messaging")

        response = await signed_in.post(f"/vault/nodes/{branch}/reveal")

        assert response.status_code == 422
        assert response.json()["error"]["details"]["kind"] == "branch"

    async def test_amending_the_value_changes_what_is_revealed(
        self, signed_in: AsyncClient
    ) -> None:
        tree_id = await _tree(signed_in)
        node = await _secret(signed_in, tree_id)

        await signed_in.patch(
            f"/vault/nodes/{node['id']}", json={"secret": {"value": "rotated-2026"}}
        )

        revealed = await signed_in.post(f"/vault/nodes/{node['id']}/reveal")
        assert revealed.json()["value"] == "rotated-2026"

    async def test_amending_metadata_leaves_the_credential_alone(
        self, signed_in: AsyncClient
    ) -> None:
        """Correcting a username must not require knowing the password."""
        tree_id = await _tree(signed_in)
        node = await _secret(signed_in, tree_id, username="old@think41.com")

        await signed_in.patch(
            f"/vault/nodes/{node['id']}", json={"secret": {"username": "ops@think41.com"}}
        )

        assert (await signed_in.post(f"/vault/nodes/{node['id']}/reveal")).json()[
            "value"
        ] == TWILIO_SECRET

    async def test_every_reveal_is_written_to_the_audit_trail(self, signed_in: AsyncClient) -> None:
        """This log is the only record of who has seen which credential."""
        tree_id = await _tree(signed_in)
        node = await _secret(signed_in, tree_id)

        await signed_in.post(f"/vault/nodes/{node['id']}/reveal")
        await signed_in.post(f"/vault/nodes/{node['id']}/reveal")

        feed = (await signed_in.get("/activity", params={"entity_type": "vault_node"})).json()
        reveals = [entry for entry in feed if entry["verb"] == "vault.secret_revealed"]
        assert len(reveals) == 2
        assert reveals[0]["payload"] == {"name": "Twilio console", "tree": "Logins"}
        assert reveals[0]["entity_id"] == node["id"]

    async def test_the_reveal_is_scoped_to_the_project_feed(self, signed_in: AsyncClient) -> None:
        tree_id = await _tree(signed_in)
        node = await _secret(signed_in, tree_id)
        project_id = (await signed_in.get("/projects/ATL")).json()["id"]

        await signed_in.post(f"/vault/nodes/{node['id']}/reveal")

        feed = (await signed_in.get("/activity", params={"project_id": project_id})).json()
        assert "vault.secret_revealed" in [entry["verb"] for entry in feed]


class TestScopes:
    async def _token(self, client: AsyncClient, *scopes: str) -> dict[str, str]:
        created = await client.post("/tokens", json={"name": "agent", "scopes": list(scopes)})
        assert created.status_code == 201, created.text
        return {"Authorization": f"Bearer {created.json()['token']}"}

    async def test_reading_a_tree_needs_vault_read(self, signed_in: AsyncClient) -> None:
        tree_id = await _tree(signed_in)
        plain = await self._token(signed_in, "read", "write")

        response = await signed_in.get(f"/vault/trees/{tree_id}", headers=plain)

        assert response.status_code == 403
        assert response.json()["error"]["details"]["missing_scopes"] == ["vault:read"]

    async def test_a_board_agent_cannot_write_into_the_vault(self, signed_in: AsyncClient) -> None:
        """`write` alone moves cards. It must not reach the credential store."""
        tree_id = await _tree(signed_in)
        plain = await self._token(signed_in, "read", "write")

        response = await signed_in.post(
            "/vault/nodes",
            json={"tree_id": tree_id, "name": "Sneaky", "kind": "branch"},
            headers=plain,
        )

        assert response.status_code == 403

    async def test_vault_read_can_see_the_structure(self, signed_in: AsyncClient) -> None:
        tree_id = await _tree(signed_in)
        await _secret(signed_in, tree_id, username="ops@think41.com")
        reader = await self._token(signed_in, "read", "vault:read")

        response = await signed_in.get(f"/vault/trees/{tree_id}", headers=reader)

        assert response.status_code == 200
        assert response.json()["nodes"][0]["secret"]["username"] == "ops@think41.com"

    async def test_vault_read_without_reveal_is_refused_the_plaintext(
        self, signed_in: AsyncClient
    ) -> None:
        """The rule the whole phase turns on."""
        tree_id = await _tree(signed_in)
        node = await _secret(signed_in, tree_id)
        reader = await self._token(signed_in, "read", "vault:read")

        response = await signed_in.post(f"/vault/nodes/{node['id']}/reveal", headers=reader)

        assert response.status_code == 403
        assert response.json()["error"]["details"]["missing_scopes"] == ["vault:reveal"]
        assert TWILIO_SECRET not in response.text

    async def test_a_refused_reveal_is_not_logged_as_one(self, signed_in: AsyncClient) -> None:
        tree_id = await _tree(signed_in)
        node = await _secret(signed_in, tree_id)
        reader = await self._token(signed_in, "read", "vault:read")

        await signed_in.post(f"/vault/nodes/{node['id']}/reveal", headers=reader)

        feed = (await signed_in.get("/activity")).json()
        assert "vault.secret_revealed" not in [entry["verb"] for entry in feed]

    async def test_a_reveal_token_gets_the_plaintext(self, signed_in: AsyncClient) -> None:
        tree_id = await _tree(signed_in)
        node = await _secret(signed_in, tree_id)
        revealer = await self._token(signed_in, "read", "vault:read", "vault:reveal")

        response = await signed_in.post(f"/vault/nodes/{node['id']}/reveal", headers=revealer)

        assert response.status_code == 200
        assert response.json()["value"] == TWILIO_SECRET

    async def test_the_reveal_names_the_token_that_asked(self, signed_in: AsyncClient) -> None:
        tree_id = await _tree(signed_in)
        node = await _secret(signed_in, tree_id)
        revealer = await self._token(signed_in, "read", "vault:read", "vault:reveal")

        await signed_in.post(f"/vault/nodes/{node['id']}/reveal", headers=revealer)

        feed = (await signed_in.get("/activity", params={"entity_type": "vault_node"})).json()
        reveal = next(entry for entry in feed if entry["verb"] == "vault.secret_revealed")
        assert reveal["actor_label"] == "agent"
        assert reveal["channel"] == "api"

    async def test_an_anonymous_caller_gets_nowhere(self, client: AsyncClient) -> None:
        assert (await client.get(f"/vault/trees/{UNKNOWN_ID}")).status_code == 401


class TestEncryptionAtRest:
    async def test_the_credential_is_not_stored_in_the_clear(
        self, signed_in: AsyncClient, session: AsyncSession
    ) -> None:
        """Reaches past the API on purpose: what is on disk is the question."""
        tree_id = await _tree(signed_in)
        await _secret(signed_in, tree_id, username="ops@think41.com", notes="Rotate me.")

        stored = (await session.scalars(select(VaultSecret))).one()

        assert TWILIO_SECRET.encode() not in stored.secret_ciphertext
        assert stored.key_version == 1
        # Searchable by design; only the value is hidden.
        assert stored.username == "ops@think41.com"
        assert stored.notes == "Rotate me."

    async def test_two_identical_credentials_are_stored_differently(
        self, signed_in: AsyncClient, session: AsyncSession
    ) -> None:
        """Otherwise the database would reveal which accounts share a password."""
        tree_id = await _tree(signed_in)
        await _secret(signed_in, tree_id, "Twilio")
        await _secret(signed_in, tree_id, "SendGrid")

        stored = list(await session.scalars(select(VaultSecret)))

        assert len(stored) == 2
        assert stored[0].secret_ciphertext != stored[1].secret_ciphertext

    async def test_a_ciphertext_moved_to_another_node_will_not_open(
        self, signed_in: AsyncClient, session: AsyncSession
    ) -> None:
        """The associated-data binding, end to end: swapping two rows' bytes
        must fail loudly rather than reveal the wrong credential."""
        tree_id = await _tree(signed_in)
        first = await _secret(signed_in, tree_id, "Twilio")
        second = await _secret(signed_in, tree_id, "SendGrid")

        rows = {str(row.node_id): row for row in await session.scalars(select(VaultSecret))}
        rows[second["id"]].secret_ciphertext = rows[first["id"]].secret_ciphertext
        await session.commit()

        response = await signed_in.post(f"/vault/nodes/{second['id']}/reveal")

        assert response.status_code == 500
        assert response.json()["error"]["code"] == "secret_unreadable"
        assert TWILIO_SECRET not in response.text

    async def test_a_tampered_ciphertext_will_not_open(
        self, signed_in: AsyncClient, session: AsyncSession
    ) -> None:
        tree_id = await _tree(signed_in)
        node = await _secret(signed_in, tree_id)

        row = (await session.scalars(select(VaultSecret))).one()
        altered = bytearray(row.secret_ciphertext)
        altered[-1] ^= 0x01
        row.secret_ciphertext = bytes(altered)
        await session.commit()

        response = await signed_in.post(f"/vault/nodes/{node['id']}/reveal")

        assert response.status_code == 500
        assert response.json()["error"]["code"] == "secret_unreadable"


class TestSchemaConstraints:
    async def test_a_secret_cannot_be_attached_to_a_branch(
        self, signed_in: AsyncClient, session: AsyncSession
    ) -> None:
        """The API refuses it, and so does the schema — the composite foreign
        key on (node_id, node_kind) has nothing to point at for a branch."""
        tree_id = await _tree(signed_in)
        branch = await _branch(signed_in, tree_id, "Messaging")

        session.add(
            VaultSecret(
                node_id=UUID(branch),
                node_kind="secret",
                notes="",
                secret_ciphertext=b"x" * 40,
                key_version=1,
            )
        )

        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
        else:  # pragma: no cover - only if the constraint went missing
            raise AssertionError("the foreign key did not fire")

    async def test_a_secret_row_cannot_claim_to_be_a_branch(
        self, signed_in: AsyncClient, session: AsyncSession
    ) -> None:
        tree_id = await _tree(signed_in)
        node = await _secret(signed_in, tree_id)

        row = (await session.scalars(select(VaultSecret))).one()
        row.node_kind = "branch"  # type: ignore[assignment]

        try:
            await session.flush()
        except (IntegrityError, DBAPIError):
            await session.rollback()
        else:  # pragma: no cover - only if the CHECK went missing
            raise AssertionError("the CHECK constraint did not fire")
        assert node["id"]

    async def test_deleting_a_project_row_takes_its_vault(
        self, signed_in: AsyncClient, session: AsyncSession
    ) -> None:
        """Projects are archived rather than deleted, but the cascade must be
        sound: a stray tree would keep credentials alive with no way in."""
        from app.models import Project

        tree_id = await _tree(signed_in)
        await _secret(signed_in, tree_id)
        project = (await session.scalars(select(Project))).one()

        await session.delete(project)
        await session.flush()

        assert list(await session.scalars(select(VaultTree))) == []
        assert list(await session.scalars(select(VaultSecret))) == []


class TestProjectSummary:
    async def test_counts_start_at_zero(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)

        summary = (await signed_in.get("/projects/ATL/summary")).json()

        assert summary["vault_tree_count"] == 0
        assert summary["vault_secret_count"] == 0

    async def test_counts_trees_and_credentials(self, signed_in: AsyncClient) -> None:
        first = await _tree(signed_in, "Logins")
        second = (await signed_in.post("/projects/ATL/vault/trees", json={"name": "Links"})).json()[
            "id"
        ]
        branch = await _branch(signed_in, first, "Messaging")
        await _secret(signed_in, first, "Twilio", parent=branch)
        await _secret(signed_in, first, "SendGrid")
        await _secret(signed_in, second, "Figma")

        summary = (await signed_in.get("/projects/ATL/summary")).json()

        assert summary["vault_tree_count"] == 2
        assert summary["vault_secret_count"] == 3

    async def test_branches_are_not_counted_as_credentials(self, signed_in: AsyncClient) -> None:
        tree_id = await _tree(signed_in)
        await _branch(signed_in, tree_id, "Messaging")

        summary = (await signed_in.get("/projects/ATL/summary")).json()

        assert summary["vault_secret_count"] == 0


class TestAuditTrail:
    async def test_every_mutation_is_recorded(self, signed_in: AsyncClient) -> None:
        tree_id = await _tree(signed_in)
        branch = await _branch(signed_in, tree_id, "Messaging")
        node = await _secret(signed_in, tree_id)
        await signed_in.patch(f"/vault/nodes/{node['id']}", json={"name": "Twilio prod"})
        await signed_in.post(
            f"/vault/nodes/{node['id']}/move", json={"parent_id": branch, "position": 0}
        )
        await signed_in.delete(f"/vault/nodes/{node['id']}")
        await signed_in.patch(f"/vault/trees/{tree_id}", json={"name": "Accounts"})
        await signed_in.delete(f"/vault/trees/{tree_id}")

        feed = (await signed_in.get("/activity")).json()
        verbs = [entry["verb"] for entry in feed if entry["verb"].startswith("vault.")]

        assert verbs == [
            "vault.tree_deleted",
            "vault.tree_updated",
            "vault.node_deleted",
            "vault.node_moved",
            "vault.node_updated",
            "vault.node_created",
            "vault.node_created",
            "vault.tree_created",
        ]


class TestWithoutAVaultKey:
    """A deployment that never ran `make vault-key`.

    It must boot, it must serve everything outside the vault, and it must
    refuse — loudly — to store or read a credential. Silently keeping
    plaintext because a secret is missing would be the worst possible bug in
    this file.
    """

    @pytest.fixture
    async def keyless(self, settings: Settings, database: Database) -> AsyncIterator[AsyncClient]:
        app = create_app(settings.model_copy(update={"vault_key": ""}))
        app.state.database = database
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test/api/v1") as http:
            response = await http.post("/auth/login", json={"password": OWNER_PASSWORD})
            assert response.status_code == 200, response.text
            yield http

    async def test_the_rest_of_the_app_still_works(self, keyless: AsyncClient) -> None:
        assert (await keyless.post("/projects", json=ATLAS)).status_code == 201

    async def test_a_tree_can_still_be_made(self, keyless: AsyncClient) -> None:
        """Structure holds no credential, so it needs no key."""
        assert await _tree(keyless) is not None

    async def test_storing_a_credential_is_refused(self, keyless: AsyncClient) -> None:
        tree_id = await _tree(keyless)

        response = await keyless.post(
            "/vault/nodes",
            json={
                "tree_id": tree_id,
                "name": "Twilio",
                "kind": "secret",
                "secret": {"value": TWILIO_SECRET},
            },
        )

        assert response.status_code == 500
        assert response.json()["error"]["code"] == "vault_unavailable"
        assert "CYLIST_VAULT_KEY" in response.json()["error"]["message"]

    async def test_nothing_was_written(self, keyless: AsyncClient, session: AsyncSession) -> None:
        """The refusal must not leave a node behind, let alone a plaintext one."""
        tree_id = await _tree(keyless)
        await keyless.post(
            "/vault/nodes",
            json={
                "tree_id": tree_id,
                "name": "Twilio",
                "kind": "secret",
                "secret": {"value": TWILIO_SECRET},
            },
        )

        assert list(await session.scalars(select(VaultSecret))) == []
        assert list(await session.scalars(select(VaultNode))) == []

    async def test_revealing_is_refused(self, keyless: AsyncClient, signed_in: AsyncClient) -> None:
        """A secret written while a key was configured stays unreadable
        without it — rather than coming back as an empty string."""
        tree_id = await _tree(signed_in)
        node = await _secret(signed_in, tree_id)

        response = await keyless.post(f"/vault/nodes/{node['id']}/reveal")

        assert response.status_code == 500
        assert response.json()["error"]["code"] == "vault_unavailable"
        assert TWILIO_SECRET not in response.text

    async def test_a_malformed_key_is_refused_too(
        self, settings: Settings, database: Database
    ) -> None:
        app = create_app(settings.model_copy(update={"vault_key": "obviously not base64"}))
        app.state.database = database
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test/api/v1") as http:
            await http.post("/auth/login", json={"password": OWNER_PASSWORD})
            tree_id = await _tree(http)
            response = await http.post(
                "/vault/nodes",
                json={
                    "tree_id": tree_id,
                    "name": "Twilio",
                    "kind": "secret",
                    "secret": {"value": TWILIO_SECRET},
                },
            )

        assert response.status_code == 500
        assert response.json()["error"]["code"] == "vault_unavailable"
