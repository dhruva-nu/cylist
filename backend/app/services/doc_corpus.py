"""A project's docs, as jev-docs reads them.

jev-docs (``vendor/jev-docs``) routes a question over a tree of markdown files —
domain → group → file → section — and plans where a new fact belongs in one.
Cylist keeps its docs as rows instead, so this builds that tree in memory from
them, fresh for each question: a project's docs are small, and a tree built
from the rows is never behind them.

The mapping:

* **section → domain.** Product and Engineering are the two domains jev-docs is
  written for, ``product`` and ``engineering``, and its own descriptions of them
  are the ones used;
* **topic → group**, described by its name, since a topic has no README (the
  router adds the titles of the docs in it);
* **doc → file**, named by a slug of its title that is unique within its
  section — every topic has a ``learned.md``, so the second is
  ``<topic>-learned``;
* **sections.** A doc written in the jev-docs format (``DOCS_FORMAT.md``: a
  summary, an ``## Index`` of ``N. Title — blurb``, one ``## Title`` per entry)
  keeps its own. Anything else has them made, so it is routable as it stands:
  one per ``## `` heading; one per top-level bullet when the doc is a list, as a
  ``learned.md`` is; or the whole doc as one section.

Nothing here talks to jev. :class:`ProjectCorpus` also carries the way back —
from the refs jev-docs answers in to the docs and topics Cylist addresses by id.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from uuid import UUID

from jevdocs import Corpus, DocFile, Domain, Group, Section, parse_text
from jevdocs.corpus import DEFAULT_DOMAIN_DESCRIPTIONS, slug

from app.models.doc import SECTION_ORDER, Doc, DocTopic

SUMMARY_MAX_CHARS = 240
"""How much of a doc's opening stands in for it when no summary was written.
Enough for its opening sentence or two; the doc's title and topic say the rest."""

BULLET_TITLE_WORDS = 8
"""How many words of a bullet name the section made from it."""

LEARNED = "learned.md"

Shape = Literal["indexed", "headings", "bullets", "plain"]
"""How a doc is written, which says how a new section goes into it:

* ``indexed`` — the jev-docs format: an ``## Index`` entry and a ``## `` section;
* ``headings`` — a ``## `` section;
* ``bullets`` — a ``- `` line;
* ``plain`` — anywhere; the doc is one section.
"""


@dataclass(frozen=True)
class FiledTopic:
    id: UUID
    section_label: str
    name: str

    @property
    def path(self) -> str:
        """``Engineering / MCP`` — how an agent names the topic back."""
        return f"{self.section_label} / {self.name}"


@dataclass(frozen=True)
class FiledDoc:
    id: UUID
    title: str
    topic: FiledTopic
    shape: Shape
    file: DocFile

    @property
    def path(self) -> str:
        """``Engineering / MCP / learned.md`` — how ``read_doc`` takes it."""
        return f"{self.topic.path} / {self.title}"

    def section_title(self, anchor: str) -> str | None:
        found = self.file.section(anchor)
        return found.title if found is not None else None


@dataclass
class ProjectCorpus:
    corpus: Corpus
    docs: dict[str, FiledDoc] = field(default_factory=dict)
    """By file key, ``engineering/tasks.md``: the one jev-docs keeps when a file
    moves between groups."""
    topics: dict[str, FiledTopic] = field(default_factory=dict)
    """By group ref, ``engineering/mcp/``."""

    @property
    def empty(self) -> bool:
        return not self.docs

    def doc_at(self, ref: str) -> FiledDoc | None:
        """The doc a ref names — ``engineering/mcp/tasks.md#moves``, with or
        without its group or its anchor."""
        parts = ref.split("#", 1)[0].removesuffix(".md").split("/")
        if len(parts) < 2:
            return None
        return self.docs.get(f"{parts[0]}/{parts[-1]}.md")

    def topic_at(self, ref: str) -> FiledTopic | None:
        """The topic a group ref names — ``engineering/mcp/``, or a proposed
        file inside it, ``engineering/mcp/(new topic).md``."""
        parts = ref.split("/")
        if len(parts) < 3:
            return None
        return self.topics.get(f"{parts[0]}/{parts[1]}/")


def build(project_key: str, filed: Sequence[tuple[DocTopic, Sequence[Doc]]]) -> ProjectCorpus:
    """The corpus for a project's topics, each with its docs in order, bodies loaded.

    Both domains are always present, so a question is routed by what it asks —
    why or how — whatever has been written so far; one routed to a domain with
    nothing in it has simply not been documented yet.
    """
    domains = {
        section.value: Domain(section.value, DEFAULT_DOMAIN_DESCRIPTIONS[section.value])
        for section in SECTION_ORDER
    }
    built = ProjectCorpus(Corpus(root=Path(project_key), domains=domains))
    for topic, docs in filed:
        domain = domains[topic.section.value]
        group_name = _unique(slug(topic.name) or "topic", domain.groups)
        group = Group(
            domain=domain.name,
            name=group_name,
            description=f"Docs filed under {topic.section.label} / {topic.name}.",
        )
        domain.groups[group_name] = group
        filed_topic = FiledTopic(id=topic.id, section_label=topic.section.label, name=topic.name)
        built.topics[group.ref] = filed_topic
        for doc in docs:
            name = _file_name(doc.title, group_name, domain.files)
            file, shape = as_file(doc.title, doc.body, name=name, domain=domain.name)
            file.group = group_name
            group.files[name] = file
            domain.files[name] = file
            built.docs[file.key] = FiledDoc(
                id=doc.id, title=doc.title, topic=filed_topic, shape=shape, file=file
            )
    return built


def as_file(title: str, body: str, *, name: str, domain: str) -> tuple[DocFile, Shape]:
    """One doc as a jev-docs file, and the shape it was written in."""
    parsed = parse_text(body, path=Path(f"{name}.md"), domain=domain)
    shown = _shown_title(title, body)
    if parsed.sections:
        parsed.title = shown
        return parsed, "indexed"

    sections, shape = made_sections(shown, body)
    opening = summary_of(body)
    return (
        DocFile(
            domain=domain,
            name=name,
            path=Path(f"{name}.md"),
            title=shown,
            summary=[opening] if opening else [shown],
            metadata=parsed.metadata,
            sections=sections,
        ),
        shape,
    )


def made_sections(title: str, body: str) -> tuple[list[Section], Shape]:
    """Sections for a doc not written in the jev-docs format."""
    preamble, blocks = _h2_blocks(body)
    if blocks:
        found = []
        if preamble:
            found.append(("Overview", preamble))
        found += blocks
        return _numbered(found), "headings"

    bullets = _bullets(preamble)
    if bullets:
        return _numbered([(_bullet_title(item), item) for item in bullets]), "bullets"

    return _numbered([(title, preamble)] if preamble else []), "plain"


# --- Reading markdown ------------------------------------------------------------

_H1 = re.compile(r"^#\s")
_H2 = re.compile(r"^##\s")
_BULLET = re.compile(r"^[-*+]\s+")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s")
_LINE_MARKER = re.compile(r"^\s*(?:>\s*|[-*+]\s+|\d+[.)]\s+)")
"""A quote, bullet or number opening a line."""
_MARKUP = re.compile(r"[*`]+|(?<!\w)_+|_+(?!\w)")
"""Emphasis and code marks — an underscore only at a word's edge, so
``write_doc`` keeps its own."""


def summary_of(body: str) -> str:
    """The opening paragraph of a doc's markdown, headings skipped, as one line.

    A doc not in the jev-docs format carries no summary; its first paragraph is
    what one would say.
    """
    for paragraph in re.split(r"\n\s*\n", body):
        lines = [
            _LINE_MARKER.sub("", line)
            for line in paragraph.splitlines()
            if not _HEADING.match(line)
        ]
        text = " ".join(_MARKUP.sub("", " ".join(lines)).split())
        if text:
            if len(text) <= SUMMARY_MAX_CHARS:
                return text
            return text[: SUMMARY_MAX_CHARS - 1].rstrip() + "…"
    return ""


def _shown_title(title: str, body: str) -> str:
    """What jev is told the doc is called: its ``# `` heading when it has one.

    A ``learned.md`` is named for what it holds, since every topic has one and
    the file name says nothing.
    """
    if title.casefold() == LEARNED:
        return "What agents learned here"
    first = next((line for line in body.splitlines() if line.strip()), "")
    if _H1.match(first):
        return first[2:].strip() or title
    return title.removesuffix(".md")


def _h2_blocks(body: str) -> tuple[str, list[tuple[str, str]]]:
    """The text before the first ``## `` (its ``# `` line dropped), and each
    ``## `` heading with the text under it."""
    lines = body.splitlines()
    if lines and _H1.match(next((line for line in lines if line.strip()), "")):
        first = next(index for index, line in enumerate(lines) if line.strip())
        lines = lines[first + 1 :]
    preamble: list[str] = []
    blocks: list[tuple[str, list[str]]] = []
    for line in lines:
        if _H2.match(line):
            blocks.append((line[3:].strip(), []))
        elif blocks:
            blocks[-1][1].append(line)
        else:
            preamble.append(line)
    return (
        "\n".join(preamble).strip(),
        [(heading, "\n".join(text).strip()) for heading, text in blocks if heading],
    )


def _bullets(text: str) -> list[str]:
    """The top-level items of a doc that is one list, or nothing when it is not.

    A line that opens no item continues the one before it; any text before the
    first item means the doc is prose with a list in it, not a list.
    """
    items: list[list[str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if _BULLET.match(line):
            items.append([_BULLET.sub("", line, count=1)])
        elif items:
            items[-1].append(line.strip())
        else:
            return []
    return [" ".join(item).strip() for item in items if " ".join(item).strip()]


def _bullet_title(item: str) -> str:
    words = _MARKUP.sub("", item).split()
    title = " ".join(words[:BULLET_TITLE_WORDS])
    return title + "…" if len(words) > BULLET_TITLE_WORDS else title


def _numbered(found: Sequence[tuple[str, str]]) -> list[Section]:
    """Index entries for made sections, each titled so its anchor is its own."""
    sections: list[Section] = []
    anchors: set[str] = set()
    for number, (title, body) in enumerate(found, start=1):
        shown = title
        if not slug(shown):
            shown = f"Section {number}"
        if slug(shown) in anchors:
            shown = f"{shown} ({number})"
        anchors.add(slug(shown))
        sections.append(Section(number, shown, summary_of(body) or shown, body=body))
    return sections


def _file_name(title: str, group_name: str, taken: dict[str, DocFile]) -> str:
    """A file stem for a doc, unique within its domain as jev-docs needs.

    The title's slug when it is free; otherwise prefixed with the topic's, which
    is how the second topic's ``learned.md`` becomes ``mcp-learned``.
    """
    base = slug(title.removesuffix(".md")) or "doc"
    if base not in taken:
        return base
    return _unique(f"{group_name}-{base}", taken)


def _unique(name: str, taken: Mapping[str, object]) -> str:
    if name not in taken:
        return name
    number = 2
    while f"{name}-{number}" in taken:
        number += 1
    return f"{name}-{number}"
