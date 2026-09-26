"""Docs: a project's markdown, filed as section → topic → doc.

What is checked here is the filing rather than the storing: that both sections
are always there, that a topic is named once per section and kept in the order
it was put in, that a doc moves between topics without leaving a gap behind,
that a topic holding docs cannot be deleted out from under them, and that the
fence is the `docs` permission.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from app.auth.permissions import Permission
from tests.conftest import INVITEE_PASSWORD, OWNER_NAME, open_account

ATLAS = {"key": "ATL", "name": "Atlas Billing Migration"}


async def _topic(client: AsyncClient, section: str, name: str) -> dict[str, Any]:
    response = await client.post(
        "/projects/ATL/doc-topics", json={"section": section, "name": name}
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _doc(client: AsyncClient, topic_id: str, title: str, body: str = "") -> dict[str, Any]:
    response = await client.post(
        f"/doc-topics/{topic_id}/docs", json={"title": title, "body": body}
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _tree(client: AsyncClient) -> dict[str, Any]:
    response = await client.get("/projects/ATL/docs")
    assert response.status_code == 200, response.text
    return dict(response.json())


def _titles(tree: dict[str, Any], section: str, topic: str) -> list[str]:
    part = next(part for part in tree["sections"] if part["section"] == section)
    found = next(found for found in part["topics"] if found["name"] == topic)
    return [doc["title"] for doc in found["docs"]]


def _topic_names(tree: dict[str, Any], section: str) -> list[str]:
    part = next(part for part in tree["sections"] if part["section"] == section)
    return [topic["name"] for topic in part["topics"]]


class TestTheTree:
    async def test_an_empty_project_still_has_both_sections(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)

        tree = await _tree(signed_in)

        assert [(part["section"], part["label"]) for part in tree["sections"]] == [
            ("product", "Product"),
            ("engineering", "Engineering"),
        ]
        assert all(part["topics"] == [] for part in tree["sections"])
        assert tree["doc_count"] == 0

    async def test_docs_are_filed_under_their_topic_without_bodies(
        self, signed_in: AsyncClient
    ) -> None:
        await signed_in.post("/projects", json=ATLAS)
        mcp = await _topic(signed_in, "engineering", "MCP")
        await _doc(signed_in, mcp["id"], "Tool list", "# Tools\n\nEvery tool.")

        tree = await _tree(signed_in)

        engineering = tree["sections"][1]
        assert engineering["topics"][0]["doc_count"] == 1
        listed = engineering["topics"][0]["docs"][0]
        assert listed["title"] == "Tool list"
        assert "body" not in listed
        assert tree["doc_count"] == 1

    async def test_the_hub_counts_docs(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        topic = await _topic(signed_in, "product", "Goals")
        await _doc(signed_in, topic["id"], "What a goal is")
        await _doc(signed_in, topic["id"], "Progress")

        summary = (await signed_in.get("/projects/ATL/summary")).json()

        assert summary["doc_count"] == 2


class TestTopics:
    async def test_a_name_is_taken_once_per_section(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        await _topic(signed_in, "engineering", "APIs")

        again = await signed_in.post(
            "/projects/ATL/doc-topics", json={"section": "engineering", "name": "apis"}
        )
        elsewhere = await signed_in.post(
            "/projects/ATL/doc-topics", json={"section": "product", "name": "APIs"}
        )

        assert again.status_code == 409
        assert "Engineering already has a topic called 'APIs'" in again.json()["error"]["message"]
        assert elsewhere.status_code == 201

    async def test_a_section_outside_the_two_is_refused(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)

        response = await signed_in.post(
            "/projects/ATL/doc-topics", json={"section": "design", "name": "Colours"}
        )

        assert response.status_code == 422

    async def test_topics_keep_the_order_they_are_given(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        ids = [
            (await _topic(signed_in, "engineering", name))["id"] for name in ("MCP", "CLI", "FE")
        ]

        response = await signed_in.put(
            "/projects/ATL/doc-topics/order",
            json={"section": "engineering", "topic_ids": [ids[2], ids[0], ids[1]]},
        )

        assert response.status_code == 200, response.text
        assert _topic_names(await _tree(signed_in), "engineering") == ["FE", "MCP", "CLI"]

    async def test_a_partial_order_is_refused(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        first = await _topic(signed_in, "engineering", "MCP")
        await _topic(signed_in, "engineering", "CLI")

        response = await signed_in.put(
            "/projects/ATL/doc-topics/order",
            json={"section": "engineering", "topic_ids": [first["id"]]},
        )

        assert response.status_code == 422

    async def test_renaming_keeps_the_section(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        topic = await _topic(signed_in, "engineering", "DB")

        renamed = await signed_in.patch(f"/doc-topics/{topic['id']}", json={"name": "DB schema"})

        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["name"] == "DB schema"
        assert renamed.json()["section"] == "engineering"

    async def test_a_topic_holding_docs_is_not_deleted(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        topic = await _topic(signed_in, "product", "Tasks")
        await _doc(signed_in, topic["id"], "Statuses")

        response = await signed_in.delete(f"/doc-topics/{topic['id']}")

        assert response.status_code == 409
        assert "still has 1 doc in it" in response.json()["error"]["message"]
        assert _titles(await _tree(signed_in), "product", "Tasks") == ["Statuses"]

    async def test_deleting_an_empty_topic_closes_the_gap(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        topics = [await _topic(signed_in, "product", name) for name in ("A", "B", "C")]

        deleted = await signed_in.delete(f"/doc-topics/{topics[1]['id']}")

        assert deleted.status_code == 200
        part = (await _tree(signed_in))["sections"][0]
        assert [(topic["name"], topic["position"]) for topic in part["topics"]] == [
            ("A", 0),
            ("C", 1),
        ]


class TestDocs:
    async def test_the_writer_is_the_author(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        topic = await _topic(signed_in, "engineering", "CLI")

        doc = await _doc(signed_in, topic["id"], "Install", "`uv tool install cylist`")

        assert doc["author"]["name"] == OWNER_NAME
        assert doc["section"] == "engineering"
        assert doc["topic_name"] == "CLI"

        read = (await signed_in.get(f"/docs/{doc['id']}")).json()
        assert read["body"] == "`uv tool install cylist`"

    async def test_editing_changes_only_what_was_sent(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        topic = await _topic(signed_in, "engineering", "CLI")
        doc = await _doc(signed_in, topic["id"], "Install", "old")

        edited = await signed_in.patch(f"/docs/{doc['id']}", json={"body": "new"})

        assert edited.status_code == 200, edited.text
        assert edited.json()["title"] == "Install"
        assert edited.json()["body"] == "new"

    async def test_moving_a_doc_refiles_it_and_leaves_no_gap(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        cli = await _topic(signed_in, "engineering", "CLI")
        goals = await _topic(signed_in, "product", "Goals")
        first = await _doc(signed_in, cli["id"], "One")
        await _doc(signed_in, cli["id"], "Two")
        await _doc(signed_in, goals["id"], "Existing")

        moved = await signed_in.patch(
            f"/docs/{first['id']}", json={"topic_id": goals["id"], "position": 0}
        )

        assert moved.status_code == 200, moved.text
        assert moved.json()["section"] == "product"
        tree = await _tree(signed_in)
        assert _titles(tree, "product", "Goals") == ["One", "Existing"]
        assert _titles(tree, "engineering", "CLI") == ["Two"]
        cli_docs = tree["sections"][1]["topics"][0]["docs"]
        assert [doc["position"] for doc in cli_docs] == [0]

    async def test_a_doc_cannot_move_to_another_projects_topic(
        self, signed_in: AsyncClient
    ) -> None:
        await signed_in.post("/projects", json=ATLAS)
        await signed_in.post("/projects", json={"key": "HRM", "name": "Hermes"})
        mine = await _topic(signed_in, "engineering", "CLI")
        theirs = await signed_in.post(
            "/projects/HRM/doc-topics", json={"section": "engineering", "name": "CLI"}
        )
        doc = await _doc(signed_in, mine["id"], "Install")

        response = await signed_in.patch(
            f"/docs/{doc['id']}", json={"topic_id": theirs.json()["id"]}
        )

        assert response.status_code == 422

    async def test_docs_keep_the_order_they_are_given(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        topic = await _topic(signed_in, "engineering", "APIs")
        ids = [(await _doc(signed_in, topic["id"], title))["id"] for title in ("A", "B", "C")]

        response = await signed_in.put(
            f"/doc-topics/{topic['id']}/order", json={"doc_ids": [ids[1], ids[2], ids[0]]}
        )

        assert response.status_code == 200, response.text
        assert _titles(await _tree(signed_in), "engineering", "APIs") == ["B", "C", "A"]

    async def test_deleting_a_doc_closes_the_gap(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        topic = await _topic(signed_in, "engineering", "APIs")
        ids = [(await _doc(signed_in, topic["id"], title))["id"] for title in ("A", "B", "C")]

        await signed_in.delete(f"/docs/{ids[0]}")

        docs = (await _tree(signed_in))["sections"][1]["topics"][0]["docs"]
        assert [(doc["title"], doc["position"]) for doc in docs] == [("B", 0), ("C", 1)]

    async def test_a_blank_title_is_refused(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        topic = await _topic(signed_in, "engineering", "APIs")

        response = await signed_in.post(f"/doc-topics/{topic['id']}/docs", json={"title": "  "})

        assert response.status_code == 422


class TestTheFence:
    async def test_a_role_without_docs_may_read_but_not_write(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await signed_in.post("/projects", json=ATLAS)
        topic = await _topic(signed_in, "engineering", "APIs")
        doc = await _doc(signed_in, topic["id"], "Auth")

        person, token = await open_account(
            signed_in,
            {"name": "Aditi K", "kind": "team", "title": "Engineer", "responsibilities": "APIs"},
            "aditi@cylist.dev",
        )
        await other_client.post(
            "/auth/accept-invite", json={"token": token, "password": INVITEE_PASSWORD}
        )
        members = (await signed_in.get("/projects/ATL/members")).json()["members"]
        owner = next(member["id"] for member in members if member["name"] == OWNER_NAME)
        await signed_in.put("/projects/ATL/members", json={"person_ids": [owner, person["id"]]})
        await signed_in.put(
            "/projects/ATL/permissions/everyone-else",
            json={"permissions": [Permission.FILES.value]},
        )

        read = await other_client.get(f"/docs/{doc['id']}")
        written = await other_client.patch(f"/docs/{doc['id']}", json={"body": "mine now"})
        filed = await other_client.post(
            f"/doc-topics/{topic['id']}/docs", json={"title": "Another"}
        )
        headed = await other_client.post(
            "/projects/ATL/doc-topics", json={"section": "product", "name": "Mine"}
        )

        assert read.status_code == 200
        assert written.status_code == 403
        assert filed.status_code == 403
        assert headed.status_code == 403
