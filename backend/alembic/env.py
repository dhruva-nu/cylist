"""Alembic environment.

Reads the database URL from application settings so there is one source of
truth, and runs migrations through the async engine the app itself uses.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import Enum, pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from app.config import get_settings
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

DATABASE_URL = get_settings().database_url
"""Where the migrations run.

Used directly rather than written back into the ini file: ConfigParser reads a
bare ``%`` as interpolation, and a perfectly ordinary URL — a percent-encoded
password, or the ``?host=%2Ftmp%2F…`` of a unix socket — is full of them.
"""

target_metadata = Base.metadata

ENUM_CHECKS = frozenset(
    f"ck_{table.name}_{column.type.name}"
    for table in target_metadata.tables.values()
    for column in table.columns
    if isinstance(column.type, Enum) and column.type.create_constraint
)
"""The CHECKs the non-native enums bring with them, by name.

Autogenerate leaves these out of the models' side of its comparison — they
belong to the type rather than the table — but reflects them from the database
all the same, so each would read as a constraint to drop. They are compared
member by member in ``tests/test_migrations.py`` instead.
"""


def _include_name(name: str | None, type_: str, parent_names: object) -> bool:
    return not (type_ == "check_constraint" and name in ENUM_CHECKS)


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,  # notice column type changes
        compare_server_default=True,
        include_name=_include_name,
        render_as_batch=False,
    )


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting, for review or manual apply."""
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run(connection: Connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Apply migrations against the configured database."""
    # NullPool: this engine runs one connection once and is thrown away, so a
    # pool would only leave sockets open for the process to shut down.
    engine = create_async_engine(DATABASE_URL, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
