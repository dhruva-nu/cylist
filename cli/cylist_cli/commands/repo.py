"""``cylist repo setup`` — teach the repository you are in to use a project.

``cylist setup`` configures a *machine*: a token, Claude Code's hooks, the MCP
server. This configures a *repository*. It writes into that repository's
``CLAUDE.md`` what an agent opening it needs to know about the board the work
is tracked on: ask the project's docs before reading the code, take a
procedure from its skills rather than inventing one, take credentials from its
vault rather than from a config file.

Half of what it writes is the same for every project. The other half is read
off the board as the command runs — the project's name, its columns and what
each one means, the shape of its docs, the skills it has — because a paragraph
describing a board from memory is wrong the week after it is written, and a
wrong instruction in a ``CLAUDE.md`` is read by every session afterwards.

Four rules shape it, and they are why this is a command rather than something
a model is asked to do by hand:

* **It checks before it writes.** Every request is made first; a project key
  that is not on the server ends with a sentence naming the keys that are, and
  a file that was never opened.
* **It never clobbers.** The block is fenced by two marker comments carrying
  the project's key. Everything outside them is left as it was, and a second
  project in the same repository gets a second block rather than overwriting
  the first.
* **It is idempotent to the byte.** A re-run replaces its own block in place
  and reports ``unchanged`` when the board has not moved. Nothing dated goes
  into the block for exactly that reason: a timestamp would make every run a
  diff.
* **It reads the same on Windows.** Every path is a :class:`~pathlib.Path`, no
  shell is involved, and the file is read and written with newline translation
  turned *off* — so a CRLF ``CLAUDE.md`` stays CRLF, an LF one stays LF, and
  the command does not flip a whole file's line endings as a side effect of
  appending twenty lines to it.

The ``/cylist-setup`` slash command in
:data:`cylist_cli.commands.hook.SETUP_COMMAND_FILE` is a wrapper round this
and nothing more: the block has to be read off the board, not composed from
whatever the model remembers about Cylist.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cylist_cli import output
from cylist_cli.commands.skills import repository_root
from cylist_cli.context import Context
from cylist_cli.errors import ApiError, CylistError

CLAUDE_MD = "CLAUDE.md"

BEGIN = "<!-- cylist:begin {key} -->"
END = "<!-- cylist:end {key} -->"
"""The fence, carrying the key so two projects can share one repository.

An HTML comment because ``CLAUDE.md`` is markdown that people read: the fence
has to be invisible when the file is rendered and obvious when it is edited.
"""

NOT_FOUND = 404


def register(subparsers: Any) -> None:
    repo = subparsers.add_parser(
        "repo",
        help="Set the repository you are in up to work against a project.",
    )
    actions = repo.add_subparsers(dest="action", required=True, metavar="<action>")

    setup = actions.add_parser(
        "setup",
        help="Write a project's conventions into this repository's CLAUDE.md.",
        description=(
            "Writes a block into CLAUDE.md at the root of the repository you are "
            "in: how to use the project's docs, skills and vault, and the "
            "project's own columns, docs and skills as the board has them right "
            "now. The block is fenced by marker comments carrying the project's "
            "key, so nothing else in the file is touched and a second run "
            "replaces the block rather than adding another. The project must "
            "exist: if it does not, nothing is written and the error names the "
            "keys that do."
        ),
    )
    setup.add_argument("project", metavar="PROJECT", help="Project key, e.g. ATL.")
    setup.add_argument(
        "--path",
        metavar="PATH",
        help="Write this file, or CLAUDE.md inside this directory, instead.",
    )
    setup.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the block that would be written and change nothing.",
    )
    setup.set_defaults(handler=_setup)


@dataclass(frozen=True)
class Edit:
    """What one run would do, or did, to a ``CLAUDE.md``."""

    text: str
    """The whole file as it should be afterwards."""

    result: str
    """``created``, ``added``, ``updated`` or ``unchanged``."""


def _setup(args: argparse.Namespace, ctx: Context) -> None:
    # Everything is read before anything is written: a half-written CLAUDE.md
    # because the fourth request 403-ed is worse than no CLAUDE.md at all.
    summary = _project(ctx, str(args.project))
    key = str(summary["key"])
    columns = list(ctx.client.get(f"/projects/{key}/columns").get("columns", []))
    docs = dict(ctx.client.get(f"/projects/{key}/docs"))
    skills = list(ctx.client.get(f"/projects/{key}/skills"))

    block = _block(summary, columns, docs, skills)
    path = _destination(args.path)
    raw = _read(path)
    edit = _merge(raw, key, block)

    if not args.dry_run and edit.result != "unchanged":
        _write(path, edit.text, newline=_newline(raw))

    if ctx.as_json:
        output.emit_json(
            {
                "project": key,
                "path": str(path),
                "result": edit.result,
                "written": not args.dry_run and edit.result != "unchanged",
                "block": block,
            }
        )
        return

    if args.dry_run:
        output.warn(f"Dry run: {path} was not touched. This is the block ({edit.result}):")
        output.echo(BEGIN.format(key=key))
        output.echo(block.rstrip("\n"))
        output.echo(END.format(key=key))
        return

    output.echo(_said(edit.result, key, path))
    output.echo(
        f"It describes {_count(len(columns), 'column')}, "
        f"{_count(int(docs.get('doc_count', 0)), 'doc')} and "
        f"{_count(len(skills), 'skill')}, between the "
        f"'{BEGIN.format(key=key)}' markers. Run it again to refresh it."
    )


def _said(result: str, key: str, path: Path) -> str:
    return {
        "created": f"Created {path} with {key}'s Cylist block.",
        "added": f"Added {key}'s Cylist block to {path}.",
        "updated": f"Updated {key}'s Cylist block in {path}.",
        "unchanged": f"{key}'s Cylist block in {path} already says this; left it alone.",
    }[result]


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _project(ctx: Context, given: str) -> dict[str, Any]:
    """The project, or a sentence naming the ones that exist.

    The server's own 404 says the reference did not resolve, which is true and
    not much help to somebody who has mistyped a key. The keys it *does* have
    are one more request and turn the error into the answer.
    """
    try:
        return dict(ctx.client.get(f"/projects/{given}/summary"))
    except ApiError as exc:
        if exc.status_code != NOT_FOUND:
            raise
        known = ", ".join(sorted(str(project["key"]) for project in ctx.client.get("/projects")))
        raise CylistError(
            f"No project {given!r} on this server, so nothing was written. "
            f"It has: {known or 'no projects at all'}.",
            details={"project": given},
        ) from exc


# --- Where it goes ---------------------------------------------------------


def _destination(given: str | None) -> Path:
    """The ``CLAUDE.md`` to write: the repository's, or whatever was asked for.

    The root of the repository rather than the current directory, because that
    is the ``CLAUDE.md`` every session started anywhere inside it reads.
    """
    if given:
        path = Path(given).expanduser()
        return path / CLAUDE_MD if path.is_dir() else path
    return repository_root(Path.cwd()) / CLAUDE_MD


def _read(path: Path) -> str | None:
    """The file as it is, with its line endings intact, or ``None``.

    ``newline=""`` turns off universal-newline translation, so what comes back
    still says whether this file is CRLF — which :func:`_newline` has to know
    and which a plain ``read_text`` has already thrown away.
    """
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return handle.read()
    except FileNotFoundError:
        return None


def _newline(raw: str | None) -> str:
    """What this file separates its lines with, and so what we must write.

    A ``CLAUDE.md`` checked out on Windows is usually CRLF and appending LF to
    it would leave the file half one and half the other. A new file is LF on
    every platform on purpose: the command should produce the same bytes
    wherever it is run, and LF is what git stores anyway.
    """
    return "\r\n" if raw is not None and "\r\n" in raw else "\n"


def _write(path: Path, text: str, *, newline: str) -> None:
    """Write ``text`` — which is in LF throughout — with that line ending.

    ``newline=""`` again, so Python translates nothing and the string decides.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text.replace("\n", newline))


def _merge(raw: str | None, key: str, block: str) -> Edit:
    """Put ``block`` into the file, replacing our own fence if it is already there.

    Works in LF throughout: the raw text is normalised on the way in and
    :func:`_write` puts the file's own line ending back on the way out. A file
    whose endings are already uniform therefore round-trips exactly; one that
    is a mixture of both is made uniform, which is the only way to splice into
    it without inventing a third convention.
    """
    begin, end = BEGIN.format(key=key), END.format(key=key)
    fenced = f"{begin}\n{block}{end}\n"

    if raw is None:
        return Edit(fenced, "created")
    existing = raw.replace("\r\n", "\n")
    if not existing.strip():
        return Edit(fenced, "added")

    start = existing.find(begin)
    if start < 0:
        return Edit(f"{existing.rstrip('\n')}\n\n{fenced}", "added")

    stop = existing.find(end, start)
    if stop < 0:
        raise CylistError(
            f"{CLAUDE_MD} has a '{begin}' with no '{end}' after it, so I cannot tell "
            "where the block I wrote ends. Close it or delete it, then run this again."
        )
    after = stop + len(end)
    if existing[after : after + 1] == "\n":
        after += 1
    merged = existing[:start] + fenced + existing[after:]
    return Edit(merged, "unchanged" if merged == existing else "updated")


# --- What it writes --------------------------------------------------------


def _block(
    summary: dict[str, Any],
    columns: Sequence[dict[str, Any]],
    docs: dict[str, Any],
    skills: Sequence[dict[str, Any]],
) -> str:
    """The markdown between the two markers, ending in one newline.

    Nothing in here is dated or counted off the clock, so two runs against an
    unchanged board produce the same bytes.
    """
    key = str(summary["key"])
    lines = [
        "",
        f"## Working with Cylist project {key}",
        "",
        *_what_this_is(summary),
        "",
        *_using_the_docs(key),
        "",
        *_using_the_skills(key),
        "",
        *_using_the_vault(key),
        "",
        *_the_board_today(key, columns, docs, skills),
        "",
    ]
    return "\n".join(lines) + "\n"


def _what_this_is(summary: dict[str, Any]) -> list[str]:
    key = str(summary["key"])
    described = _sentence(summary.get("description"))
    headline = f"**{key} — {summary['name']}**." + (f" {described}" if described else "")
    return [
        f"The work in this repository is tracked on the Cylist project {headline}",
        "",
        "Cylist's tools are in this session as `mcp__cylist__*`; the names below are",
        "those tools without the prefix, and `cylist --help` does the same things from",
        "a terminal. Everything between the two `cylist:` marker comments is written by",
        f"`cylist repo setup {key}` and is replaced whole when it runs again, so put",
        "notes of your own outside them.",
    ]


def _using_the_docs(key: str) -> list[str]:
    return [
        "### Ask the docs before you read the code",
        "",
        f"{key} keeps docs, filed as section -> topic -> doc, most of them written by",
        "the agents who worked here before you. They are the first place to look and",
        "the place to leave what you learn.",
        "",
        "- Whenever you would open this repository to answer a question — where a thing",
        f"  lives, how it works, why it is the way it is — `ask_docs` {key} first, in",
        "  plain words, as you would ask a colleague.",
        "- Status `ok` hands you the one section that answers it. Act on that section.",
        "  Any other status means the docs do not know yet.",
        "- When they do not know, find the answer in the code and then **straight away**",
        "  — not at the end of the task, because a session can stop first — `place_doc`",
        "  what you found and make the edits it plans with `write_doc`.",
        "- Write facts, one per sentence, in the shape the doc is already written in.",
        "  Not a progress log: the board reports that. Where a section has stopped being",
        "  true, write the correction rather than a second account beside it.",
        "- `list_docs` shows the whole tree and `read_doc` reads one doc end to end.",
        "  Topics are made by people; agents file docs under the ones that exist.",
    ]


def _using_the_skills(key: str) -> list[str]:
    return [
        "### Take a procedure from the skills rather than inventing one",
        "",
        f"- `list_skills` for {key} lists the jobs somebody has already written down.",
        "  Read it before you improvise a procedure that exists.",
        "- `read_skill` reads one. `download_skill` hands you one to install, and",
        f"  `cylist skills pull {key}` writes them all into `.claude/skills/`, where",
        "  Claude Code loads them by itself from the next session onwards.",
    ]


def _using_the_vault(key: str) -> list[str]:
    return [
        "### Take credentials from the vault, never from the source",
        "",
        "- There are no credentials in this repository and none should be added to it.",
        f"  `list_vault` for {key} shows the trees, and given a tree the whole of it:",
        "  every secret with its username, URL and notes. No response from it ever",
        "  carries a value.",
        "- A value is read only by `reveal_secret`, which needs the `vault:reveal`",
        "  scope and is written to the audit feed naming the secret and the token that",
        "  asked. Use what it returns; do not repeat it in a comment, a summary, a log",
        "  line or a file.",
        "- A new credential goes in with `add_secret` (or `cylist vault add`), not into",
        "  a config file, a fixture or an environment variable committed here.",
    ]


def _the_board_today(
    key: str,
    columns: Sequence[dict[str, Any]],
    docs: dict[str, Any],
    skills: Sequence[dict[str, Any]],
) -> list[str]:
    return [
        f"### {key} as the board has it",
        "",
        *_column_lines(columns),
        "",
        *_doc_lines(key, docs),
        "",
        *_skill_lines(key, skills),
    ]


def _column_lines(columns: Sequence[dict[str, Any]]) -> list[str]:
    if not columns:
        return ["This board has no columns yet."]
    return [
        "Columns, in the order they are drawn. `move_task` takes these names exactly,",
        "trailing space and all, so copy them from inside the backticks:",
        "",
        "| Column | What it means |",
        "| --- | --- |",
        *(
            f"| `{_verbatim(column['name'])}` | {_cell(column.get('description')) or '—'} |"
            for column in columns
        ),
    ]


def _doc_lines(key: str, docs: dict[str, Any]) -> list[str]:
    """The shape of the docs, not their content: which topics exist, how full.

    Enough to tell an agent that a question about the CLI has somewhere to go,
    and no more — `ask_docs` is what finds the section, and listing every doc
    title here would be a second index to keep in step with the first.
    """
    written = [
        (str(section["label"]), section.get("topics") or [])
        for section in docs.get("sections") or []
        if section.get("topics")
    ]
    if not written:
        return [
            f"{key} has no docs yet. The first `place_doc` makes the shape, so if you",
            "work something out here, that is the moment to write it down.",
        ]
    return [
        "Docs, by section and topic, with how many docs each topic holds. `ask_docs`",
        "finds the right one; this is only what there is to find:",
        "",
        *(
            f"- **{label}** — "
            + ", ".join(f"{topic['name']} ({topic['doc_count']})" for topic in topics)
            for label, topics in written
        ),
    ]


def _skill_lines(key: str, skills: Sequence[dict[str, Any]]) -> list[str]:
    if not skills:
        return [f"{key} has no skills yet; `list_skills` is where they will appear."]
    return [
        "Skills on the board:",
        "",
        "| Skill | What it does |",
        "| --- | --- |",
        *(
            f"| `{_verbatim(skill['name'])}` | {_cell(skill.get('description')) or '—'} |"
            for skill in skills
        ),
    ]


def _cell(value: Any) -> str:
    """Prose in a table cell: no pipes to break the row, no newlines to end it early."""
    return " ".join(str(value or "").split()).replace("|", "\\|")


def _verbatim(value: Any) -> str:
    """A name in a table cell, spelled exactly as the board spells it.

    Not :func:`_cell`, which collapses whitespace: a column called ``Dev `` is
    a column called ``Dev ``, and ``move_task`` does not find it under ``Dev``.
    The backticks around it in the table are what make the space visible. Only
    the two characters that would break the row out of its cell are touched.
    """
    return str(value).replace("|", "\\|").replace("\n", " ")


def _sentence(value: Any) -> str:
    """A description, on one line and ending in a full stop as prose should."""
    text = " ".join(str(value or "").split())
    return text if not text or text[-1] in ".!?" else f"{text}."
