"""Files: folders, uploads, links, downloads and what happens when they go."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import Database
from app.models import Blob, FileItem, Folder, ItemKind, ItemSource, Project
from app.services.files import MAX_DEPTH
from tests.conftest import client_for, sign_in

ATLAS = {"key": "ATL", "name": "Atlas Billing Migration"}
HERMES = {"key": "HRM", "name": "Hermes Notifications"}
UPLOADER = {
    "name": "Aditi K",
    "kind": "team",
    "role": "Backend engineer",
    "responsibilities": "Payments and webhooks.",
}
UNKNOWN_ID = "00000000-0000-7000-8000-000000000000"

BRIEF = b"%PDF-1.7 the atlas billing brief, restated"
NUMBERS = b"the quarterly numbers, restated"


def content_of(label: str) -> bytes:
    """Bytes unique to one test.

    The store is content-addressed and the data directory outlives a single
    test, so two tests uploading ``b"hello"`` would be looking at one blob.
    """
    return f"cylist test fixture: {label}\n".encode() * 8


def blob_path(settings: Settings, content: bytes) -> Path:
    """Where the store would put this content, whether or not it is there."""
    digest = hashlib.sha256(content).hexdigest()
    return settings.blob_dir / digest[0:2] / digest[2:4] / digest


async def root_id(client: AsyncClient, key: str = "ATL") -> str:
    """The project's root folder, which every tree is hung from."""
    response = await client.get(f"/projects/{key}/tree")
    assert response.status_code == 200, response.text
    folder_id: str = response.json()["id"]
    return folder_id


async def make_folder(client: AsyncClient, name: str, parent_id: str | None = None) -> str:
    body = {"name": name, "parent_id": parent_id}
    response = await client.post("/projects/ATL/folders", json=body)
    assert response.status_code == 201, response.text
    folder_id: str = response.json()["id"]
    return folder_id


async def upload(
    client: AsyncClient,
    folder_id: str,
    name: str,
    content: bytes,
    *,
    mime: str = "application/pdf",
    added_by: str | None = None,
) -> Response:
    return await client.post(
        f"/folders/{folder_id}/upload",
        files={"file": (name, content, mime)},
        data={"added_by": added_by} if added_by else None,
    )


@pytest.fixture
async def project(signed_in: AsyncClient) -> AsyncClient:
    """A signed-in client with one project to hang folders off."""
    await signed_in.post("/projects", json=ATLAS)
    return signed_in


@pytest.fixture
async def capped(
    settings: Settings, database: Database, tmp_path: Path
) -> AsyncIterator[AsyncClient]:
    """A client whose app refuses anything over a megabyte.

    Its own data directory, so the assertions about what is left on disk after
    a refusal see only this test's doing.
    """
    limited = settings.model_copy(update={"max_upload_mb": 1, "data_dir": tmp_path})
    async with client_for(limited, database) as http:
        await sign_in(http)
        await http.post("/projects", json=ATLAS)
        yield http


class TestCreatingFolders:
    async def test_creates_one_in_the_root(self, project: AsyncClient) -> None:
        """No parent named means the project's root, not no parent at all."""
        response = await project.post("/projects/ATL/folders", json={"name": "Architecture"})

        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "Architecture"
        assert body["parent_id"] == await root_id(project)
        assert body["is_root"] is False

    async def test_creates_one_inside_another(self, project: AsyncClient) -> None:
        parent = await make_folder(project, "Finance inputs")

        body = (
            await project.post("/projects/ATL/folders", json={"name": "2025", "parent_id": parent})
        ).json()

        assert body["parent_id"] == parent

    async def test_the_project_can_be_named_by_key(self, project: AsyncClient) -> None:
        """So an agent can call /projects/ATL/folders without resolving a UUID."""
        response = await project.post("/projects/atl/folders", json={"name": "Exports"})

        assert response.status_code == 201

    async def test_refuses_two_folders_with_one_name_in_a_parent(
        self, project: AsyncClient
    ) -> None:
        parent = await make_folder(project, "Finance inputs")
        await make_folder(project, "2025", parent)

        response = await project.post(
            "/projects/ATL/folders", json={"name": "2025", "parent_id": parent}
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"

    async def test_refuses_two_folders_with_one_name_in_the_root(
        self, project: AsyncClient
    ) -> None:
        await make_folder(project, "Exports")

        assert (
            await project.post("/projects/ATL/folders", json={"name": "Exports"})
        ).status_code == 409

    async def test_the_same_name_in_two_parents_is_fine(self, project: AsyncClient) -> None:
        first = await make_folder(project, "Finance inputs")
        second = await make_folder(project, "Architecture")

        await make_folder(project, "2025", first)

        assert (
            await project.post("/projects/ATL/folders", json={"name": "2025", "parent_id": second})
        ).status_code == 201

    async def test_rejects_a_name_that_is_a_path(self, project: AsyncClient) -> None:
        assert (
            await project.post("/projects/ATL/folders", json={"name": "../secrets"})
        ).status_code == 422

    async def test_an_unknown_parent_is_a_404(self, project: AsyncClient) -> None:
        response = await project.post(
            "/projects/ATL/folders", json={"name": "2025", "parent_id": UNKNOWN_ID}
        )

        assert response.status_code == 404

    async def test_refuses_a_parent_in_another_project(self, project: AsyncClient) -> None:
        await project.post("/projects", json=HERMES)
        elsewhere = (
            await project.post("/projects/HRM/folders", json={"name": "Templates"})
        ).json()["id"]

        response = await project.post(
            "/projects/ATL/folders", json={"name": "2025", "parent_id": elsewhere}
        )

        assert response.status_code == 422


class TestTheRoot:
    async def test_a_new_project_has_one(self, project: AsyncClient) -> None:
        tree = (await project.get("/projects/ATL/tree")).json()

        assert tree["is_root"] is True
        assert tree["parent_id"] is None
        assert tree["children"] == []

    async def test_is_named_after_the_project(self, project: AsyncClient) -> None:
        tree = (await project.get("/projects/ATL/tree")).json()

        assert tree["name"] == ATLAS["name"]

    async def test_follows_the_project_when_it_is_renamed(self, project: AsyncClient) -> None:
        """The root stands for the project, so a stale name would be a lie."""
        await project.patch("/projects/ATL", json={"name": "Atlas Billing"})

        assert (await project.get("/projects/ATL/tree")).json()["name"] == "Atlas Billing"

    async def test_takes_files_directly(self, project: AsyncClient) -> None:
        """The point of the whole exercise: a README beside the tree, not in it."""
        response = await upload(project, await root_id(project), "README.md", content_of("readme"))

        assert response.status_code == 201
        assert response.json()["folder_id"] == await root_id(project)

    async def test_takes_links_directly(self, project: AsyncClient) -> None:
        response = await project.post(
            f"/folders/{await root_id(project)}/links",
            json={
                "name": "Project brief",
                "url": "https://docs.google.com/document/d/atlas-brief",
                "source": "gdrive",
            },
        )

        assert response.status_code == 201

    async def test_cannot_be_renamed(self, project: AsyncClient) -> None:
        response = await project.patch(
            f"/folders/{await root_id(project)}", json={"name": "Something else"}
        )

        assert response.status_code == 422
        message = response.json()["error"]["message"]
        assert "stands for the project" in message
        assert "Rename the project" in message

    async def test_cannot_be_moved(self, project: AsyncClient) -> None:
        elsewhere = await make_folder(project, "Architecture")

        response = await project.patch(
            f"/folders/{await root_id(project)}", json={"parent_id": elsewhere}
        )

        assert response.status_code == 422

    async def test_cannot_be_deleted(self, project: AsyncClient) -> None:
        response = await project.delete(f"/folders/{await root_id(project)}")

        assert response.status_code == 422
        assert "cannot be deleted" in response.json()["error"]["message"]

    async def test_a_refused_delete_leaves_it_there(self, project: AsyncClient) -> None:
        before = await root_id(project)

        await project.delete(f"/folders/{before}")

        assert await root_id(project) == before

    async def test_each_project_gets_its_own(self, project: AsyncClient) -> None:
        await project.post("/projects", json=HERMES)

        assert await root_id(project, "ATL") != await root_id(project, "HRM")

    async def test_the_database_permits_only_one_per_project(
        self, project: AsyncClient, session: AsyncSession
    ) -> None:
        """The rule is an index, not a convention the service remembers."""
        project_id = UUID((await project.get("/projects/ATL")).json()["id"])

        session.add(Folder(project_id=project_id, parent_id=None, name="Impostor"))
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


class TestTheTree:
    async def test_starts_at_the_root(self, project: AsyncClient) -> None:
        finance = await make_folder(project, "Finance inputs")
        await make_folder(project, "2025", finance)
        await make_folder(project, "Architecture")

        tree = (await project.get("/projects/ATL/tree")).json()

        assert [node["name"] for node in tree["children"]] == ["Architecture", "Finance inputs"]
        assert [node["name"] for node in tree["children"][1]["children"]] == ["2025"]

    async def test_comes_back_in_one_call_however_deep(self, project: AsyncClient) -> None:
        first = await make_folder(project, "One")
        second = await make_folder(project, "Two", first)
        await make_folder(project, "Three", second)

        tree = (await project.get("/projects/ATL/tree")).json()

        assert tree["children"][0]["children"][0]["children"][0]["name"] == "Three"


class TestFolderContents:
    async def test_lists_subfolders_and_items(self, project: AsyncClient) -> None:
        folder = await make_folder(project, "Finance inputs")
        await make_folder(project, "2025", folder)
        await upload(project, folder, "vat.xlsx", content_of("vat"))
        await project.post(
            f"/folders/{folder}/links",
            json={"name": "Tax portal", "url": "https://tax.example/atlas", "source": "other"},
        )

        body = (await project.get(f"/folders/{folder}/children")).json()

        assert [f["name"] for f in body["folders"]] == ["2025"]
        assert [i["name"] for i in body["items"]] == ["Tax portal", "vat.xlsx"]

    async def test_gives_the_breadcrumb_to_the_folder(self, project: AsyncClient) -> None:
        first = await make_folder(project, "Finance inputs")
        second = await make_folder(project, "2025", first)

        body = (await project.get(f"/folders/{second}/children")).json()

        assert [crumb["name"] for crumb in body["path"]] == [
            ATLAS["name"],
            "Finance inputs",
            "2025",
        ]

    async def test_an_unknown_folder_is_a_clean_404(self, project: AsyncClient) -> None:
        response = await project.get(f"/folders/{UNKNOWN_ID}/children")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


class TestMovingFolders:
    async def test_renames(self, project: AsyncClient) -> None:
        folder = await make_folder(project, "Finance")

        body = (await project.patch(f"/folders/{folder}", json={"name": "Finance inputs"})).json()

        assert body["name"] == "Finance inputs"

    async def test_moves_into_another_folder(self, project: AsyncClient) -> None:
        parent = await make_folder(project, "Finance inputs")
        child = await make_folder(project, "2025")

        body = (await project.patch(f"/folders/{child}", json={"parent_id": parent})).json()

        assert body["parent_id"] == parent

    async def test_moves_back_into_the_root(self, project: AsyncClient) -> None:
        """An explicit null means the root; omitting it leaves the parent alone."""
        parent = await make_folder(project, "Finance inputs")
        child = await make_folder(project, "2025", parent)

        body = (await project.patch(f"/folders/{child}", json={"parent_id": None})).json()

        assert body["parent_id"] == await root_id(project)

    async def test_renaming_does_not_move(self, project: AsyncClient) -> None:
        parent = await make_folder(project, "Finance inputs")
        child = await make_folder(project, "2025", parent)

        body = (await project.patch(f"/folders/{child}", json={"name": "2026"})).json()

        assert body["parent_id"] == parent

    async def test_refuses_to_put_a_folder_inside_itself(self, project: AsyncClient) -> None:
        folder = await make_folder(project, "Finance inputs")

        response = await project.patch(f"/folders/{folder}", json={"parent_id": folder})

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "unprocessable"

    async def test_refuses_to_put_a_folder_inside_its_own_subtree(
        self, project: AsyncClient
    ) -> None:
        """Left to the database this would detach the whole branch from the project."""
        grandparent = await make_folder(project, "One")
        parent = await make_folder(project, "Two", grandparent)
        child = await make_folder(project, "Three", parent)

        response = await project.patch(f"/folders/{grandparent}", json={"parent_id": child})

        assert response.status_code == 422

    async def test_a_refused_move_leaves_the_tree_alone(self, project: AsyncClient) -> None:
        grandparent = await make_folder(project, "One")
        parent = await make_folder(project, "Two", grandparent)

        await project.patch(f"/folders/{grandparent}", json={"parent_id": parent})

        tree = (await project.get("/projects/ATL/tree")).json()
        assert [node["name"] for node in tree["children"]] == ["One"]

    async def test_refuses_a_folder_nested_deeper_than_the_limit(
        self, project: AsyncClient
    ) -> None:
        """The cap is what makes every walk up the tree terminate."""
        parent: str | None = None
        # One short of the cap: the root already occupies the first level.
        for level in range(MAX_DEPTH - 1):
            parent = await make_folder(project, f"level-{level}", parent)

        response = await project.post(
            "/projects/ATL/folders", json={"name": "too deep", "parent_id": parent}
        )

        assert response.status_code == 422

    async def test_refuses_a_move_onto_a_taken_name(self, project: AsyncClient) -> None:
        parent = await make_folder(project, "Finance inputs")
        await make_folder(project, "2025", parent)
        stray = await make_folder(project, "2025")

        assert (
            await project.patch(f"/folders/{stray}", json={"parent_id": parent})
        ).status_code == 409


class TestUploading:
    async def test_stores_a_file(self, project: AsyncClient) -> None:
        folder = await make_folder(project, "Architecture")

        response = await upload(project, folder, "brief.pdf", BRIEF)

        assert response.status_code == 201
        body = response.json()
        assert body["kind"] == "file"
        assert body["name"] == "brief.pdf"
        assert body["source"] == "upload"
        assert body["size"] == len(BRIEF)
        assert body["mime"] == "application/pdf"

    async def test_writes_the_content_to_the_blob_store(
        self, project: AsyncClient, settings: Settings
    ) -> None:
        folder = await make_folder(project, "Architecture")
        content = content_of("stored on disk")

        await upload(project, folder, "brief.pdf", content)

        assert blob_path(settings, content).read_bytes() == content

    async def test_records_who_uploaded_it(self, project: AsyncClient) -> None:
        folder = await make_folder(project, "Architecture")
        person = (await project.post("/people", json=UPLOADER)).json()["id"]

        body = (await upload(project, folder, "brief.pdf", BRIEF, added_by=person)).json()

        assert body["added_by"]["name"] == "Aditi K"

    async def test_nobody_in_particular_uploaded_it_by_default(self, project: AsyncClient) -> None:
        """An API token is not a person, so attribution has to be given, not guessed."""
        folder = await make_folder(project, "Architecture")

        assert (await upload(project, folder, "brief.pdf", BRIEF)).json()["added_by"] is None

    async def test_refuses_an_uploader_who_is_not_in_the_directory(
        self, project: AsyncClient
    ) -> None:
        folder = await make_folder(project, "Architecture")

        response = await upload(project, folder, "brief.pdf", BRIEF, added_by=UNKNOWN_ID)

        assert response.status_code == 422

    async def test_strips_any_directory_from_the_filename(self, project: AsyncClient) -> None:
        folder = await make_folder(project, "Architecture")

        body = (await upload(project, folder, "Downloads/../../etc/passwd", BRIEF)).json()

        assert body["name"] == "passwd"

    async def test_refuses_a_second_file_with_the_same_name(self, project: AsyncClient) -> None:
        folder = await make_folder(project, "Architecture")
        await upload(project, folder, "brief.pdf", BRIEF)

        response = await upload(project, folder, "brief.pdf", content_of("different bytes"))

        assert response.status_code == 409

    async def test_the_same_name_in_two_folders_is_fine(self, project: AsyncClient) -> None:
        first = await make_folder(project, "Architecture")
        second = await make_folder(project, "Exports")
        await upload(project, first, "brief.pdf", BRIEF)

        assert (await upload(project, second, "brief.pdf", BRIEF)).status_code == 201


class TestDeduplication:
    async def test_the_same_bytes_twice_are_stored_once(
        self, project: AsyncClient, settings: Settings
    ) -> None:
        folder = await make_folder(project, "Architecture")
        content = content_of("uploaded twice")

        first = await upload(project, folder, "brief.pdf", content)
        second = await upload(project, folder, "brief-copy.pdf", content)

        assert first.status_code == 201
        assert second.status_code == 201
        assert blob_path(settings, content).is_file()

    async def test_one_blob_row_serves_both_items(
        self, project: AsyncClient, session: AsyncSession
    ) -> None:
        """The saving is a row as well as a file: two items, one blob."""
        folder = await make_folder(project, "Architecture")
        content = content_of("one blob, two items")

        await upload(project, folder, "brief.pdf", content)
        await upload(project, folder, "brief-copy.pdf", content)

        assert await session.scalar(select(func.count()).select_from(Blob)) == 1
        assert await session.scalar(select(func.count()).select_from(FileItem)) == 2

    async def test_both_items_point_at_one_blob(self, project: AsyncClient) -> None:
        """Proved through the API: deleting one leaves the other downloadable."""
        folder = await make_folder(project, "Architecture")
        content = content_of("shared between two items")
        first = (await upload(project, folder, "brief.pdf", content)).json()
        second = (await upload(project, folder, "brief-copy.pdf", content)).json()

        await project.delete(f"/items/{first['id']}")

        download = await project.get(f"/items/{second['id']}/download")
        assert download.content == content


class TestTheUploadCap:
    async def test_refuses_a_file_over_the_limit(self, capped: AsyncClient) -> None:
        folder = await make_folder(capped, "Exports")

        response = await upload(capped, folder, "dump.zip", b"x" * (2 * 1024 * 1024))

        assert response.status_code == 413
        assert response.json()["error"]["code"] == "payload_too_large"

    async def test_accepts_a_file_under_the_limit(self, capped: AsyncClient) -> None:
        folder = await make_folder(capped, "Exports")

        assert (await upload(capped, folder, "small.zip", b"x" * 1000)).status_code == 201

    async def test_a_refused_upload_leaves_nothing_on_disk(
        self, capped: AsyncClient, tmp_path: Path
    ) -> None:
        folder = await make_folder(capped, "Exports")
        content = b"x" * (2 * 1024 * 1024)

        await upload(capped, folder, "dump.zip", content)

        blobs = tmp_path / "blobs"
        assert sorted(blobs.rglob("*")) == []

    async def test_a_refused_upload_leaves_no_row(self, capped: AsyncClient) -> None:
        folder = await make_folder(capped, "Exports")

        await upload(capped, folder, "dump.zip", b"x" * (2 * 1024 * 1024))

        assert (await capped.get(f"/folders/{folder}/children")).json()["items"] == []


class TestLinks:
    async def test_adds_a_link(self, project: AsyncClient) -> None:
        folder = await make_folder(project, "Architecture")

        response = await project.post(
            f"/folders/{folder}/links",
            json={
                "name": "Project brief",
                "url": "https://docs.google.com/document/d/atlas",
                "source": "gdrive",
            },
        )

        assert response.status_code == 201
        body = response.json()
        assert body["kind"] == "link"
        assert body["source"] == "gdrive"
        assert body["size"] is None

    async def test_sits_in_the_same_folder_as_uploads(self, project: AsyncClient) -> None:
        folder = await make_folder(project, "Architecture")
        await upload(project, folder, "brief.pdf", BRIEF)
        await project.post(
            f"/folders/{folder}/links",
            json={"name": "Spec", "url": "https://example.com/spec", "source": "sharepoint"},
        )

        items = (await project.get(f"/folders/{folder}/children")).json()["items"]

        assert {item["kind"] for item in items} == {"file", "link"}

    async def test_refuses_a_link_that_claims_it_was_uploaded_here(
        self, project: AsyncClient
    ) -> None:
        folder = await make_folder(project, "Architecture")

        response = await project.post(
            f"/folders/{folder}/links",
            json={"name": "Spec", "url": "https://example.com/spec", "source": "upload"},
        )

        assert response.status_code == 422

    async def test_refuses_something_that_is_not_a_url(self, project: AsyncClient) -> None:
        folder = await make_folder(project, "Architecture")

        response = await project.post(
            f"/folders/{folder}/links", json={"name": "Spec", "url": "/etc/passwd"}
        )

        assert response.status_code == 422

    async def test_refuses_a_name_a_file_already_has(self, project: AsyncClient) -> None:
        folder = await make_folder(project, "Architecture")
        await upload(project, folder, "brief.pdf", BRIEF)

        response = await project.post(
            f"/folders/{folder}/links",
            json={"name": "brief.pdf", "url": "https://example.com/brief"},
        )

        assert response.status_code == 409


class TestDownloading:
    async def test_returns_the_bytes_that_were_uploaded(self, project: AsyncClient) -> None:
        folder = await make_folder(project, "Architecture")
        content = content_of("downloaded back")
        item = (await upload(project, folder, "brief.pdf", content)).json()

        response = await project.get(f"/items/{item['id']}/download")

        assert response.status_code == 200
        assert response.content == content

    async def test_sends_the_stored_type_and_filename(self, project: AsyncClient) -> None:
        folder = await make_folder(project, "Architecture")
        item = (await upload(project, folder, "brief.pdf", BRIEF, mime="application/pdf")).json()

        response = await project.get(f"/items/{item['id']}/download")

        assert response.headers["content-type"] == "application/pdf"
        assert "brief.pdf" in response.headers["content-disposition"]
        assert response.headers["content-disposition"].startswith("attachment")

    async def test_a_link_has_nothing_to_download(self, project: AsyncClient) -> None:
        folder = await make_folder(project, "Architecture")
        item = (
            await project.post(
                f"/folders/{folder}/links",
                json={"name": "Spec", "url": "https://example.com/spec"},
            )
        ).json()

        response = await project.get(f"/items/{item['id']}/download")

        assert response.status_code == 422


class TestUpdatingItems:
    async def test_renames_a_file(self, project: AsyncClient) -> None:
        folder = await make_folder(project, "Architecture")
        item = (await upload(project, folder, "brief.pdf", BRIEF)).json()

        body = (await project.patch(f"/items/{item['id']}", json={"name": "brief-v2.pdf"})).json()

        assert body["name"] == "brief-v2.pdf"

    async def test_corrects_a_links_target(self, project: AsyncClient) -> None:
        folder = await make_folder(project, "Architecture")
        item = (
            await project.post(
                f"/folders/{folder}/links",
                json={"name": "Spec", "url": "https://example.com/old"},
            )
        ).json()

        body = (
            await project.patch(f"/items/{item['id']}", json={"url": "https://example.com/new"})
        ).json()

        assert body["url"] == "https://example.com/new"

    async def test_an_uploaded_file_has_no_url_to_change(self, project: AsyncClient) -> None:
        folder = await make_folder(project, "Architecture")
        item = (await upload(project, folder, "brief.pdf", BRIEF)).json()

        response = await project.patch(
            f"/items/{item['id']}", json={"url": "https://example.com/elsewhere"}
        )

        assert response.status_code == 422

    async def test_refuses_a_rename_onto_a_taken_name(self, project: AsyncClient) -> None:
        folder = await make_folder(project, "Architecture")
        await upload(project, folder, "brief.pdf", BRIEF)
        other = (await upload(project, folder, "notes.pdf", content_of("notes"))).json()

        response = await project.patch(f"/items/{other['id']}", json={"name": "brief.pdf"})

        assert response.status_code == 409


class TestDeletingItems:
    async def test_removes_the_row(self, project: AsyncClient) -> None:
        folder = await make_folder(project, "Architecture")
        item = (await upload(project, folder, "brief.pdf", BRIEF)).json()

        assert (await project.delete(f"/items/{item['id']}")).status_code == 200
        assert (await project.get(f"/folders/{folder}/children")).json()["items"] == []

    async def test_takes_the_content_off_the_disk_with_it(
        self, project: AsyncClient, settings: Settings
    ) -> None:
        folder = await make_folder(project, "Architecture")
        content = content_of("deleted with its item")
        item = (await upload(project, folder, "brief.pdf", content)).json()

        await project.delete(f"/items/{item['id']}")

        assert not blob_path(settings, content).exists()

    async def test_keeps_content_another_item_still_needs(
        self, project: AsyncClient, settings: Settings
    ) -> None:
        folder = await make_folder(project, "Architecture")
        content = content_of("shared, so kept")
        first = (await upload(project, folder, "brief.pdf", content)).json()
        await upload(project, folder, "brief-copy.pdf", content)

        await project.delete(f"/items/{first['id']}")

        assert blob_path(settings, content).is_file()

    async def test_a_link_leaves_no_content_to_clean_up(self, project: AsyncClient) -> None:
        folder = await make_folder(project, "Architecture")
        item = (
            await project.post(
                f"/folders/{folder}/links",
                json={"name": "Spec", "url": "https://example.com/spec"},
            )
        ).json()

        assert (await project.delete(f"/items/{item['id']}")).status_code == 200


class TestDeletingFolders:
    async def test_takes_its_items_with_it(self, project: AsyncClient) -> None:
        folder = await make_folder(project, "Architecture")
        item = (await upload(project, folder, "brief.pdf", BRIEF)).json()

        await project.delete(f"/folders/{folder}")

        assert (await project.get(f"/items/{item['id']}")).status_code == 404

    async def test_takes_its_subfolders_with_it(self, project: AsyncClient) -> None:
        parent = await make_folder(project, "Finance inputs")
        child = await make_folder(project, "2025", parent)
        await make_folder(project, "Q1", child)

        await project.delete(f"/folders/{parent}")

        assert (await project.get("/projects/ATL/tree")).json()["children"] == []

    async def test_cleans_the_content_out_of_the_store(
        self, project: AsyncClient, settings: Settings
    ) -> None:
        parent = await make_folder(project, "Finance inputs")
        child = await make_folder(project, "2025", parent)
        content = content_of("deep in a deleted tree")
        await upload(project, child, "vat.xlsx", content)

        await project.delete(f"/folders/{parent}")

        assert not blob_path(settings, content).exists()

    async def test_keeps_content_a_file_elsewhere_still_needs(
        self, project: AsyncClient, settings: Settings
    ) -> None:
        doomed = await make_folder(project, "Exports")
        kept = await make_folder(project, "Architecture")
        content = content_of("uploaded into two folders")
        await upload(project, doomed, "brief.pdf", content)
        survivor = (await upload(project, kept, "brief.pdf", content)).json()

        await project.delete(f"/folders/{doomed}")

        assert blob_path(settings, content).is_file()
        assert (await project.get(f"/items/{survivor['id']}/download")).content == content

    async def test_leaves_the_rest_of_the_tree_alone(self, project: AsyncClient) -> None:
        await make_folder(project, "Architecture")
        doomed = await make_folder(project, "Exports")

        await project.delete(f"/folders/{doomed}")

        tree = (await project.get("/projects/ATL/tree")).json()
        assert [node["name"] for node in tree["children"]] == ["Architecture"]


class TestProjectSummary:
    async def test_counts_folders_and_everything_in_them(self, project: AsyncClient) -> None:
        first = await make_folder(project, "Architecture")
        await make_folder(project, "Exports")
        await upload(project, first, "brief.pdf", BRIEF)
        await project.post(
            f"/folders/{first}/links",
            json={"name": "Spec", "url": "https://example.com/spec"},
        )

        summary = (await project.get("/projects/ATL/summary")).json()

        assert summary["folder_count"] == 2
        assert summary["file_count"] == 2

    async def test_is_zero_for_an_untouched_project(self, project: AsyncClient) -> None:
        """The root is not counted: every project has one, so it says nothing."""
        summary = (await project.get("/projects/ATL/summary")).json()

        assert summary["folder_count"] == 0
        assert summary["file_count"] == 0

    async def test_counts_files_sitting_in_the_root(self, project: AsyncClient) -> None:
        await upload(project, await root_id(project), "README.md", content_of("root readme"))

        summary = (await project.get("/projects/ATL/summary")).json()

        assert summary["folder_count"] == 0
        assert summary["file_count"] == 1

    async def test_counts_only_this_projects_files(self, project: AsyncClient) -> None:
        await project.post("/projects", json=HERMES)
        elsewhere = (
            await project.post("/projects/HRM/folders", json={"name": "Templates"})
        ).json()["id"]
        await upload(project, elsewhere, "welcome.html", content_of("hermes"))

        assert (await project.get("/projects/ATL/summary")).json()["file_count"] == 0


class TestAuditTrail:
    async def test_records_every_change(self, project: AsyncClient) -> None:
        folder = await make_folder(project, "Architecture")
        item = (await upload(project, folder, "brief.pdf", BRIEF)).json()
        await project.post(
            f"/folders/{folder}/links",
            json={"name": "Spec", "url": "https://example.com/spec"},
        )
        await project.patch(f"/items/{item['id']}", json={"name": "brief-v2.pdf"})
        await project.delete(f"/items/{item['id']}")
        await project.delete(f"/folders/{folder}")

        project_id = (await project.get("/projects/ATL")).json()["id"]
        entries = (await project.get("/activity", params={"project_id": project_id})).json()

        assert {entry["verb"] for entry in entries} == {
            "project.created",
            "folder.created",
            "file.uploaded",
            "link.added",
            "item.updated",
            "item.deleted",
            "folder.deleted",
        }

    async def test_reading_a_folder_records_nothing(self, project: AsyncClient) -> None:
        folder = await make_folder(project, "Architecture")

        await project.get(f"/folders/{folder}/children")
        await project.get("/projects/ATL/tree")

        project_id = (await project.get("/projects/ATL")).json()["id"]
        entries = (await project.get("/activity", params={"project_id": project_id})).json()
        assert [entry["verb"] for entry in entries] == ["folder.created", "project.created"]


class TestTheSchema:
    """Constraints the API upholds, proved where they are actually enforced.

    These reach past the routes deliberately. A rule that only lives in Python
    is a rule a future endpoint — or a migration script — can forget.
    """

    async def test_a_file_must_have_content(
        self, project: AsyncClient, session: AsyncSession
    ) -> None:
        folder_id = UUID(await make_folder(project, "Architecture"))

        session.add(
            FileItem(
                folder_id=folder_id,
                kind=ItemKind.FILE,
                name="hollow.pdf",
                source=ItemSource.UPLOAD,
            )
        )

        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

    async def test_a_link_must_not_have_content(
        self, project: AsyncClient, session: AsyncSession
    ) -> None:
        folder_id = UUID(await make_folder(project, "Architecture"))
        blob = Blob(sha256="0" * 64, size=1, mime="text/plain", path="00/00/" + "0" * 64)
        session.add(blob)
        await session.flush()

        session.add(
            FileItem(
                folder_id=folder_id,
                kind=ItemKind.LINK,
                name="confused",
                url="https://example.com/spec",
                blob_id=blob.id,
                source=ItemSource.OTHER,
            )
        )

        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

    async def test_deleting_a_project_takes_its_folders_with_it(
        self, project: AsyncClient, session: AsyncSession
    ) -> None:
        """Projects are archived, not deleted, but the foreign key must be sound."""
        await make_folder(project, "Architecture")
        project_id = UUID((await project.get("/projects/ATL")).json()["id"])

        row = await session.get(Project, project_id)
        assert row is not None
        await session.delete(row)
        await session.flush()

        assert list(await session.scalars(select(Folder))) == []
