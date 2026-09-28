"""jevdocs: route a question to `domain/file.md#section` using the Jev System One API.

    from jevdocs import load, Router, mock_client
    corpus = load("docs")
    router = Router(corpus, Jev())            # or mock_client(corpus) offline
    print(router.route("why do we keep 20 copies of Book A?").explain())
"""
from .corpus import Corpus, DocFile, Domain, Group, Problem, Section, lint, load, parse_text
from .mock import MockJev, mock_client
from .check import Report, check, diff, load_snapshot, save_snapshot, snapshot
from .placement import Action, Change, Fact, Placer, Plan, load_change
from .router import AMBIGUOUS, NOT_DOCUMENTED, OK, UNVERIFIED, Candidate, Hop, Route, Router

__all__ = [
    "Corpus", "DocFile", "Domain", "Group", "Section", "Problem", "load", "lint", "parse_text",
    "Router", "Route", "Hop", "Candidate", "OK", "AMBIGUOUS", "UNVERIFIED", "NOT_DOCUMENTED",
    "MockJev", "mock_client",
    "Placer", "Plan", "Action", "Change", "Fact", "load_change",
    "check", "Report", "snapshot", "diff", "save_snapshot", "load_snapshot",
]
