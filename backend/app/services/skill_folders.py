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
        raise _refuse(filename, "it is not a readable zip") from exc

    with archive:
        entries = [
            info
            for info in archive.infolist()
            if not info.is_dir() and not IGNORED.search(info.filename)
        ]
        if len(entries) > MAX_FILES:
            raise _refuse(filename, f"it holds more than {MAX_FILES} files")
        if sum(info.file_size for info in entries) > MAX_UNPACKED_BYTES:
            raise _refuse(filename, f"it unpacks to more than {_megabytes(MAX_UNPACKED_BYTES)}")

        paths = {info.filename: _safe_path(filename, info) for info in entries}
        root = _root_of(filename, list(paths.values()))

        files: list[FolderFile] = []
        budget = MAX_UNPACKED_BYTES
        for info in entries:
            relative = paths[info.filename].parts[len(root) :]
            with archive.open(info) as handle:
                data = handle.read(budget + 1)
            budget -= len(data)
            if budget < 0:
                raise _refuse(filename, f"it unpacks to more than {_megabytes(MAX_UNPACKED_BYTES)}")
            mode = info.external_attr >> 16
            files.append(
                FolderFile(
                    path="/".join(relative),
                    data=data,
                    executable=bool(mode & stat.S_IXUSR),
                )
            )

    files.sort(key=lambda one: (one.path.casefold() != SKILL_FILE.casefold(), one.path))
    manifest = next(one for one in files if one.path.casefold() == SKILL_FILE.casefold())
    try:
        text = manifest.data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise _refuse(filename, f"its {SKILL_FILE} is not UTF-8 text") from exc

    name = _folder_name(text, fallbacks=(root[0] if root else "", _stem(filename)))
    rewritten = FolderFile(
        SKILL_FILE, _with_frontmatter(text, name=name, description=description).encode()
    )
    files = [rewritten if one is manifest else one for one in files]
    return SkillFolder(name=name, files=files)


def _safe_path(filename: str, info: zipfile.ZipInfo) -> PurePosixPath:
    """The entry's path inside the folder, or a refusal if it would leave it."""
    raw = info.filename.replace("\\", "/")
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    escapes = (
        raw.startswith("/")
        or ".." in parts
        or (parts and re.match(r"^[A-Za-z]:", parts[0]) is not None)
        or not parts
    )
    if escapes:
        raise _refuse(filename, f"its entry {info.filename!r} points outside the skill's folder")
    if stat.S_ISLNK(info.external_attr >> 16):
        raise _refuse(filename, f"its entry {info.filename!r} is a symlink")
    return PurePosixPath(*parts)


def _root_of(filename: str, paths: list[PurePosixPath]) -> tuple[str, ...]:
    """The directory inside the archive that is the skill's folder.

    The archive's own root if ``SKILL.md`` is there; otherwise the one
    directory everything is in, which is what zipping a folder gives you.
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
        filename,
        f"it has no {SKILL_FILE} at its root, which is the file Claude Code loads a skill from",
    )


def _refuse(filename: str, why: str) -> UnprocessableRequestError:
    return UnprocessableRequestError(
        f"{filename!r} cannot be installed as a skill: {why}.", details={"name": filename}
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
