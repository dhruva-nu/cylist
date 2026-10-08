"""A project's skills.

*A skill is one stored file, and re-uploading one replaces it.* Files refuse a duplicate
name, because two things called ``report.pdf`` in a folder is data loss waiting
to be reported. A skill is the opposite case: the name **is** the skill, and
uploading ``board-tidy.md`` again means you have a newer version of it. So the
unique index on (project, name) is honoured by replacing the row's content
rather than by raising a 409 the uploader would only resolve by deleting the
old one first.

*A skill is usually more than one file*, though — a ``SKILL.md`` beside the
scripts and references it tells an agent to use — and a folder of them is
uploaded a part at a time. The folder is laid out and zipped here, by
:mod:`app.services.skill_folders`, so that one skill is still one row and one
blob: the listing, the download, the garbage collector and the ``/folder``
route all go on working unchanged, and the zip they are given is the same
shape as a zip somebody uploaded by hand.
"""

from __future__ import annotations

from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from typing import NamedTuple
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, PayloadTooLargeError, UnprocessableRequestError
from app.models.agent import Skill
from app.models.file import Blob
from app.models.project import Project
from app.schemas.agents import SkillUpdate
from app.services import blobs, skill_folders
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
    sources: Sequence[UploadFile],
    *,
    description: str | None,
    folder: str | None = None,
    executable: AbstractSet[str] = frozenset(),
    max_bytes: int,
    added_by: UUID | None,
) -> tuple[Skill, bool]:
    """Put a skill on the project, or replace the one of that name.

    ``sources`` is one file, or the parts of a folder — :func:`_stored_as`
    decides which and is where the difference ends. Returns the skill and
    whether it replaced an existing one, so the caller can record the right
    activity and answer 201 or 200.

    Raises:
        PayloadTooLargeError: if the content exceeds ``max_bytes``.
        UnprocessableRequestError: if the upload has no usable filename, if
            the parts are not a skill's folder, or if ``added_by`` names
            nobody in the directory.
    """
    await ensure_person_exists(session, added_by)

    name, blob, mime = await _stored_as(
        session,
        store,
        sources,
        description=description,
        folder=folder,
        executable=executable,
        max_bytes=max_bytes,
    )

    existing = await session.scalar(
        select(Skill).where(Skill.project_id == project.id, Skill.name == name)
    )

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


async def _stored_as(
    session: AsyncSession,
    store: BlobStore,
    sources: Sequence[UploadFile],
    *,
    description: str | None,
    folder: str | None,
    executable: AbstractSet[str],
    max_bytes: int,
) -> tuple[str, Blob, str]:
    """The name, bytes and type to file this upload under.

    **One part and no ``folder``** is a single-file skill, stored exactly as
    it was sent and under its own filename. That is what a skill has always
    been, and one uploaded as ``board-tidy.md`` must keep downloading as that
    markdown rather than as a zip of it.

    **Anything else is a folder**: several parts, or one part sent together
    with the name of the folder it came out of. Each part's filename is the
    path it had inside that folder, the lot is laid out and zipped, and the
    skill is named ``<folder>.zip``. Storing the zip rather than a row per
    file is what keeps a skill one blob — and it is the same archive somebody
    would have uploaded had they zipped the folder by hand, so
    ``/skills/{id}/folder`` unpacks it with no second code path.

    Raises:
        PayloadTooLargeError: if the parts together exceed ``max_bytes``.
        UnprocessableRequestError: if nothing was sent, or the parts are not
            a skill's folder.
    """
    if not sources:
        raise UnprocessableRequestError("Send the skill's file, or a folder's files.")

    if len(sources) == 1 and folder is None:
        source = sources[0]
        blob, mime = await blobs.store_upload(session, store, source, max_bytes=max_bytes)
        return filename_of(source), blob, mime

    bodies = await _read_all(sources, max_bytes)
    parts = [
        skill_folders.FolderFile(
            path=source.filename or "",
            data=data,
            executable=(source.filename or "") in executable,
        )
        for source, data in zip(sources, bodies, strict=True)
    ]
    laid_out = skill_folders.gather(parts, description, folder=folder)
    blob = await blobs.store_bytes(
        session, store, skill_folders.pack(laid_out), max_bytes=max_bytes
    )
    return f"{laid_out.name}{skill_folders.ZIP_SUFFIX}", blob, skill_folders.ZIP_MIME


async def _read_all(sources: Sequence[UploadFile], max_bytes: int) -> list[bytes]:
    """Every part's bytes, refused as soon as they pass the upload limit.

    A folder has to be in memory to be zipped, so it is read against the same
    cap a single upload is streamed against rather than measured after the
    fact. :data:`~app.services.skill_folders.MAX_UNPACKED_BYTES` caps it
    again, lower, once the parts are known to be a folder.
    """
    read: list[bytes] = []
    total = 0
    for source in sources:
        body = bytearray()
        while chunk := await source.read(blobs.CHUNK_BYTES):
            total += len(chunk)
            if total > max_bytes:
                raise PayloadTooLargeError(
                    f"That folder is larger than the {max_bytes // (1024 * 1024)} MB upload limit.",
                    details={"max_bytes": max_bytes},
                )
            body += chunk
        read.append(bytes(body))
    return read


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
