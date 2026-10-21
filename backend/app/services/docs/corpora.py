"""A project's docs as jev-docs reads them, built once and kept until they change.

Building the corpus means loading every doc body on the project — up to 200k
characters each — and parsing each into sections. Done for every ``ask_docs``
and ``place_doc``, that was the whole corpus in bandwidth and memory per
question, times every agent asking at once (CYLIST-144). So each process keeps
the last corpus it built for each project, and asks the database one cheap
question before reusing it: has anything the corpus was built from changed?

That question is the invalidation, rather than an event. A doc written in
another worker is a change this process hears of only over the bus, and the
bus drops what is said while it reconnects (see :mod:`app.realtime.bus`) —
which would leave a stale corpus with nothing to say so. Asking costs one
aggregate over the project's rows, bodies unread, and is right whichever
process wrote.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import BigInteger, Select, Text, cast, func, literal_column, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.doc import Doc, DocTopic
from app.models.project import Project
from app.services import doc_corpus
from app.services.doc_corpus import ProjectCorpus
from app.services.docs.filing import topics_for

KEPT = 32
"""Projects whose corpus a process keeps at once, the least recently asked
dropped first. A deployment has a handful of projects with docs; this bounds
the memory if it ever has many."""

Rows = tuple[int, datetime | None, int]
"""How many rows, the newest ``updated_at`` among them, and the sum of their
``xmin``."""


@dataclass(frozen=True)
class Stamp:
    """What a corpus was built from, in a form cheap to read and compare.

    ``count`` and newest ``updated_at`` catch a row added, deleted or edited —
    but not on their own. ``updated_at`` is ``now()``, the moment a transaction
    *began*, so a write that began before another and committed after it lands
    older than the newest stamp already seen, and would go unnoticed. The sum
    of the rows' ``xmin`` — the transaction that last wrote each — is what
    catches that: it moves for every row rewritten, whenever its transaction
    began.

    Topics count as well as docs, since a corpus names its groups after them
    and orders by them; so does the project's key, which is the corpus's root.
    """

    project_key: str
    docs: Rows
    topics: Rows


def _rows_of(
    model: type[Doc] | type[DocTopic], project_id: UUID
) -> Select[tuple[int, datetime, int]]:
    # xid has no arithmetic; through text, it is a plain number.
    xmin = cast(cast(literal_column("xmin"), Text), BigInteger)
    return (
        select(
            func.count().label("count"),
            func.max(model.updated_at).label("newest"),
            func.coalesce(func.sum(xmin), 0).label("versions"),
        )
        .select_from(model)
        .where(model.project_id == project_id)
    )


async def stamp_of(session: AsyncSession, project: Project) -> Stamp:
    """The project's docs and topics as they stand, in one round trip."""
    docs = _rows_of(Doc, project.id).subquery()
    topics = _rows_of(DocTopic, project.id).subquery()
    # One row each, so joined on nothing: side by side.
    both = select(docs, topics).select_from(docs.join(topics, true()))
    row = (await session.execute(both)).one()
    return Stamp(
        project_key=project.key,
        docs=(int(row[0]), row[1], _whole(row[2])),
        topics=(int(row[3]), row[4], _whole(row[5])),
    )


def _whole(total: Decimal | int) -> int:
    """A ``sum`` of bigints, which Postgres widens to numeric, as an int."""
    return int(total)


class CorpusCache:
    """The corpus last built for each project in this process, and what from.

    Shared by every request the process serves. A corpus is only read once it
    is built — jev-docs plans its questions from it and changes nothing — so
    two questions may share one, in two threads, safely.
    """

    def __init__(self, kept: int = KEPT) -> None:
        self._kept = kept
        self._built: OrderedDict[UUID, tuple[Stamp, ProjectCorpus]] = OrderedDict()

    def get(self, project_id: UUID, stamp: Stamp) -> ProjectCorpus | None:
        """The corpus built from exactly ``stamp``, if this process has it."""
        found = self._built.get(project_id)
        if found is None or found[0] != stamp:
            return None
        self._built.move_to_end(project_id)
        return found[1]

    def put(self, project_id: UUID, stamp: Stamp, corpus: ProjectCorpus) -> None:
        self._built[project_id] = (stamp, corpus)
        self._built.move_to_end(project_id)
        while len(self._built) > self._kept:
            self._built.popitem(last=False)


async def corpus_for(session: AsyncSession, project: Project, cache: CorpusCache) -> ProjectCorpus:
    """The project's docs as jev-docs reads them, in tree order.

    From ``cache`` when nothing it was built from has changed; otherwise built
    from the rows, bodies and all, and kept for next time. The stamp is read
    before the rows, so a write landing between the two leaves a corpus newer
    than its stamp — rebuilt once more next time, never stale.
    """
    stamp = await stamp_of(session, project)
    kept = cache.get(project.id, stamp)
    if kept is not None:
        return kept
    topics = await topics_for(session, project.id)
    by_topic: dict[UUID, list[Doc]] = {topic.id: [] for topic in topics}
    for doc in await session.scalars(
        select(Doc).where(Doc.project_id == project.id).order_by(Doc.position, Doc.created_at)
    ):
        by_topic[doc.topic_id].append(doc)
    built = doc_corpus.build(project.key, [(topic, by_topic[topic.id]) for topic in topics])
    cache.put(project.id, stamp, built)
    return built
