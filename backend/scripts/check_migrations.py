"""Prove the migrations produce exactly the schema the models describe.

Three things are checked, in order:

1. ``upgrade head`` applies every revision to an empty database.
2. ``alembic check`` finds no difference between the result and the ORM models,
   which is what catches a model edited without a matching migration.
3. ``downgrade base`` then ``upgrade head`` proves every revision reverses.

All of it runs against a scratch database created here and dropped afterwards.
That matters: the test suite empties its tables on teardown but leaves the
``alembic_version`` stamp behind, so a shared database would look like it were
at head while holding no tables at all, and the check would fail for a reason
that has nothing to do with the migrations.

Usage::

    uv run python -m scripts.check_migrations            # uses CYLIST_DATABASE_URL
    uv run python -m scripts.check_migrations --url ...
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from alembic.config import Config
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command

BACKEND_ROOT = Path(__file__).resolve().parent.parent
SCRATCH_DATABASE = "cylist_migration_check"


async def _run_on_maintenance_db(url: URL, statements: list[str]) -> None:
    """Execute statements against the server's default database.

    CREATE and DROP DATABASE cannot run inside a transaction, hence AUTOCOMMIT.
    """
    engine = create_async_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            for statement in statements:
                await connection.exec_driver_sql(statement)
    finally:
        await engine.dispose()


def _alembic_config() -> Config:
    """Alembic pointed at this repository.

    No URL: ``env.py`` reads that from the environment, and pushing one through
    the ini file would fail on any URL containing a percent sign.
    """
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=os.environ.get("CYLIST_DATABASE_URL"),
        help="Any database on the target server; a scratch database is made beside it.",
    )
    args = parser.parse_args(argv)

    if not args.url:
        parser.error("Pass --url or set CYLIST_DATABASE_URL.")

    base_url = make_url(args.url)
    scratch_url = base_url.set(database=SCRATCH_DATABASE)

    asyncio.run(
        _run_on_maintenance_db(
            base_url,
            [
                f'DROP DATABASE IF EXISTS "{SCRATCH_DATABASE}"',
                f'CREATE DATABASE "{SCRATCH_DATABASE}"',
            ],
        )
    )

    # env.py reads the URL from the environment, and is imported for the
    # first time by the commands below, so this has to be set before them.
    os.environ["CYLIST_DATABASE_URL"] = scratch_url.render_as_string(hide_password=False)
    config = _alembic_config()

    try:
        print("→ upgrade head")
        command.upgrade(config, "head")

        print("→ check for drift between the models and the migrations")
        command.check(config)

        print("→ downgrade base")
        command.downgrade(config, "base")

        print("→ upgrade head again")
        command.upgrade(config, "head")
    finally:
        asyncio.run(
            _run_on_maintenance_db(base_url, [f'DROP DATABASE IF EXISTS "{SCRATCH_DATABASE}"'])
        )

    print("\nMigrations match the models and reverse cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
