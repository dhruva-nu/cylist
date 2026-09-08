"""What a project's agents work from: its skills, and its scratchpad.

One router over two features, for the same reason the files router covers three
prefixes: skills are addressed under their project when they are listed and
uploaded, and by their own id once they exist — a skill keeps its download URL
— so splitting by prefix would scatter one screen across two modules.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.config import Settings, app_settings
from app.core.errors import NotFoundError
from app.db import SessionDependency
from app.models.agent import AgentNote, Skill
from app.models.project import Project
from app.routers.projects import resolved_project
from app.schemas.agents import NoteCreate, NoteRead, SkillRead, SkillUpdate
from app.schemas.common import Acknowledged
from app.schemas.people import PersonRead
from app.services import activity, agents, blobs
from app.storage import BlobStore, get_blob_store

router = APIRouter(tags=["agents"])


async def resolved_skill(
    skill_id: UUID,
    session: AsyncSession = SessionDependency,
) -> Skill:
    """Turn the path segment into a skill, 404-ing if nothing matches."""
    return await agents.get_skill(session, skill_id)


async def resolved_note(
    note_id: UUID,
    session: AsyncSession = SessionDependency,
) -> AgentNote:
    """Turn the path segment into a note, 404-ing if nothing matches."""
    return await agents.get_note(session, note_id)


def _skill(skill: Skill) -> SkillRead:
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


def _note(note: AgentNote) -> NoteRead:
    person = note.added_by_person
    return NoteRead(
        id=note.id,
        project_id=note.project_id,
        body=note.body,
        author_label=note.author_label,
        added_by=PersonRead.model_validate(person) if person is not None else None,
        created_at=note.created_at,
    )


# --- Skills ----------------------------------------------------------------


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
    `/skills/{skill_id}/download`.
    """
    return [_skill(skill) for skill in await agents.list_skills(session, project)]


@router.post(
    "/projects/{project_ref}/skills",
    response_model=SkillRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a skill",
    responses={
        200: {"description": "A skill of that name already existed and was replaced."},
        413: {"description": "The file is larger than the configured upload limit."},
    },
)
async def upload_skill(
    response: Response,
    project: Project = Depends(resolved_project),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
    store: BlobStore = Depends(get_blob_store),
    settings: Settings = Depends(app_settings),
    file: UploadFile = File(description="The skill file itself."),
    description: str | None = Form(default=None, description="One line on what the skill does."),
    added_by: UUID | None = Form(default=None, description="Which person is uploading it."),
) -> SkillRead:
    """Upload a skill as `multipart/form-data`.

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
        payload={"name": skill.name, "size": skill.size},
    )
    return _skill(skill)


@router.get("/skills/{skill_id}", response_model=SkillRead, summary="Get a skill")
async def get_skill(
    skill: Skill = Depends(resolved_skill),
    _: Principal = Depends(require(Scope.READ)),
) -> SkillRead:
    """One skill's details, without its content."""
    return _skill(skill)


@router.patch("/skills/{skill_id}", response_model=SkillRead, summary="Describe a skill")
async def update_skill(
    body: SkillUpdate,
    skill: Skill = Depends(resolved_skill),
    principal: Principal = Depends(require(Scope.WRITE)),
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
    return _skill(updated)


@router.delete("/skills/{skill_id}", response_model=Acknowledged, summary="Delete a skill")
async def delete_skill(
    skill: Skill = Depends(resolved_skill),
    principal: Principal = Depends(require(Scope.WRITE)),
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


# --- The scratchpad --------------------------------------------------------


@router.get(
    "/projects/{project_ref}/agent-notes",
    response_model=list[NoteRead],
    summary="Read a project's agent scratchpad",
)
async def list_notes(
    project: Project = Depends(resolved_project),
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
) -> list[NoteRead]:
    """The scratchpad, newest first.

    Short lines an agent wrote when it learned something about this project
    that it would otherwise have to work out again. Read this before starting
    work here.
    """
    return [_note(note) for note in await agents.list_notes(session, project)]


@router.post(
    "/projects/{project_ref}/agent-notes",
    response_model=NoteRead,
    status_code=status.HTTP_201_CREATED,
    summary="Note something learned",
)
async def add_note(
    body: NoteCreate,
    project: Project = Depends(resolved_project),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> NoteRead:
    """Write one line onto the project's scratchpad.

    For something you had to work out and the next agent would have to work
    out again — not for what the code, the board or the README already says.
    Notes are capped at 280 characters, and newlines are folded into spaces:
    one fact, in as few words as carry it.

    The note is signed with your credential's own label, so a reader can tell
    an agent's line from a person's.
    """
    note = await agents.add_note(
        session,
        project,
        body,
        author_label=principal.label,
        added_by=None,
    )
    await activity.record(
        session,
        principal,
        "agent_note.added",
        entity_type="agent_note",
        entity_id=note.id,
        project_id=project.id,
        payload={"body": note.body},
    )
    return _note(note)


@router.delete(
    "/agent-notes/{note_id}",
    response_model=Acknowledged,
    summary="Rub a line off the scratchpad",
)
async def delete_note(
    note: AgentNote = Depends(resolved_note),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> Acknowledged:
    """Delete a note, because what it says has stopped being true."""
    body, project_id = note.body, note.project_id
    await agents.delete_note(session, note)
    await activity.record(
        session,
        principal,
        "agent_note.deleted",
        entity_type="agent_note",
        entity_id=note.id,
        project_id=project_id,
        payload={"body": body},
    )
    return Acknowledged()
