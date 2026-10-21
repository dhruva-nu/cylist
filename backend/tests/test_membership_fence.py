"""A person sees the projects they are on, and no others.

Membership is the fence. A project somebody has not been added to is not in
their list, and every way of reaching it — its key in a path, a card's
reference, the id of a doc, a folder, a vault node — answers as though it did
not exist. Not a 403: a refusal that says "you may not" has already said
"there is something here", and the name of a project is the interesting part.

An agent's token acts as whoever minted it, so it is held to the same line.
"""

from __future__ import annotations

from typing import Any

import httpx2
import pytest
from httpx import AsyncClient
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent

from tests import ws
from tests.conftest import INVITEE_PASSWORD, open_account, put_members

ATLAS = {"key": "ATL", "name": "Atlas Billing Migration"}
HERMES = {"key": "HRM", "name": "Hermes Notifications"}

ADITI = {
    "name": "Aditi K",
    "kind": "team",
    "title": "Backend engineer",
    "responsibilities": "Payments and webhooks.",
}
ADITI_EMAIL = "aditi@cylist.dev"


async def _created(client: AsyncClient, path: str, body: dict[str, Any]) -> dict[str, Any]:
    response = await client.post(path, json=body)
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _atlas(owner: AsyncClient) -> dict[str, str]:
    """ATL with one of everything on it, and the path that reaches each.

    Every path is addressed the way a client would hold it after seeing the
    thing once — by key, by reference, or by the row's own id — because each
    of those is a separate way in, and each has to be shut.
    """
    await _created(owner, "/projects", ATLAS)
    card = await _created(
        owner,
        "/projects/ATL/tasks",
        {"title": "Stripe webhook idempotency", "description": "Dedupe.", "type": "bug"},
    )
    reference = card["reference"]
    comment = await _created(owner, f"/tasks/{reference}/comments", {"body": "Looking at it."})
    column = card["column_id"]
    owner_id = card["assignee"]["id"]
    goal = await _created(owner, "/projects/ATL/goals", {"name": "Search", "owner_id": owner_id})
    template = await _created(owner, "/projects/ATL/templates", {"name": "Hotfix"})
    topic = await _created(
        owner, "/projects/ATL/doc-topics", {"section": "engineering", "name": "apis"}
    )
    doc = await _created(owner, f"/doc-topics/{topic['id']}/docs", {"title": "Webhooks"})
    folder = await _created(owner, "/projects/ATL/folders", {"name": "Contracts"})
    link = await _created(
        owner, f"/folders/{folder['id']}/links", {"name": "Spec", "url": "https://example.com"}
    )
    tree = await _created(owner, "/projects/ATL/vault/trees", {"name": "Logins"})
    node = await _created(
        owner,
        "/vault/nodes",
        {"tree_id": tree["id"], "parent_id": None, "name": "Production", "kind": "branch"},
    )
    return {
        "project": "/projects/ATL",
        "summary": "/projects/ATL/summary",
        "board": "/projects/ATL/columns",
        "tasks": "/projects/ATL/tasks",
        "people": "/projects/ATL/members",
        "docs": "/projects/ATL/docs",
        "files": "/projects/ATL/folders",
        "vault": "/projects/ATL/vault/trees",
        "card": f"/tasks/{reference}",
        "card by id": f"/tasks/{card['id']}",
        "comments": f"/tasks/{reference}/comments",
        "comment": f"/comments/{comment['id']}",
        "column": f"/columns/{column}",
        "goal": f"/goals/{goal['reference']}",
        "template": f"/templates/{template['id']}",
        "doc": f"/docs/{doc['id']}",
        "folder": f"/folders/{folder['id']}/children",
        "link": f"/items/{link['id']}",
        "tree": f"/vault/trees/{tree['id']}",
        "node": f"/vault/nodes/{node['id']}",
    }


READS = [
    "project",
    "summary",
    "board",
    "tasks",
    "people",
    "docs",
    "files",
    "vault",
    "card",
    "card by id",
    "comments",
    "goal",
    "doc",
    "folder",
    "link",
    "tree",
    "node",
]
"""The paths answering ``GET``. The rest — a comment, a column, a template —
only take writes, which :class:`TestAProjectYouAreNotOn` sends them."""


async def _aditi(owner: AsyncClient, theirs: AsyncClient) -> str:
    """Aditi, invited and signed in on her own browser, and on no project."""
    person, token = await open_account(owner, ADITI, ADITI_EMAIL)
    accepted = await theirs.post(
        "/auth/accept-invite", json={"token": token, "password": INVITEE_PASSWORD}
    )
    assert accepted.status_code == 200, accepted.text
    return str(person["id"])


async def _token(client: AsyncClient, scopes: list[str]) -> dict[str, str]:
    issued = await _created(client, "/tokens", {"name": "Claude Code", "scopes": scopes})
    return {"Authorization": f"Bearer {issued['token']}"}


def _keys(listed: list[dict[str, Any]]) -> list[str]:
    return [project["key"] for project in listed]


class TestTheList:
    async def test_a_member_sees_the_project(self, signed_in: AsyncClient) -> None:
        await _created(signed_in, "/projects", ATLAS)

        assert _keys((await signed_in.get("/projects")).json()["items"]) == ["ATL"]

    async def test_somebody_not_on_it_does_not(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _created(signed_in, "/projects", ATLAS)
        await _aditi(signed_in, other_client)

        assert (await other_client.get("/projects")).json()["items"] == []

    async def test_an_archived_project_is_still_only_its_members(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _created(signed_in, "/projects", ATLAS)
        await signed_in.patch("/projects/ATL", json={"archived": True})
        await _aditi(signed_in, other_client)

        listed = await other_client.get("/projects", params={"include_archived": True})

        assert listed.json()["items"] == []

    async def test_a_new_person_sees_exactly_the_one_they_were_added_to(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        """The card's own scenario: invited, put on one board, signs in."""
        await _created(signed_in, "/projects", ATLAS)
        await _created(signed_in, "/projects", HERMES)
        person, token = await open_account(signed_in, ADITI, ADITI_EMAIL)
        await put_members(signed_in, "HRM", [person["id"]])

        await other_client.post(
            "/auth/accept-invite", json={"token": token, "password": INVITEE_PASSWORD}
        )

        assert _keys((await other_client.get("/projects")).json()["items"]) == ["HRM"]
        assert _keys((await signed_in.get("/projects")).json()["items"]) == ["HRM", "ATL"]


class TestAProjectYouAreNotOn:
    @pytest.mark.parametrize("name", READS)
    async def test_a_member_can_read_it(self, signed_in: AsyncClient, name: str) -> None:
        paths = await _atlas(signed_in)

        response = await signed_in.get(paths[name])

        assert response.status_code == 200, response.text

    @pytest.mark.parametrize("name", READS)
    async def test_reading_it_is_not_found(
        self, signed_in: AsyncClient, other_client: AsyncClient, name: str
    ) -> None:
        paths = await _atlas(signed_in)
        await _aditi(signed_in, other_client)

        response = await other_client.get(paths[name])

        assert response.status_code == 404, response.text

    async def test_it_reads_exactly_like_a_project_that_does_not_exist(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        """So the refusal cannot be used to find out which keys are taken."""
        await _created(signed_in, "/projects", ATLAS)
        await _aditi(signed_in, other_client)

        taken = (await other_client.get("/projects/ATL")).json()
        free = (await other_client.get("/projects/NOPE")).json()

        assert taken["error"] == {**free["error"], "message": "No project matching 'ATL'."}
        assert free["error"]["message"] == "No project matching 'NOPE'."

    @pytest.mark.parametrize(
        ("method", "name", "body"),
        [
            ("POST", "tasks", {"title": "Sneak", "description": "In.", "type": "bug"}),
            ("PATCH", "card", {"title": "Renamed"}),
            ("POST", "comments", {"body": "Hello?"}),
            ("DELETE", "comment", None),
            ("PATCH", "column", {"name": "Mine"}),
            ("PATCH", "goal", {"name": "Mine"}),
            ("PATCH", "template", {"name": "Mine"}),
            ("PATCH", "doc", {"title": "Mine"}),
            ("PATCH", "link", {"name": "Mine"}),
            ("PATCH", "tree", {"name": "Mine"}),
            ("PATCH", "node", {"name": "Mine"}),
            ("PUT", "people", {"person_ids": []}),
        ],
    )
    async def test_writing_to_it_is_not_found(
        self,
        signed_in: AsyncClient,
        other_client: AsyncClient,
        method: str,
        name: str,
        body: dict[str, Any] | None,
    ) -> None:
        paths = await _atlas(signed_in)
        await _aditi(signed_in, other_client)

        response = await other_client.request(method, paths[name], json=body)

        assert response.status_code == 404, response.text

    async def test_filing_into_its_vault_is_not_found(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        """The one write whose project is named only in the body."""
        paths = await _atlas(signed_in)
        tree_id = paths["tree"].rsplit("/", 1)[1]
        await _aditi(signed_in, other_client)

        response = await other_client.post(
            "/vault/nodes",
            json={"tree_id": tree_id, "parent_id": None, "name": "Mine", "kind": "branch"},
        )

        assert response.status_code == 404

    async def test_being_added_opens_it(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        paths = await _atlas(signed_in)
        aditi = await _aditi(signed_in, other_client)

        await put_members(signed_in, "ATL", [aditi])

        assert (await other_client.get(paths["card"])).status_code == 200
        assert _keys((await other_client.get("/projects")).json()["items"]) == ["ATL"]

    async def test_being_taken_off_closes_it_again(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        paths = await _atlas(signed_in)
        aditi = await _aditi(signed_in, other_client)
        await put_members(signed_in, "ATL", [aditi])

        await put_members(signed_in, "ATL", [])

        assert (await other_client.get(paths["card"])).status_code == 404
        assert (await other_client.get("/projects")).json()["items"] == []

    async def test_its_board_socket_is_closed_as_not_found(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _created(signed_in, "/projects", ATLAS)
        await _aditi(signed_in, other_client)
        cookie = "; ".join(f"{name}={value}" for name, value in other_client.cookies.items())

        async with ws.connect(
            other_client.app,  # type: ignore[attr-defined]
            "/api/v1/projects/ATL/board/ws",
            cookies=cookie,
        ) as socket:
            assert await socket.expect_close() == 4404


class TestTheFeed:
    async def test_leaves_out_projects_you_are_not_on(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _atlas(signed_in)
        await _aditi(signed_in, other_client)

        feed = (await other_client.get("/activity", params={"limit": 100})).json()["items"]

        assert feed, "the directory entries are nobody's project, so they are still there"
        assert all(entry["project_id"] is None for entry in feed)

    async def test_naming_one_you_are_not_on_is_not_found(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _created(signed_in, "/projects", ATLAS)
        await _aditi(signed_in, other_client)

        response = await other_client.get("/activity", params={"project": "ATL"})

        assert response.status_code == 404


class TestAgentTokens:
    """A token is whoever minted it — no wider, and no narrower."""

    async def test_a_members_token_reaches_the_project(self, signed_in: AsyncClient) -> None:
        paths = await _atlas(signed_in)
        agent = await _token(signed_in, ["read", "write"])

        assert _keys((await signed_in.get("/projects", headers=agent)).json()["items"]) == ["ATL"]
        assert (await signed_in.get(paths["card"], headers=agent)).status_code == 200

    async def test_an_outsiders_token_does_not(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        """Every scope there is, and still not a way onto somebody else's board."""
        paths = await _atlas(signed_in)
        await _aditi(signed_in, other_client)
        agent = await _token(other_client, ["read", "write", "vault:read", "vault:reveal", "admin"])

        assert (await other_client.get("/projects", headers=agent)).json()["items"] == []
        assert (await other_client.get(paths["card"], headers=agent)).status_code == 404
        assert (await other_client.get(paths["node"], headers=agent)).status_code == 404

    async def test_list_projects_over_mcp_is_the_minters_list(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        """The hosted MCP tools go through the same API, so they see the same."""
        await _created(signed_in, "/projects", ATLAS)
        await _created(signed_in, "/projects", HERMES)
        aditi = await _aditi(signed_in, other_client)
        await put_members(signed_in, "HRM", [aditi])
        agent = await _token(other_client, ["read", "write"])
        app = signed_in.app  # type: ignore[attr-defined]

        async with (
            app.state.mcp.run(),
            httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), headers=agent) as http,
            Client(
                streamable_http_client("http://test/mcp", http_client=http), mode="legacy"
            ) as mcp,
        ):
            listed = await mcp.call_tool("list_projects", {})

        assert isinstance(listed, CallToolResult)
        assert not listed.is_error, listed.content
        text = "".join(block.text for block in listed.content if isinstance(block, TextContent))
        assert "HRM" in text
        assert "ATL" not in text


class TestTheBootstrapSession:
    async def test_sees_every_project(self, bootstrapped: AsyncClient) -> None:
        """It is nobody, so it is on nothing — and it is what adds the first
        person to a project, so it cannot be fenced out of them."""
        await _created(bootstrapped, "/projects", ATLAS)
        await bootstrapped.put("/projects/ATL/members", json={"person_ids": []})

        assert _keys((await bootstrapped.get("/projects")).json()["items"]) == ["ATL"]
        assert (await bootstrapped.get("/projects/ATL/members")).status_code == 200
