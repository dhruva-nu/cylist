"""Database engine, session lifecycle and the FastAPI dependency.

One request is one transaction: the session yielded by :func:`get_session`
commits when the handler returns and rolls back if it raises. Handlers
therefore never call ``commit`` themselves.

*When* that commit happens matters as much as that it happens, which is what
:data:`SessionDependency` is for — see its note. The same timing is why the
outbox exists: a session collects the news it wants told, and
:meth:`Database.session` tells it only once the transaction has committed.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

logger = logging.getLogger(__name__)

OUTBOX = "outbox"
"""Key under ``session.info`` where pending events wait for the commit.

Written by :func:`app.services.activity.record`, drained here. It lives on
the session rather than in a context variable because its lifetime is exactly
a transaction's: a rollback must discard the news along with the rows, and
that falls out for free if the two are the same object.
"""


def publish_into(session: AsyncSession, event: dict[str, Any]) -> None:
    """Queue one event to be published if — and only if — this session commits.

    The alternative, publishing where the change is made, is the bug
    :data:`SessionDependency` already documents, arriving faster: a client
    told that something happened refetches immediately and reads the state
    from before the commit.
    """
    session.info.setdefault(OUTBOX, []).append(event)


class Database:
    """Owns the connection pool for the lifetime of the application."""

    def __init__(self, url: str, *, echo: bool = False) -> None:
        self._engine: AsyncEngine = create_async_engine(
            url,
            echo=echo,
            pool_pre_ping=True,  # survive a Postgres restart without a failed request
        )
        self._session_factory = async_sessionmaker(
            self._engine,
            expire_on_commit=False,  # attributes stay readable after commit
            autoflush=False,
        )
        self._publish: Callable[[dict[str, Any]], None] | None = None
        """Where committed events go. ``None`` until something is listening,
        which is the honest default: a pool can exist before there is anyone
        to tell, and every test that does not care about pushes gets silence
        for free."""

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    def publish_to(self, publish: Callable[[dict[str, Any]], None] | None) -> None:
        """Send committed events here from now on.

        Set by whoever owns both the pool and the audience — the application's
        lifespan in production, a fixture in tests. Kept off the constructor
        because the two are built in different orders in those two places, and
        a database that has to know its hub up front cannot be reused by an
        app that builds one later.
        """
        self._publish = publish

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield a session wrapped in a transaction, then tell the news.

        The outbox is drained *after* the commit, so nothing is announced that
        another connection cannot yet read. A rollback discards it untold.
        """
        async with self._session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            else:
                await session.commit()
                self._drain(session)

    def _drain(self, session: AsyncSession) -> None:
        """Publish what the committed transaction queued.

        Never allowed to fail the request it belongs to: the write has already
        landed, and a board that missed a nudge refetches on its next one.
        """
        events = session.info.pop(OUTBOX, [])
        if not events or self._publish is None:
            return
        for event in events:
            try:
                self._publish(event)
            except Exception:  # pragma: no cover - defensive
                logger.exception("Failed to publish a committed event")

    async def dispose(self) -> None:
        """Close every pooled connection. Called on shutdown."""
        await self._engine.dispose()


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency returning the request-scoped session.

    Depend on :data:`SessionDependency` rather than on this directly.
    """
    database: Database = request.app.state.database
    async with database.session() as session:
        yield session


SessionDependency = Depends(get_session, scope="function")
"""The session, committed before the response leaves.

``scope="function"`` is the whole point. FastAPI closes a ``yield``
dependency at one of two moments: at the end of the *function*, before the
response is sent, or at the end of the *request*, after it has already gone
out — and the second is the default. Since the commit lives in that closing
code, the default means a client can be holding a 201 for a row no other
connection can see yet.

That is not a theoretical window. It is exactly what a browser does: a
mutation succeeds, its success handler immediately refetches the list, and the
list comes back without the thing that was just created — until the page is
reloaded and it appears, having committed in the meantime.
"""
