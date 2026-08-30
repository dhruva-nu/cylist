"""Database engine, session lifecycle and the FastAPI dependency.

One request is one transaction: the session yielded by :func:`get_session`
commits when the handler returns and rolls back if it raises. Handlers
therefore never call ``commit`` themselves.

*When* that commit happens matters as much as that it happens, which is what
:data:`SessionDependency` is for — see its note.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


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

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield a session wrapped in a transaction."""
        async with self._session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            else:
                await session.commit()

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
