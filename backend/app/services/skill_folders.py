"""A skill as the folder Claude Code reads it from.

Claude Code finds a skill at ``.claude/skills/<name>/SKILL.md``, in the repo
it was started in or under the user's own ``~/.claude``. What a project holds
is not that shape: somebody
uploads ``board-tidy.md``, or ``cylist.zip`` with a ``cylist/`` folder inside
it. This module turns either into the folder, once, on the server — so the
CLI that writes it to disk and the MCP tool that hands it to a model agree on
what a skill looks like installed, instead of each carrying its own unzip.

*A single file becomes ``<name>/SKILL.md``.* Its frontmatter gets a ``name``
and ``description`` if it lacks them, taken from the file's own name and the
description it was uploaded with — which is how Claude Code decides when the
skill is relevant, so a skill uploaded without frontmatter would otherwise sit
in the folder unused.

*A zip is unpacked as the folder it already is.* One wrapping directory is
looked through, since that is what zipping a folder produces. It has to have a
``SKILL.md`` at its root after that; one that does not is refused rather than
guessed at, because a guess that picks the wrong markdown file installs a
skill that looks right and does the wrong job.

*A folder can also arrive a file at a time*, which is what a browser's
directory picker and ``cylist skills push`` send. :func:`gather` applies the
same rules to those parts and :func:`pack` zips them, so what is stored is
still one file and the rest of Cylist — the blob store, the download, the
listing — does not have to learn what a multi-file skill is. ``gather`` then
``pack`` then :func:`unpack` returns the files that were sent, byte for byte.

**Nothing here trusts the archive.** An entry that climbs out of the folder
(``../``, an absolute path, a drive letter) or is a symlink is refused, and
the unpacked size and file count are capped before anything is decompressed,
so a zip bomb costs a 422 rather than the server's memory. The CLI checks the
paths again before writing: the server being right is no reason for a client
to write wherever it is told.
"""

from __future__ import annotations

import json
import re
import stat
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath

from app.core.errors import UnprocessableRequestError

SKILL_FILE = "SKILL.md"

MAX_FILES = 200
"""More than a skill has any business holding. A folder past this is a
repository someone zipped by mistake."""

MAX_UNPACKED_BYTES = 20 * 1024 * 1024
"""The whole folder, decompressed. Checked against what the archive declares
before reading, and again while reading, because a declaration can lie."""

NAME_MAX = 64
"""Claude Code's own limit on a skill's name."""

UPLOADED_FOLDER = "the uploaded folder"
"""What a refusal calls a folder that arrived a file at a time and was not
named. A zip is refused by its filename; parts of a folder have no one name
to quote, and ``folder/SKILL.md`` would be the wrong thing to blame."""

ZIP_SUFFIX = ".zip"

ZIP_MIME = "application/zip"

ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
"""The one date :func:`pack` stamps every entry with — the earliest a zip can
carry. The honest alternative is the moment of packing, which would give the
same folder a different digest on every upload and so defeat the
content-addressed store: re-uploading an unchanged skill would cost a second
copy on disk and a pointless garbage collection of the first."""

IGNORED = re.compile(r"(^|/)(__MACOSX/|\.DS_Store$|Thumbs\.db$)")
"""What an operating system adds to a zip on its own. Not part of the skill,
and a ``__MACOSX/`` beside the real folder would otherwise hide that the
archive has a single root to look through."""

FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(?P<body>.*?)^---[ \t]*(?:\r?\n|\Z)", re.S | re.M)
TOP_LEVEL_KEY = re.compile(r"^(?P<key>[A-Za-z0-9_-]+)[ \t]*:(?P<value>.*)$")


@dataclass(frozen=True)
class FolderFile:
    """One file in the folder, at a path relative to it."""

    path: str
    data: bytes
    executable: bool = False


@dataclass(frozen=True)
class SkillFolder:
    """What to write under ``.claude/skills/``: a directory name and its files."""

    name: str
    files: list[FolderFile]


def unpack(filename: str, description: str | None, content: bytes) -> SkillFolder:
    """Turn a stored skill into the folder Claude Code would load it from.

    Raises:
        UnprocessableRequestError: if the skill cannot be made into one — a
            zip without a ``SKILL.md``, an entry that escapes the folder, or a
            single file that is not text.
    """
    if filename.lower().endswith(".zip") or zipfile.is_zipfile(BytesIO(content)):
        return _from_zip(filename, description, content)
    return _from_document(filename, description, content)


def slug(text: str) -> str:
    """A name Claude Code accepts: lowercase letters, digits and hyphens."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")
    return cleaned[:NAME_MAX].rstrip("-")


# --- A single document -----------------------------------------------------


def _from_document(filename: str, description: str | None, content: bytes) -> SkillFolder:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UnprocessableRequestError(
            f"{filename!r} is neither text nor a zip, so it cannot be installed as a skill.",
            details={"name": filename},
        ) from exc

    name = _folder_name(text, fallbacks=(_stem(filename),))
    skill = _with_frontmatter(text, name=name, description=description)
    return SkillFolder(name=name, files=[FolderFile(SKILL_FILE, skill.encode())])


# --- A zipped folder -------------------------------------------------------


def _from_zip(filename: str, description: str | None, content: bytes) -> SkillFolder:
    try:
        archive = zipfile.ZipFile(BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise _refuse(repr(filename), "it is not a readable zip") from exc

    subject = repr(filename)
    entries: list[FolderFile] = []
    with archive:
        infos = [
            info
            for info in archive.infolist()
            if not info.is_dir() and not IGNORED.search(info.filename)
        ]
        _within_the_caps(subject, len(infos), sum(info.file_size for info in infos))

        budget = MAX_UNPACKED_BYTES
        for info in infos:
            path = _checked_path(subject, info.filename)
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise _refuse(subject, f"its entry {info.filename!r} is a symlink")
            with archive.open(info) as handle:
                # One byte past what is left, so a declared size that lied is
                # caught by the read rather than believed.
                data = handle.read(budget + 1)
            budget -= len(data)
            if budget < 0:
                raise _refuse(subject, f"it unpacks to more than {_megabytes(MAX_UNPACKED_BYTES)}")
            entries.append(
                FolderFile(path=str(path), data=data, executable=bool(mode & stat.S_IXUSR))
            )

    return _assemble(subject, description, entries, fallbacks=(_stem(filename),))


# --- A folder sent a file at a time ----------------------------------------


def gather(
    parts: Sequence[FolderFile], description: str | None, *, folder: str | None = None
) -> SkillFolder:
    """The folder these uploaded parts make, laid out the way a zip's is.

    Each part's ``path`` is the one it had inside what the uploader picked —
    ``cylist/SKILL.md``, ``cylist/scripts/setup.sh``. That is what a browser's
    directory picker reports as ``webkitRelativePath`` and what
    ``cylist skills push`` builds by walking a directory. ``folder`` is the
    name the uploader gave that directory; it names the folder in a refusal
    and is the last fallback for the skill's own name.

    The rules are a zip's, deliberately: one wrapping directory is looked
    through, a root ``SKILL.md`` is required, nothing may point outside the
    folder, and the same two caps apply. A folder that uploads is a folder
    that downloads.

    Raises:
        UnprocessableRequestError: if these parts are not a skill's folder.
    """
    subject = repr(folder) if folder else UPLOADED_FOLDER
    kept = [part for part in parts if not IGNORED.search(part.path)]
    if not kept:
        raise _refuse(subject, "it has no files in it")
    _within_the_caps(subject, len(kept), sum(len(part.data) for part in kept))

    checked = [
        FolderFile(
            path=str(_checked_path(subject, part.path)),
            data=part.data,
            executable=part.executable,
        )
        for part in kept
    ]
    return _assemble(subject, description, checked, fallbacks=(folder or "",))


def pack(folder: SkillFolder) -> bytes:
    """A folder as the zip to store it as — the inverse of :func:`unpack`.

    Deterministic, so the same folder always hashes to the same blob: the
    files in the order the folder holds them, one fixed timestamp
    (:data:`ZIP_TIMESTAMP`), and a mode that says only whether the file is
    executable. See :data:`ZIP_TIMESTAMP` for why the clock is kept out of it.
    """
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in folder.files:
            info = zipfile.ZipInfo(file.path, date_time=ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100755 if file.executable else 0o100644) << 16
            archive.writestr(info, file.data)
    return buffer.getvalue()


# --- What both ways in share -----------------------------------------------


def _assemble(
    subject: str,
    description: str | None,
    entries: list[FolderFile],
    *,
    fallbacks: tuple[str, ...],
) -> SkillFolder:
    """Entries at the paths they came at, as the folder to install.

    The wrapping directory comes off, ``SKILL.md`` sorts first, and its
    frontmatter gets the ``name`` and ``description`` Claude Code reads.
    """
    root = _root_of(subject, [PurePosixPath(entry.path) for entry in entries])
    files = [
        FolderFile(
            path="/".join(PurePosixPath(entry.path).parts[len(root) :]),
            data=entry.data,
            executable=entry.executable,
        )
        for entry in entries
    ]
    files.sort(key=lambda one: (one.path.casefold() != SKILL_FILE.casefold(), one.path))

    manifest = next(one for one in files if one.path.casefold() == SKILL_FILE.casefold())
    try:
        text = manifest.data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise _refuse(subject, f"its {SKILL_FILE} is not UTF-8 text") from exc

    name = _folder_name(text, fallbacks=(root[0] if root else "", *fallbacks))
    rewritten = FolderFile(
        SKILL_FILE, _with_frontmatter(text, name=name, description=description).encode()
    )
    return SkillFolder(name=name, files=[rewritten if one is manifest else one for one in files])


def _within_the_caps(subject: str, count: int, size: int) -> None:
    """Refuse a folder that is too many files or too many bytes."""
    if count > MAX_FILES:
        raise _refuse(subject, f"it holds more than {MAX_FILES} files")
    if size > MAX_UNPACKED_BYTES:
        raise _refuse(subject, f"it unpacks to more than {_megabytes(MAX_UNPACKED_BYTES)}")


def _checked_path(subject: str, raw: str) -> PurePosixPath:
    """The file's path inside the folder, or a refusal if it would leave it."""
    cleaned = raw.replace("\\", "/")
    parts = [part for part in cleaned.split("/") if part not in ("", ".")]
    escapes = (
        cleaned.startswith("/")
        or ".." in parts
        or (parts and re.match(r"^[A-Za-z]:", parts[0]) is not None)
        or not parts
    )
    if escapes:
        raise _refuse(subject, f"its entry {raw!r} points outside the skill's folder")
    return PurePosixPath(*parts)


def _root_of(subject: str, paths: list[PurePosixPath]) -> tuple[str, ...]:
    """The directory inside the upload that is the skill's folder.

    The upload's own root if ``SKILL.md`` is there; otherwise the one
    directory everything is in, which is what zipping or picking a folder
    gives you.
    """
    at_root = {path.name.casefold() for path in paths if len(path.parts) == 1}
    if SKILL_FILE.casefold() in at_root:
        return ()

    tops = {path.parts[0] for path in paths}
    if len(tops) == 1 and all(len(path.parts) > 1 for path in paths):
        (top,) = tops
        inside = {path.parts[1].casefold() for path in paths if len(path.parts) == 2}
        if SKILL_FILE.casefold() in inside:
            return (top,)

    raise _refuse(
        subject,
        f"it has no {SKILL_FILE} at its root, which is the file Claude Code loads a skill from",
    )


def _refuse(subject: str, why: str) -> UnprocessableRequestError:
    """Say what cannot be installed and why.

    ``subject`` is rendered already — a quoted filename, or a phrase for an
    upload that has no single name — because the two ways in name what they
    are refusing differently.
    """
    return UnprocessableRequestError(
        f"{subject} cannot be installed as a skill: {why}.", details={"name": subject}
    )


def _megabytes(count: int) -> str:
    return f"{count // (1024 * 1024)} MB"


# --- Frontmatter -----------------------------------------------------------


def _folder_name(text: str, *, fallbacks: tuple[str, ...]) -> str:
    """The skill's own ``name`` if it gives one, else the first usable fallback."""
    for candidate in (_frontmatter(text).get("name", ""), *fallbacks):
        if name := slug(candidate):
            return name
    return "skill"


def _frontmatter(text: str) -> dict[str, str]:
    """The top-level ``key: value`` pairs of a YAML header, as plain strings.

    Not a YAML parser, and it does not need to be: the only keys read are
    ``name`` and ``description``, which are one-line scalars, and anything
    nested is left exactly as it was written.
    """
    match = FRONTMATTER.match(text)
    if match is None:
        return {}
    pairs: dict[str, str] = {}
    for line in match.group("body").splitlines():
        found = TOP_LEVEL_KEY.match(line)
        if found:
            pairs[found.group("key")] = found.group("value").strip().strip("'\"")
    return pairs


def _with_frontmatter(text: str, *, name: str, description: str | None) -> str:
    """The text with ``name`` and ``description`` in its header.

    A key the author wrote is kept, except a ``name`` Claude Code would not
    accept, which is replaced by the folder's. Values are written as JSON
    strings, which YAML reads as double-quoted scalars — so a description with
    a colon in it stays one value.
    """
    present = _frontmatter(text)
    own = present.get("name")
    replace_name = own is not None and slug(own) != own
    added: list[str] = []
    if own is None or replace_name:
        added.append(f"name: {json.dumps(name)}")
    if "description" not in present and description and description.strip():
        added.append(f"description: {json.dumps(' '.join(description.split()))}")

    match = FRONTMATTER.match(text)
    if match is None:
        return "---\n" + "".join(f"{line}\n" for line in added) + "---\n\n" + text

    kept = match.group("body").splitlines(keepends=True)
    if replace_name:
        kept = [line for line in kept if not re.match(r"^name[ \t]*:", line)]
    header = "---\n" + "".join(f"{line}\n" for line in added) + "".join(kept) + "---\n"
    return header + text[match.end() :]


def _stem(filename: str) -> str:
    return PurePosixPath(filename).stem
