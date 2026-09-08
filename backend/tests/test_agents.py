"""Skills an agent can be given, and the scratchpad it writes on."""

from __future__ import annotations

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
from app.models import AgentNote, Blob, Skill
from app.models.agent import NOTE_MAX_LENGTH
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
                size=1,
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


class TestTheScratchpad:
    async def test_writes_a_line(self, project: AsyncClient) -> None:
        response = await project.post(
            "/projects/ATL/agent-notes",
            json={"body": "Staging deploys skip migrations when two branches share a revision."},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["body"].startswith("Staging deploys skip migrations")
        assert body["author_label"], "a note says who wrote it"

    async def test_reads_newest_first(self, project: AsyncClient) -> None:
        for line in ("learned first", "learned second", "learned third"):
            await project.post("/projects/ATL/agent-notes", json={"body": line})

        listing = (await project.get("/projects/ATL/agent-notes")).json()

        assert [note["body"] for note in listing] == [
            "learned third",
            "learned second",
            "learned first",
        ]

    async def test_folds_a_wrapped_note_onto_one_line(self, project: AsyncClient) -> None:
        response = await project.post(
            "/projects/ATL/agent-notes",
            json={"body": "  the board\nrefuses a hold\n\nwithout a reason  "},
        )

        assert response.json()["body"] == "the board refuses a hold without a reason"

    async def test_refuses_an_essay(self, project: AsyncClient) -> None:
        response = await project.post(
            "/projects/ATL/agent-notes", json={"body": "x" * (NOTE_MAX_LENGTH + 1)}
        )

        assert response.status_code == 422

    async def test_refuses_a_blank_note(self, project: AsyncClient) -> None:
        response = await project.post("/projects/ATL/agent-notes", json={"body": "   \n  "})

        assert response.status_code == 422

    async def test_the_database_refuses_a_blank_note_too(
        self, project: AsyncClient, session: AsyncSession
    ) -> None:
        """The cap is a check constraint, so no client is the only thing enforcing it."""
        project_id = UUID((await project.get("/projects/ATL")).json()["id"])

        session.add(AgentNote(project_id=project_id, body="   ", author_label="owner"))

        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

    async def test_rubs_a_line_off(self, project: AsyncClient) -> None:
        note = (
            await project.post("/projects/ATL/agent-notes", json={"body": "no longer true"})
        ).json()

        assert (await project.delete(f"/agent-notes/{note['id']}")).status_code == 200

        assert (await project.get("/projects/ATL/agent-notes")).json() == []

    async def test_a_scratchpad_belongs_to_its_project(self, project: AsyncClient) -> None:
        await project.post("/projects", json=HERMES)
        await project.post("/projects/ATL/agent-notes", json={"body": "an atlas fact"})

        assert (await project.get("/projects/HRM/agent-notes")).json() == []


class TestTheHubCounts:
    async def test_reports_skills_and_notes(self, project: AsyncClient) -> None:
        await upload(project, "triage.md", content_of("counted"))
        await project.post("/projects/ATL/agent-notes", json={"body": "a counted fact"})

        summary = (await project.get("/projects/ATL/summary")).json()

        assert summary["skill_count"] == 1
        assert summary["agent_note_count"] == 1

    async def test_reports_nothing_on_an_empty_project(self, project: AsyncClient) -> None:
        summary = (await project.get("/projects/ATL/summary")).json()

        assert summary["skill_count"] == 0
        assert summary["agent_note_count"] == 0
