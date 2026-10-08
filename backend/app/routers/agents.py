"""What a project's agents work from: its skills.

Skills are addressed under their project when they are listed and uploaded,
and by their own id once they exist — a skill keeps its download URL.
"""

from __future__ import annotations

import base64
from uuid import UUID

from anyio import to_thread
from fastapi import APIRouter, Depends, File, Form, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require
from app.auth.permissions import Permission
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.config import Settings, app_settings
from app.core.errors import NotFoundError
from app.db import SessionDependency
from app.models.agent import Skill
from app.models.project import Project
from app.routers import guards
from app.routers.projects import resolved_project
from app.schemas.agents import (
    SkillFolderFile,
    SkillFolderRead,
    SkillRead,
    SkillUpdate,
)
from app.schemas.common import Acknowledged
from app.schemas.people import PersonRead
from app.services import activity, agents, blobs, skill_folders
from app.storage import BlobStore, get_blob_store

router = APIRouter(tags=["agents"])


async def resolved_skill(
    skill_id: UUID,
    session: AsyncSession = SessionDependency,
) -> Skill:
    """Turn the path segment into a skill, 404-ing if nothing matches."""
    return await agents.get_skill(session, skill_id)


def _skill_read(skill: Skill) -> SkillRead:
    person = skill.added_by_person
    return SkillRead(
        id=skill.id,
        project_id=skill.project_id,
        name=skill.name,
        description=skill.description,
        size=skill.size,
        mime=skill.mime,
        added_by=PersonRead.model_validate(person) if person is not None else None,
        created_at=skill.created_at,
    )


# --- Skills ----------------------------------------------------------------


WRITE_AGENT_KIT = guards.on_project(Permission.AGENTS)
"""What this project's agents work from — its skills."""

WRITE_THIS_SKILL = guards.for_entity(Permission.AGENTS, resolved_skill)


@router.get(
    "/projects/{project_ref}/skills",
    response_model=list[SkillRead],
    summary="List a project's skills",
)
async def list_skills(
    project: Project = Depends(resolved_project),
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
) -> list[SkillRead]:
    """Every skill uploaded to this project, alphabetical.

    A skill is a packaged job an agent can be handed. Download one with
    `/skills/{skill_id}/download`, or as the folder Claude Code loads it from
    with `/skills/{skill_id}/folder`.
    """
    return [_skill_read(skill) for skill in await agents.list_skills(session, project)]


@router.post(
    "/projects/{project_ref}/skills",
    response_model=SkillRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a skill, as one file or as a folder",
    responses={
        200: {"description": "A skill of that name already existed and was replaced."},
        413: {"description": "The upload is larger than the configured limit."},
        422: {"description": "The parts sent are not a skill's folder — see the message."},
    },
)
async def upload_skill(
    response: Response,
    project: Project = Depends(resolved_project),
    principal: Principal = Depends(WRITE_AGENT_KIT),
    session: AsyncSession = SessionDependency,
    store: BlobStore = Depends(get_blob_store),
    settings: Settings = Depends(app_settings),
    file: list[UploadFile] = File(
        description=(
            "The skill. One part for a single-file skill, or one part per file "
            "for a folder — each named with the path it has inside the folder, "
            "'cylist/SKILL.md', 'cylist/scripts/setup.sh'."
        )
    ),
    folder: str | None = Form(
        default=None,
        description=(
            "The name of the folder being uploaded. Send it and every part is "
            "read as a path inside a folder, even if there is only one."
        ),
    ),
    executable: list[str] = Form(
        default=[],
        description=(
            "Which parts to mark executable, by the same path, repeated once each. "
            "A browser cannot know; a client walking a directory can."
        ),
    ),
    description: str | None = Form(default=None, description="One line on what the skill does."),
    added_by: UUID | None = Form(default=None, description="Which person is uploading it."),
) -> SkillRead:
    """Upload a skill as `multipart/form-data`.

    **A skill can be a whole folder.** A skill is usually a `SKILL.md` beside
    the scripts and references it points an agent at, so send one `file` part
    per file, each named with its path inside the folder, and `folder` with
    the folder's own name. The server lays it out, checks it the way it checks
    an uploaded zip — a root `SKILL.md`, nothing pointing outside the folder,
    at most 200 files and 20 MB — and stores it as `<folder>.zip`, which
    `/skills/{skill_id}/folder` unpacks again file for file. A single part with
    no `folder` is stored exactly as it was sent, as it always was.

    **Uploading a name that already exists replaces it** and answers 200 rather
    than 201 — the name is the skill, so sending `board-tidy.md` again means
    you have a newer version of it, not that you have a conflict. This is the
    one place Cylist's upload rules differ from the file tree's, where a
    duplicate name is refused.

    Content is addressed by its SHA-256 and shared with the file store, so
    uploading bytes the server already holds costs a row and no disk.
    """
    skill, replaced = await agents.upload_skill(
        session,
        store,
        project,
        file,
        description=description,
        folder=folder,
        executable=set(executable),
        max_bytes=settings.max_upload_bytes,
        added_by=added_by,
    )
    if replaced:
        response.status_code = status.HTTP_200_OK
    await activity.record(
        session,
        principal,
        "skill.replaced" if replaced else "skill.uploaded",
        entity_type="skill",
        entity_id=skill.id,
        project_id=project.id,
        payload={"name": skill.name, "size": skill.size, "files": len(file)},
    )
    return _skill_read(skill)


@router.get("/skills/{skill_id}", response_model=SkillRead, summary="Get a skill")
async def get_skill(
    skill: Skill = Depends(resolved_skill),
    _: Principal = Depends(require(Scope.READ)),
) -> SkillRead:
    """One skill's details, without its content."""
    return _skill_read(skill)


@router.patch("/skills/{skill_id}", response_model=SkillRead, summary="Describe a skill")
async def update_skill(
    body: SkillUpdate,
    skill: Skill = Depends(resolved_skill),
    principal: Principal = Depends(WRITE_THIS_SKILL),
    session: AsyncSession = SessionDependency,
) -> SkillRead:
    """Change a skill's description. Its content is replaced by uploading it again."""
    updated = await agents.update_skill(session, skill, body)
    await activity.record(
        session,
        principal,
        "skill.updated",
        entity_type="skill",
        entity_id=updated.id,
        project_id=updated.project_id,
        payload={"fields": sorted(body.model_dump(exclude_unset=True))},
    )
    return _skill_read(updated)


@router.delete("/skills/{skill_id}", response_model=Acknowledged, summary="Delete a skill")
async def delete_skill(
    skill: Skill = Depends(resolved_skill),
    principal: Principal = Depends(WRITE_THIS_SKILL),
    session: AsyncSession = SessionDependency,
    store: BlobStore = Depends(get_blob_store),
) -> Acknowledged:
    """Take a skill off the project. Its content goes with it unless a file shares it."""
    name, project_id = skill.name, skill.project_id
    await agents.delete_skill(session, store, skill)
    await activity.record(
        session,
        principal,
        "skill.deleted",
        entity_type="skill",
        entity_id=skill.id,
        project_id=project_id,
        payload={"name": name},
    )
    return Acknowledged()


@router.get(
    "/skills/{skill_id}/download",
    summary="Download a skill",
    response_class=FileResponse,
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def download_skill(
    skill: Skill = Depends(resolved_skill),
    _: Principal = Depends(require(Scope.READ)),
    store: BlobStore = Depends(get_blob_store),
) -> FileResponse:
    """The skill's own bytes, under the name it was uploaded with."""
    if not await store.exists(skill.blob.path):
        raise NotFoundError(
            "This skill's content is missing from the store.", details={"name": skill.name}
        )

    return FileResponse(
        store.locate(skill.blob.path),
        media_type=skill.mime or blobs.DEFAULT_MIME,
        filename=skill.name,
    )


@router.get(
    "/skills/{skill_id}/folder",
    response_model=SkillFolderRead,
    summary="Get a skill as the folder Claude Code loads",
    responses={422: {"description": "The skill cannot be laid out as one — see the message."}},
)
async def skill_folder(
    skill: Skill = Depends(resolved_skill),
    _: Principal = Depends(require(Scope.READ)),
    store: BlobStore = Depends(get_blob_store),
) -> SkillFolderRead:
    """The skill as the files to write under `.claude/skills/<folder>/`.

    A markdown skill becomes `<folder>/SKILL.md`, with `name` and
    `description` added to its frontmatter where it has none. A zip is
    unpacked — through one wrapping directory, if that is where its
    `SKILL.md` is — and refused if it has no `SKILL.md`, holds a path that
    leaves the folder, or unpacks to more than a skill should.

    `/download` is still the skill exactly as it was uploaded; this is the
    same bytes, laid out to be used.
    """
    if not await store.exists(skill.blob.path):
        raise NotFoundError(
            "This skill's content is missing from the store.", details={"name": skill.name}
        )

    content = await to_thread.run_sync(store.locate(skill.blob.path).read_bytes)
    folder = await to_thread.run_sync(skill_folders.unpack, skill.name, skill.description, content)
    return SkillFolderRead(
        skill=_skill_read(skill),
        folder=folder.name,
        files=[_folder_file(folder_file) for folder_file in folder.files],
    )


def _folder_file(folder_file: skill_folders.FolderFile) -> SkillFolderFile:
    try:
        content, encoding = folder_file.data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        content, encoding = base64.b64encode(folder_file.data).decode("ascii"), "base64"
    return SkillFolderFile(
        path=folder_file.path,
        encoding=encoding,
        content=content,
        size=len(folder_file.data),
        executable=folder_file.executable,
    )
