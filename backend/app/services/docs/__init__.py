"""Docs: a project's markdown, filed as section → topic → doc.

Two halves. :mod:`.filing` is what people do on the Docs page — topics and
docs made, renamed, moved and reordered — and owns the rules about where a
doc may sit. The rest is what agents do, through jev-docs: :mod:`.asking`
finds the section that answers a question, :mod:`.placing` plans where a new
fact belongs — both over the corpus :mod:`.corpora` keeps between questions —
and :mod:`.writing` files what the agent wrote. The agent half
only ever files through the first, so the rules about where a doc may sit
are kept in one place.
"""

from app.services.docs.asking import Answer, Found, Weighed, ask
from app.services.docs.corpora import CorpusCache, corpus_for
from app.services.docs.filing import (
    count_for_project,
    counts_by_topic,
    create_doc,
    create_topic,
    delete_doc,
    delete_topic,
    docs_for,
    find_topic,
    get_doc,
    get_topic,
    rename_topic,
    reorder_docs,
    reorder_topics,
    topics_for,
    update_doc,
)
from app.services.docs.placing import Edit, Placed, place
from app.services.docs.writing import TopicUnclearError, Written, appended, write

__all__ = [
    "Answer",
    "CorpusCache",
    "Edit",
    "Found",
    "Placed",
    "TopicUnclearError",
    "Weighed",
    "Written",
    "appended",
    "ask",
    "corpus_for",
    "count_for_project",
    "counts_by_topic",
    "create_doc",
    "create_topic",
    "delete_doc",
    "delete_topic",
    "docs_for",
    "find_topic",
    "get_doc",
    "get_topic",
    "place",
    "rename_topic",
    "reorder_docs",
    "reorder_topics",
    "topics_for",
    "update_doc",
    "write",
]
