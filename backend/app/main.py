"""Application entry point.

Run with::

    uvicorn app.main:app --reload

The API is mounted under ``/api/v1``. Its OpenAPI document at
``/api/v1/openapi.json`` is the contract every client is built from — the
React app's TypeScript types, and later the CLI and MCP server.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.db import Database
from app.routers import api_router

API_PREFIX = "/api/v1"

Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]

DESCRIPTION = """
Cylist keeps a project's **board**, **files**, **vault** and **people** in one
place — for you in the browser, and for your agents over the same HTTP API.

Authenticate with either an `Authorization: Bearer cyl_…` header or the
session cookie set by `POST /auth/login`. Tokens carry scopes; read each
endpoint's description to see which one it needs.
""".strip()


def build_lifespan(settings: Settings) -> Lifespan:
    """Create the startup/shutdown handler for a given configuration."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.blob_dir.mkdir(parents=True, exist_ok=True)
        app.state.database = Database(settings.database_url, echo=settings.database_echo)
        try:
            yield
        finally:
            await app.state.database.dispose()

    return lifespan


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application.

    Taking settings as an argument keeps the factory testable: a test can build
    an app pointed at a throwaway database without touching the environment.
    """
    settings = settings or get_settings()

    app = FastAPI(
        title="Cylist",
        version="0.1.0",
        description=DESCRIPTION,
        lifespan=build_lifespan(settings),
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=None,
    )

    # Set eagerly, not in the lifespan: tests build an app without running the
    # lifespan, and every request needs to reach this configuration.
    app.state.settings = settings

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,  # required for the session cookie
            allow_methods=["*"],
            allow_headers=["*"],
        )

    register_exception_handlers(app)
    app.include_router(api_router, prefix=API_PREFIX)
    return app


app = create_app()
