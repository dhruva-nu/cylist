"""A project's skills.

*A skill is a file, and re-uploading one replaces it.* Files refuse a duplicate
name, because two things called ``report.pdf`` in a folder is data loss waiting
to be reported. A skill is the opposite case: the name **is** the skill, and
uploading ``board-tidy.md`` again means you have a newer version of it. So the
unique index on (project, name) is honoured by replacing the row's content
rather than by raising a 409 the uploader would only resolve by deleting the
old one first.

"""

from __future__ import annotations

from typing import NamedTuple
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, UnprocessableRequestError
from app.models.agent import Skill
from app.models.project import Project
from app.schemas.agents import SkillUpdate
from app.services import blobs
from app.services.files import ensure_person_exists, filename_of
from app.storage import BlobStore


class Counts(NamedTuple):
    """What a project holds for its agents, for its hub card."""

    skills: int


# --- Skills ----------------------------------------------------------------


async def get_skill(session: AsyncSession, skill_id: UUID) -> Skill:
    """Fetch one skill, 404-ing if nothing matches."""
    skill = await session.get(Skill, skill_id)
    if skill is None:
        raise NotFoundError("No such skill.", details={"skill_id": str(skill_id)})
    return skill


async def list_skills(session: AsyncSession, project: Project) -> list[Skill]:
    """Every skill on the project, by name.

    By name rather than by upload time: this is a list of what an agent can be
    given, and it is read to find one. Recency is the wrong order for a
    reference — it moves the entry you are looking for every time somebody
    uploads something else.
    """
    rows = await session.scalars(
        select(Skill).where(Skill.project_id == project.id).order_by(Skill.name)
    )
    return list(rows)


async def upload_skill(
    session: AsyncSession,
    store: BlobStore,
    project: Project,
    source: UploadFile,
    *,
    description: str | None,
    max_bytes: int,
    added_by: UUID | None,
) -> tuple[Skill, bool]:
    """Put a skill on the project, or replace the one of that name.

    Returns the skill and whether it replaced an existing one, so the caller
    can record the right activity and answer 201 or 200.

    Raises:
        PayloadTooLargeError: if the content exceeds ``max_bytes``.
        UnprocessableRequestError: if the upload has no usable filename, or
            ``added_by`` names nobody in the directory.
    """
    name = filename_of(source)
    await ensure_person_exists(session, added_by)

    existing = await session.scalar(
        select(Skill).where(Skill.project_id == project.id, Skill.name == name)
    )

    blob, mime = await blobs.store_upload(session, store, source, max_bytes=max_bytes)

    if existing is not None:
        # The bytes it used to hold may now be unreferenced. Read the old id
        # before overwriting it, and sweep after the new one is committed to
        # the row — the two can be the same blob, and collecting first would
        # delete content the replacement is about to point at.
        previous = existing.blob_id
        # The relationship rather than the id: the size a response reports is
        # read off it, and an id alone would leave the old blob attached.
        existing.blob = blob
        existing.mime = mime
        existing.added_by = added_by
        if description is not None:
            existing.description = description
        await session.flush()
        await blobs.collect_garbage(session, store, {previous})
        await session.refresh(existing, ["added_by_person"])
        return existing, True

    skill = Skill(
        project_id=project.id,
        name=name,
        description=description,
        blob=blob,
        mime=mime,
        added_by=added_by,
    )
    session.add(skill)
    try:
        await session.flush()
    except IntegrityError as exc:
        # Lost the race for the name against a concurrent upload. Retrying
        # would be the other caller's replacement anyway, so say so plainly.
        raise UnprocessableRequestError(
            f"A skill called {name!r} was uploaded at the same time. Try again.",
            details={"name": name},
        ) from exc
    await session.refresh(skill, ["added_by_person"])
    return skill, False


async def update_skill(session: AsyncSession, skill: Skill, data: SkillUpdate) -> Skill:
    """Change a skill's description, leaving its content alone."""
    fields = data.model_dump(exclude_unset=True)
    if "description" in fields:
        skill.description = fields["description"]
    await session.flush()
    return skill


async def delete_skill(session: AsyncSession, store: BlobStore, skill: Skill) -> None:
    """Remove a skill, and its content if nothing else refers to it."""
    blob_id = skill.blob_id
    await session.delete(skill)
    await session.flush()
    await blobs.collect_garbage(session, store, {blob_id})


# --- Counts ----------------------------------------------------------------


async def counts(session: AsyncSession, project: Project) -> Counts:
    """How many skills the project holds, for its hub card."""
    skills = await session.scalar(
        select(func.count()).select_from(Skill).where(Skill.project_id == project.id)
    )
    return Counts(skills=skills or 0)
