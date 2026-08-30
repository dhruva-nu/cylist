"""Database engine, session lifecycle and the FastAPI dependency.

One request is one transaction: the session yielded by :func:`get_session`
commits when the handler returns and rolls back if it raises. Handlers
therefore never call ``commit`` themselves.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Request
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
    """FastAPI dependency returning the request-scoped session."""
    database: Database = request.app.state.database
    async with database.session() as session:
        yield session
