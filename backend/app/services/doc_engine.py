"""Asking jev-docs about a project's docs: which section answers a question, and
where a new fact belongs.

jev-docs (``vendor/jev-docs``) is built on TypeSafe's Jev System One model
(https://api.typesafe.ai), which answers typed questions about a piece of
content with calibrated probabilities rather than generated text. Two things
are asked of it here:

* **ask** routes an agent's question to the one section of the docs that
  answers it — section, topic, doc, then the section itself read against the
  question — about five requests. What comes back says how sure it is: a
  section that passed the relevance check (``ok``), a best guess that did not
  (``unverified``), or nothing written on it (``not_documented``);
* **place** takes the facts an agent found in the code when the docs had
  nothing, and plans where each one goes: which doc, which section, or a doc
  of its own. It writes nothing; the agent does.

The corpus they read is built from the project's rows by
:mod:`app.services.doc_corpus`; what to do with the answers belongs to
:mod:`app.services.docs`. Behind a :class:`DocEngine` so the tests can stand a
fake in for the network, and so a deployment with no key configured has no
engine at all rather than one that always fails.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from jev import Jev, JevError
from jevdocs import Change, Corpus, Placer, Plan, Route, Router
from starlette.concurrency import run_in_threadpool

from app.config import Settings


class EngineUnavailableError(Exception):
    """jev could not be asked, or did not answer in a usable shape."""


class DocEngine(Protocol):
    async def ask(self, corpus: Corpus, question: str) -> Route:
        """The section of ``corpus`` that answers ``question``, as jev-docs routes it."""
        ...

    async def place(self, corpus: Corpus, change: Change) -> Plan:
        """Where each of ``change``'s facts belongs in ``corpus``."""
        ...


class JevDocEngine:
    """A :class:`DocEngine` that runs jev-docs against a Jev client.

    jev-docs is synchronous, and fans its own requests out over a thread pool,
    so each call runs in a worker thread rather than on the event loop.
    """

    def __init__(self, client: Callable[[Corpus], Jev], *, relevance_threshold: float) -> None:
        self._client = client
        self.relevance_threshold = relevance_threshold

    async def ask(self, corpus: Corpus, question: str) -> Route:
        def route() -> Route:
            with self._client(corpus) as jev:
                router = Router(corpus, jev, relevance_threshold=self.relevance_threshold)
                routed: Route = router.route(question)
                return routed

        return await self._run(route)

    async def place(self, corpus: Corpus, change: Change) -> Plan:
        def plan() -> Plan:
            with self._client(corpus) as jev:
                planned: Plan = Placer(corpus, jev).place(change)
                return planned

        return await self._run(plan)

    @staticmethod
    async def _run[T](work: Callable[[], T]) -> T:
        try:
            return await run_in_threadpool(work)
        except JevError as exc:
            raise EngineUnavailableError(str(exc)) from exc
        except (KeyError, ValueError, TypeError) as exc:
            # An answer naming a label that was not offered, or one not of the
            # asked type: jev answered, but not in a shape jev-docs can use.
            raise EngineUnavailableError(
                f"jev answered in a shape jev-docs cannot read: {exc!r}"
            ) from exc


def engine_for(settings: Settings) -> DocEngine | None:
    """The engine this deployment is configured for, or None without a key."""
    if not settings.jev_api_key:
        return None

    def client(_: Corpus) -> Jev:
        # One retry, not the SDK's two: a question stands between an agent and
        # its work, and reading the code is a better answer than a third wait.
        return Jev(
            settings.jev_api_key,
            model=settings.jev_model,
            timeout=settings.jev_timeout_seconds,
            max_retries=1,
        )

    return JevDocEngine(client, relevance_threshold=settings.doc_relevance_threshold)
