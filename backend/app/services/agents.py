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

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import NamedTuple
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import multipart
from app.core.errors import NotFoundError, PayloadTooLargeError, UnprocessableRequestError
from app.models.agent import Skill
from app.models.file import Blob
from app.models.project import Project
from app.services import blobs, people, skill_folders
from app.services.commands.agents import SkillUpdate
from app.services.files import filename_of
from app.services.paging import PageRequest, Window, keyset
from app.services.skill_folders import MAX_UNPACKED_BYTES
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


async def list_skills(session: AsyncSession, project: Project, page: PageRequest) -> Window[Skill]:
    """A page of the skills on the project, by name.

    By name rather than by upload time: this is a list of what an agent can be
    given, and it is read to find one. Recency is the wrong order for a
    reference — it moves the entry you are looking for every time somebody
    uploads something else.
    """
    return await keyset(
        session,
        select(Skill).where(Skill.project_id == project.id),
        keys=(Skill.name, Skill.id),
        page=page,
    )


DESCRIPTION_MAX_LENGTH = 2000
"""The longest description an upload may carry — the same as
:class:`~app.schemas.agents.SkillUpdate` allows when it is changed later."""


class Received(NamedTuple):
    """What a skill upload's form came to, before it is filed."""

    name: str
    blob: Blob
    mime: str
    description: str | None
    files: int
    """How many file parts it carried — one, or a folder's worth."""


async def upload_skill(
    session: AsyncSession,
    store: BlobStore,
    project: Project,
    form: multipart.Form,
    *,
    max_bytes: int,
    added_by: UUID | None,
) -> tuple[Skill, Received, bool]:
    """Put a skill on the project, or replace the one of that name.

    The form is read here, after the route's permission checks (CYLIST-123)
    — see :func:`_received` for what it may hold. Returns the skill, what was
    received, and whether it replaced an existing one, so the caller can
    record the right activity and answer 201 or 200.

    Raises:
        PayloadTooLargeError: if the content exceeds ``max_bytes``.
        RequestValidationError: if there is no file, or a field is too long.
        UnprocessableRequestError: if the upload has no usable filename, if
            the parts are not a skill's folder, or if ``added_by`` names
            nobody in the directory.
    """
    await people.ensure_person_exists(session, added_by)

    received = await _received(session, store, form, max_bytes=max_bytes)
    name, blob, mime, description = (
        received.name,
        received.blob,
        received.mime,
        received.description,
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
        await blobs.collect_garbage(session, {previous})
        await session.refresh(existing, ["added_by_person"])
        return existing, received, True

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
    return skill, received, False


_SKILL_FIELDS = frozenset({"folder", "executable", "description"})


@dataclass
class _Held:
    """A file part read into memory, while it may still be part of a folder."""

    filename: str | None
    content_type: str | None
    data: bytes


async def _received(
    session: AsyncSession,
    store: BlobStore,
    form: multipart.Form,
    *,
    max_bytes: int,
) -> Received:
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

    Which of the two it is cannot be known until the form is over — a
    ``folder`` field may come last — so the first part is held in memory
    while it is small enough to be in a folder at all. One that outgrows
    :data:`~app.services.skill_folders.MAX_UNPACKED_BYTES` can only be a
    single file, and goes on to the blob store as it arrives instead of
    being held whole.

    Raises:
        PayloadTooLargeError: if the parts together exceed ``max_bytes``.
        RequestValidationError: if nothing was sent, or a field is too long.
        UnprocessableRequestError: if the parts are not a skill's folder.
    """
    held: list[_Held] = []
    streamed: list[tuple[str, Blob, str]] = []
    total = 0

    async def take(part: multipart.Part, before: Mapping[str, list[str]]) -> None:
        nonlocal total
        if streamed:
            raise _too_large_for_a_folder()
        chunks = part.chunks()
        # Held in memory: every part of a folder, and the first part of
        # anything until it is too big to be one. Read against the upload
        # limit across all parts, as a single file is streamed against it.
        cap = max_bytes if held or multipart.single(before, "folder") else MAX_UNPACKED_BYTES
        body = bytearray()
        async for chunk in chunks:
            total += len(chunk)
            if total > max_bytes:
                raise PayloadTooLargeError(
                    f"That folder is larger than the {max_bytes // (1024 * 1024)} MB upload limit.",
                    details={"max_bytes": max_bytes},
                )
            body += chunk
            if len(body) > cap:
                break
        else:
            held.append(_Held(part.filename, part.content_type, bytes(body)))
            return

        # Too big to be a folder's file, so it is a single file, and the rest
        # of it goes to the store as it arrives.
        name = filename_of(part.filename)
        blob = await blobs.store_stream(
            session, store, _resumed(bytes(body), chunks), max_bytes=max_bytes
        )
        streamed.append((name, blob, blobs.declared_mime(part.content_type)))

    fields = await form.read(file_field="file", on_file=take, text_fields=_SKILL_FIELDS)
    folder = multipart.single(fields, "folder")
    description = multipart.single(fields, "description")
    if description is not None and len(description) > DESCRIPTION_MAX_LENGTH:
        raise multipart.invalid_field(
            "description", f"must be at most {DESCRIPTION_MAX_LENGTH} characters"
        )

    if streamed:
        if folder is not None:
            raise _too_large_for_a_folder()
        name, blob, mime = streamed[0]
        return Received(name, blob, mime, description, files=1)

    if not held:
        raise multipart.invalid_field("file", "Field required", kind="missing")

    if len(held) == 1 and folder is None:
        only = held[0]
        name = filename_of(only.filename)
        blob = await blobs.store_bytes(session, store, only.data, max_bytes=max_bytes)
        return Received(name, blob, blobs.declared_mime(only.content_type), description, files=1)

    executable = set(fields.get("executable", []))
    parts = [
        skill_folders.FolderFile(
            path=part.filename or "",
            data=part.data,
            executable=(part.filename or "") in executable,
        )
        for part in held
    ]
    laid_out = skill_folders.gather(parts, description, folder=folder)
    blob = await blobs.store_bytes(
        session, store, skill_folders.pack(laid_out), max_bytes=max_bytes
    )
    return Received(
        f"{laid_out.name}{skill_folders.ZIP_SUFFIX}",
        blob,
        skill_folders.ZIP_MIME,
        description,
        files=len(held),
    )


async def _resumed(head: bytes, rest: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    """A part's content from where holding it in memory left off."""
    yield head
    async for chunk in rest:
        yield chunk


def _too_large_for_a_folder() -> UnprocessableRequestError:
    """A folder one of whose files is already past the folder's own cap."""
    return UnprocessableRequestError(
        f"That folder unpacks to more than "
        f"{MAX_UNPACKED_BYTES // (1024 * 1024)} MB, which is more than a skill should.",
        details={"max_bytes": MAX_UNPACKED_BYTES},
    )


async def update_skill(session: AsyncSession, skill: Skill, data: SkillUpdate) -> Skill:
    """Change a skill's description, leaving its content alone."""
    fields = data.model_dump(exclude_unset=True)
    if "description" in fields:
        skill.description = fields["description"]
    await session.flush()
    return skill


async def delete_skill(session: AsyncSession, skill: Skill) -> None:
    """Remove a skill, and its content if nothing else refers to it."""
    blob_id = skill.blob_id
    await session.delete(skill)
    await session.flush()
    await blobs.collect_garbage(session, {blob_id})


# --- Counts ----------------------------------------------------------------


async def counts(session: AsyncSession, project: Project) -> Counts:
    """How many skills the project holds, for its hub card."""
    skills = await session.scalar(
        select(func.count()).select_from(Skill).where(Skill.project_id == project.id)
    )
    return Counts(skills=skills or 0)
