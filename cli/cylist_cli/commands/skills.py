"""``cylist skills ls`` and ``cylist skills pull`` — a project's skills, installed.

``list_skills`` and ``read_skill`` let an agent *read* a skill. This puts one
where Claude Code *loads* it: ``.claude/skills/<folder>/SKILL.md`` at the root
of the repository you are in, or ``~/.claude/skills/`` with ``--user``.

When the skill turns up in a session depends on the directory, not on this
command. Claude Code watches a skills directory that existed when the session
started, and a skill written into one is usable a few seconds later. One that
did not exist yet is not watched, so what lands in it loads from the next
session. The first pull into a repository is that case, and it says so rather
than leaving an agent to find out from an "Unknown skill".

The server does the unpacking (``GET /skills/{id}/folder``): a markdown skill
arrives as a ``SKILL.md`` with its frontmatter filled in, a zip as the files
in it. What is left here is writing them down without doing damage, which
comes to three rules:

* **Nothing is written outside the skill's folder.** The server already
  refuses an archive entry that climbs out; every path is checked again here
  anyway, because a client that writes wherever it is told is only as safe as
  every server it will ever be pointed at.
* **A skill somebody wrote by hand is never replaced without ``--force``.**
  Each pulled folder carries a ``.cylist-skill.json`` saying which skill it
  is and what every file hashed to when it was written. A folder without one
  is not ours. A folder with one whose files have since changed has been
  edited by someone, and pulling over it would throw that edit away. Both are
  left alone and reported, and the command exits 1.
* **A pulled skill is a copy, and git is told so.** The folder gets a
  ``.gitignore`` of ``*``, so a copy of the board's skill does not get
  committed to a product's repository by accident and drift from the one on
  the board, which is the version that is kept up to date.

Replacing a folder is done by writing the new one beside it and swapping the
two, so an interrupted pull leaves either the old skill or the new one and
never half of each.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from cylist_cli import output
from cylist_cli.commands.hook import REFERENCE, claude_config_dir
from cylist_cli.context import Context
from cylist_cli.errors import ApiError, CylistError

MARKER = ".cylist-skill.json"
GITIGNORE = ".gitignore"
IGNORE_EVERYTHING = (
    "# Pulled from Cylist by 'cylist skills pull'. The board holds the original.\n*\n"
)

FOLDER_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
"""What Claude Code accepts as a skill's name, and so as its folder."""

NAME_WIDTH = 32
DESCRIPTION_WIDTH = 60


def register(subparsers: Any) -> None:
    skills = subparsers.add_parser(
        "skills",
        help="A project's skills, and installing them where Claude Code finds them.",
    )
    actions = skills.add_subparsers(dest="action", required=True, metavar="<action>")

    ls = actions.add_parser("ls", help="List a project's skills.")
    ls.add_argument("project", nargs="?", metavar="PROJECT", help=_PROJECT_HELP)
    ls.set_defaults(handler=_list)

    pull = actions.add_parser(
        "pull",
        help="Install a project's skills where Claude Code loads them.",
        description=(
            "Writes each skill to .claude/skills/<name>/ at the root of the repository "
            "you are in (or this directory, outside one), unpacking a zip and giving a "
            "markdown skill the SKILL.md Claude Code looks for. A running session sees "
            "it if that directory already existed when the session started, otherwise "
            "the next one does. Running it again updates what it pulled before, and "
            "leaves alone any skill it did not write or that has been edited since."
        ),
    )
    pull.add_argument("project", nargs="?", metavar="PROJECT", help=_PROJECT_HELP)
    pull.add_argument(
        "names",
        nargs="*",
        metavar="NAME",
        help="Just these skills, as 'skills ls' names them. Default: every one.",
    )
    where = pull.add_mutually_exclusive_group()
    where.add_argument(
        "--user",
        action="store_true",
        help="Into ~/.claude/skills, for every session on this machine, not just this repo.",
    )
    where.add_argument(
        "--dest",
        metavar="DIR",
        help="Into this skills directory instead. Each skill becomes a folder inside it.",
    )
    pull.add_argument(
        "--force",
        action="store_true",
        help="Replace a skill folder even if it was written by hand or edited since.",
    )
    pull.set_defaults(handler=_pull)


_PROJECT_HELP = (
    "Project key, e.g. ATL. Defaults to the project of the card this session is "
    "working on (CYLIST_TASK), so inside 'cylist work' it can be left out."
)


# --- ls --------------------------------------------------------------------


def _list(args: argparse.Namespace, ctx: Context) -> None:
    project = _project(args.project)
    skills = ctx.client.get(f"/projects/{project}/skills")
    if ctx.as_json:
        output.emit_json(skills)
        return
    output.table(
        ["NAME", "SIZE", "DESCRIPTION"],
        [
            [
                output.truncate(str(skill["name"]), NAME_WIDTH),
                output.human_size(skill.get("size")),
                output.truncate(str(skill.get("description") or ""), DESCRIPTION_WIDTH),
            ]
            for skill in skills
        ],
        empty=f"{project} has no skills yet. Upload one on its Agents page.",
    )


# --- pull ------------------------------------------------------------------


@dataclass
class Outcome:
    """What happened to one skill."""

    skill: str
    folder: str
    result: str
    """``installed``, ``updated``, ``unchanged``, ``skipped`` or ``failed``."""
    path: Path
    reason: str = ""


def _pull(args: argparse.Namespace, ctx: Context) -> None:
    project = _project(args.project)
    destination = _destination(args)
    skills = ctx.client.get(f"/projects/{project}/skills")
    chosen = _choose(skills, list(args.names), project)
    if not chosen:
        output.echo(f"{project} has no skills to pull.")
        return

    # Claude Code only watches a skills directory that was there when the
    # session began, so whether this one is new is worth saying.
    new_directory = not destination.is_dir()
    outcomes: list[Outcome] = []
    for skill in chosen:
        # One skill the server cannot lay out — a zip with no SKILL.md — is
        # reported beside the rest rather than stopping them.
        try:
            folder = ctx.client.get(f"/skills/{skill['id']}/folder")
        except ApiError as exc:
            outcomes.append(
                Outcome(str(skill["name"]), "-", "failed", destination, reason=exc.message)
            )
            continue
        outcomes.append(_install(project, folder, destination, force=bool(args.force)))

    failed = [one for one in outcomes if one.result == "failed"]
    skipped = [one for one in outcomes if one.result == "skipped"]
    if ctx.as_json:
        output.emit_json(
            {
                "destination": str(destination),
                "new_directory": new_directory,
                "skills": [
                    {
                        "skill": one.skill,
                        "folder": one.folder,
                        "result": one.result,
                        "path": str(one.path),
                        "reason": one.reason or None,
                    }
                    for one in outcomes
                ],
            }
        )
    else:
        output.table(
            ["SKILL", "FOLDER", "RESULT"],
            [
                [one.skill, one.folder, f"{one.result}: {one.reason}" if one.reason else one.result]
                for one in outcomes
            ],
        )
        if len(failed) + len(skipped) < len(outcomes):
            output.echo()
            output.echo(f"Claude Code loads these from {destination}.")
            if new_directory:
                output.echo(
                    "That directory is new, so a Claude Code session already running here "
                    "will not see it; the next one started will. Until then, follow the "
                    "skill's SKILL.md from there."
                )

    if skipped:
        noun = "skill was" if len(skipped) == 1 else "skills were"
        raise CylistError(
            f"{len(skipped)} {noun} left alone. Pass --force to replace "
            + ", ".join(str(one.path) for one in skipped)
            + ".",
            details={"skipped": [str(one.path) for one in skipped]},
        )
    if failed:
        raise CylistError(
            "Could not pull " + ", ".join(one.skill for one in failed) + ".",
            details={"failed": [one.skill for one in failed]},
        )


def _project(given: str | None) -> str:
    """The project named, or the one the bound card is on."""
    if given:
        return given
    task = os.environ.get("CYLIST_TASK", "").strip().upper()
    if REFERENCE.match(task):
        return task.split("-", 1)[0]
    raise CylistError("Name the project, e.g. 'cylist skills pull ATL'.")


def _destination(args: argparse.Namespace) -> Path:
    """The skills directory the folders go into."""
    if args.dest:
        return Path(args.dest).expanduser().resolve()
    if args.user:
        return claude_config_dir() / "skills"
    return _repository_root(Path.cwd()) / ".claude" / "skills"


def _repository_root(start: Path) -> Path:
    """The top of the git repository ``start`` is in, or ``start`` itself.

    The top rather than here, because Claude Code reads ``.claude/skills``
    from the directory it was started in and every parent up to the root of
    the repository — so the root is the one place every session in it sees.
    """
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return start


def _choose(skills: list[dict[str, Any]], names: list[str], project: str) -> list[dict[str, Any]]:
    """The skills asked for, by the name ``ls`` shows or that name less its extension."""
    if not names:
        return skills
    chosen: list[dict[str, Any]] = []
    for name in names:
        match = [
            skill
            for skill in skills
            if name in (skill["name"], PurePosixPath(str(skill["name"])).stem)
        ]
        if len(match) != 1:
            known = ", ".join(str(skill["name"]) for skill in skills) or "none"
            what = "more than one skill" if match else "no skill"
            raise CylistError(f"{project} has {what} called {name!r}. It has: {known}.")
        chosen.append(match[0])
    return chosen


def _install(project: str, folder: dict[str, Any], destination: Path, *, force: bool) -> Outcome:
    skill = folder["skill"]
    name = str(folder.get("folder", ""))
    if not FOLDER_NAME.match(name):
        raise CylistError(
            f"The server gave {skill['name']!r} the folder {name!r}, which is not a skill "
            "name Claude Code accepts. Nothing was written."
        )

    files = {_checked_path(skill["name"], one["path"]): one for one in folder["files"]}
    contents = {path: _decoded(one) for path, one in files.items()}
    hashes = {path: _sha256(data) for path, data in contents.items()}
    target = destination / name
    outcome = Outcome(skill=str(skill["name"]), folder=name, result="installed", path=target)

    if target.exists() or target.is_symlink():
        ours = _marker(target)
        reason = _why_not_ours(target, ours, skill)
        if reason and not force:
            outcome.result, outcome.reason = "skipped", reason
            return outcome
        if not reason and ours is not None and ours.get("files") == hashes:
            outcome.result = "unchanged"
            return outcome
        outcome.result = "updated"

    marker = {
        "project": project,
        "skill_id": skill["id"],
        "name": skill["name"],
        "pulled_from": skill.get("created_at"),
        "files": hashes,
    }
    _write_folder(target, files, contents, marker)
    return outcome


def _why_not_ours(target: Path, ours: dict[str, Any] | None, skill: dict[str, Any]) -> str:
    """Why this folder may not be written over, or ``""`` if it may."""
    if target.is_symlink() or not target.is_dir():
        return "something that is not a pulled skill is already there"
    if ours is None:
        return "a skill that was not pulled from Cylist is already there"
    if ours.get("skill_id") != skill["id"]:
        return f"it holds {ours.get('project')}'s {ours.get('name')!r}, not this skill"
    if _local_files(target) != ours.get("files"):
        return "it has been edited since it was pulled"
    return ""


def _checked_path(skill: str, raw: str) -> str:
    """The file's path inside the folder, refused if it would be anywhere else."""
    bad = (
        not raw
        or "\\" in raw
        or PurePosixPath(raw).is_absolute()
        or any(part in ("..", ".", "") for part in raw.split("/"))
        or re.match(r"^[A-Za-z]:", raw) is not None
        or raw == MARKER
    )
    if bad:
        raise CylistError(f"{skill!r} has a file at {raw!r}, which is not inside its folder.")
    return raw


def _decoded(one: dict[str, Any]) -> bytes:
    content = str(one.get("content", ""))
    if one.get("encoding") == "base64":
        return base64.b64decode(content)
    return content.encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _marker(target: Path) -> dict[str, Any] | None:
    try:
        found = json.loads((target / MARKER).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return found if isinstance(found, dict) else None


def _local_files(target: Path) -> dict[str, str]:
    """What the folder holds now, hashed the way the marker records it."""
    found: dict[str, str] = {}
    for path in target.rglob("*"):
        relative = path.relative_to(target).as_posix()
        if relative == MARKER or path.is_dir():
            continue
        if relative == GITIGNORE and path.read_text("utf-8", errors="replace") == IGNORE_EVERYTHING:
            continue
        found[relative] = _sha256(path.read_bytes()) if path.is_file() else "not a file"
    return found


def _write_folder(
    target: Path,
    files: dict[str, dict[str, Any]],
    contents: dict[str, bytes],
    marker: dict[str, Any],
) -> None:
    """Write the folder beside ``target``, then swap it into place."""
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
    try:
        for path, one in files.items():
            file = staging.joinpath(*path.split("/"))
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(contents[path])
            if one.get("executable"):
                file.chmod(file.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        if GITIGNORE not in files:
            (staging / GITIGNORE).write_text(IGNORE_EVERYTHING, "utf-8")
        (staging / MARKER).write_text(json.dumps(marker, indent=2) + "\n", "utf-8")
        staging.chmod(0o755)

        if target.is_symlink() or target.is_file():
            target.unlink()
        if target.exists():
            retired = target.with_name(f".{target.name}.replaced")
            shutil.rmtree(retired, ignore_errors=True)
            target.rename(retired)
            staging.rename(target)
            shutil.rmtree(retired, ignore_errors=True)
        else:
            staging.rename(target)
    except OSError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise CylistError(f"Cannot write {target}: {exc.strerror or exc}.") from exc
