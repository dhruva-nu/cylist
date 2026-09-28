"""Turn a Corpus into Jev questions. One builder per routing hop.

    DomainQ, Answerable, SpansDomains = domain_questions(corpus)
    GroupQ   = group_question(corpus.domains["product"])        # a grouped domain only
    TopicQ   = topic_question(corpus.domains["product"].groups["store"])   # or a flat domain
    SectionQ = section_question(docfile, name="section_0")
    Relevant = relevance_question()             # asked about {question, section text}

Every question is a Choice / Noul built at runtime with the SDK factories, so the
labels are the domain names, file stems and section anchors the caller already has.
"""
from __future__ import annotations

from typing import Dict, Tuple, Type

from jev import Choice, Noul, choice, noul

from .corpus import Corpus, DocFile, Domain, Group

DOMAIN = "domain"
GROUP = "group"
ANSWERABLE = "answerable"
SPANS_DOMAINS = "spans_domains"
TOPIC = "topic"
SECTION = "section"
NEEDS_WHOLE_FILE = "needs_whole_file"
RELEVANT = "relevant"


def domain_questions(corpus: Corpus) -> Tuple[Type[Choice], Type[Noul], Type[Noul]]:
    names = list(corpus.domains)
    domain_q = choice(
        DOMAIN,
        "The state is a question a reader asked about our software. Which kind of documentation "
        "answers it? Judge by what the reader wants to know, not by the nouns they mention.",
        {name: corpus.domains[name].description for name in names},
    )
    answerable = noul(
        ANSWERABLE,
        "Is this something written documentation about our software could answer?",
        true_means="a question about how our product behaves, its rules, its code, its operations, or its history",
        false_means="small talk, a request to perform an action, or a question about the outside world",
    )
    spans = noul(
        SPANS_DOMAINS,
        "Does a complete answer need BOTH the business reason and the technical mechanism?",
        true_means="the reader asks why AND how, or asks for the rule together with its implementation",
        false_means="one of the two is enough: either the reason/rule alone, or the mechanism alone",
    )
    return domain_q, answerable, spans


def group_question(domain: Domain, name: str = GROUP) -> Type[Choice]:
    """Which group of a grouped domain: each option is the group README plus its files' titles."""
    if len(domain.groups) < 2:
        raise ValueError("domain %r needs at least two groups to route between" % domain.name)
    criteria = {}
    for gname, group in domain.groups.items():
        titles = "; ".join(d.title for d in group.files.values())
        criteria[gname] = "%s Files: %s." % (group.description.rstrip(".") + ".", titles)
    return choice(
        name,
        "The state is a reader's question. Which group of %s documentation covers it? "
        "Each option is the group's own description and the files in it." % domain.name,
        criteria,
    )


def topic_question(scope: "Domain | Group", name: str = TOPIC) -> Type[Choice]:
    """Which file, among a flat domain's files or one group's files."""
    if len(scope.files) < 2:
        raise ValueError("%r needs at least two files to route between" % scope.name)
    criteria: Dict[str, str] = {}
    for fname, doc in scope.files.items():
        # The summary is what the author wrote for exactly this purpose; section titles add
        # the concrete nouns a summary may have skipped.
        criteria[fname] = "%s. Sections: %s." % (doc.summary_text().rstrip("."), "; ".join(s.title for s in doc.sections))
    where = ("the %s group of %s" % (scope.name, scope.domain)) if isinstance(scope, Group) else scope.name
    return choice(
        name,
        "The state is a reader's question. Which of these %s documentation files covers it? "
        "Each option is that file's own summary." % where,
        criteria,
    )


def section_question(doc: DocFile, name: str = SECTION) -> Type[Choice]:
    """`name` lets several files' section questions share one request (section_0, section_1, ...)."""
    if len(doc.sections) < 2:
        raise ValueError("%s needs at least two sections to route between" % doc.ref)
    criteria = {s.anchor: "%s — %s" % (s.title, s.blurb) for s in doc.sections}
    return choice(
        name,
        "The state is a reader's question, already known to be about %r (%s). "
        "Which section of that file answers it?" % (doc.title, doc.summary_text()),
        criteria,
    )


def whole_file_question(doc: DocFile, name: str = NEEDS_WHOLE_FILE) -> Type[Noul]:
    return noul(
        name,
        "Does answering this question need the whole %r file rather than one section?" % doc.title,
        true_means="the reader asks for an overview, a full list, or something that cuts across most sections",
        false_means="one section answers it",
    )


def relevance_question() -> Type[Noul]:
    """Asked with state = {question, section text}: the one hop that reads a section body."""
    return noul(
        RELEVANT,
        "The state holds a reader's question and one section of our documentation. "
        "Is this section relevant to the question: does it contain the information the reader is asking for?",
        true_means="the section answers the question, fully or in part",
        false_means="the section is about something else, or only shares some of the same words",
    )


def relevance_state(question: str, doc: DocFile, title: str, text: str) -> dict:
    return {"question": question, "documentation_section": {"file": doc.ref, "title": title, "text": text}}


# ----------------------------------------------------------------------------- placement
# Placement asks about a change to the docs, not a reader's question. The state is one fact
# of the change (or, for `affected`, the facts plus a section's text). See placement.py.
TOUCHES = "touches"
PLACE = "place"
AFFECTED = "affected"
NEW_SECTION = "new_section"  # underscore: a slug never contains one, so it cannot clash with an anchor


def file_card(doc: DocFile) -> dict:
    """What placement shows Jev about a file: the same text routing reads, never the bodies."""
    return {"file": doc.ref, "title": doc.title, "summary": doc.summary_text(),
            "sections": ["%s — %s" % (s.title, s.blurb) for s in doc.sections]}


def group_card(group: Group) -> dict:
    return {"group": group.ref, "description": group.description,
            "files": ["%s — %s" % (d.title, d.summary_text()) for d in group.files.values()]}


def group_touches_question(group: Group, name: str) -> Type[Noul]:
    """Does this fact touch anything in this group? Asked first, so only its files are asked next."""
    return noul(
        name,
        {"question": "The state is one fact about a change to our software. Does any documentation in this "
                     "group need an edit because of it, or does the fact belong in this group as a new topic?",
         "group": group_card(group)},
        true_means="the fact falls under this group's area, or makes something one of its files says wrong or incomplete",
        false_means="the group's area is unrelated; the fact only shares some words or names with it",
    )


NEW_GROUP = "new_group"  # underscore: a folder slug in the POC never has one; the Choice below reserves it


def new_file_group_question(domain: Domain, name: str = "new_file_group") -> Type[Choice]:
    """Where a NEW file for these facts belongs: which group, or a new one. A Choice, not a Noul per
    group: when no group clearly accepts the facts, the best fit is a relative question."""
    criteria = {g: "%s Files: %s." % (grp.description.rstrip(".") + ".", "; ".join(d.title for d in grp.files.values()))
                for g, grp in domain.groups.items()}
    criteria[NEW_GROUP] = ("none of these areas: the new file starts an area of its own, unrelated to every "
                           "group above")
    return choice(
        name,
        "The state is one or more facts about a change to our software that need a new %s documentation "
        "file, because no existing file covers them. Which group of %s documentation should that new file "
        "go in? Each option is a group's description and the files already in it." % (domain.name, domain.name),
        criteria,
    )


def touches_question(doc: DocFile, name: str) -> Type[Noul]:
    """Does this fact belong in, or change, this file? One per file, all in one request per fact."""
    return noul(
        name,
        {"question": "The state is one fact about a change to our software. Does this documentation file "
                     "need an edit because of it: a new section, or a change to what an existing section says?",
         "file": file_card(doc)},
        true_means="the fact falls under this file's topic, or makes something the file says wrong or incomplete",
        false_means="the file's topic is unrelated; the fact only shares some words or names with it",
    )


def place_question(doc: DocFile, name: str) -> Type[Choice]:
    """Where in this file the fact goes: an existing section, or a new one."""
    criteria = {s.anchor: "%s — %s" % (s.title, s.blurb) for s in doc.sections}
    criteria[NEW_SECTION] = ("none of these sections: the fact is a new rule, mechanism, contract or failure "
                             "that needs a section of its own in this file")
    return choice(
        name,
        "The state is one fact about a change to our software, and it belongs in the %s file %r (%s). "
        "Which section should hold it?" % (doc.domain, doc.title, doc.summary_text()),
        criteria,
    )


def affected_question() -> Type[Noul]:
    """Asked with state = {change, section text}: the one placement hop that reads a body."""
    return noul(
        AFFECTED,
        "The state holds a change to our software and one existing section of our documentation. "
        "After the change ships, is this section wrong, incomplete or misleading unless it is edited?",
        true_means="the section lists, summarises, contradicts or depends on what the change alters, so it must be edited",
        false_means="the section stays true and complete as written",
    )


def affected_state(facts: list, doc: DocFile, title: str, text: str) -> dict:
    return {"change": facts, "documentation_section": {"file": doc.ref, "title": title, "text": text}}
