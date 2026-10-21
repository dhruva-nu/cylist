"""Small operator commands.

These are the two things you need before the first login::

    uv run python -m app.cli hash-password
    uv run python -m app.cli generate-vault-key

Both print a value to paste into ``.env``. Neither touches the database.

One more is for later, and does::

    uv run python -m app.cli blobs gc

It takes uploaded bytes no row refers to off the disk now, rather than at the
application's next hourly sweep — after deleting a large project, say. In a
deployment it runs inside the app container, against the configuration the
server itself runs with::

    docker compose -f docker-compose.prod.yml exec app python -m app.cli blobs gc

``hash-password`` makes the *bootstrap* password: the one login that belongs
to nobody, accepted only while no person in the directory has an account. It
is how a fresh deployment is opened for the first time, and it stops working
the moment somebody opens an account from it. Nobody signs in with it day to
day — everybody has their own email and password after that — so there is no
reason to keep it anywhere but ``.env``.

This is not the ``cylist`` CLI for day-to-day use — that one arrives with the
agent tooling and talks to the HTTP API.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import getpass
import secrets
import sys
from datetime import timedelta

from app.auth.passwords import hash_password_sync
from app.config import get_settings
from app.db import Database
from app.services import blobs
from app.storage import LocalBlobStore

_VAULT_KEY_BYTES = 32


def _hash_password() -> int:
    password = getpass.getpass("Bootstrap password: ")
    if not password:
        print("Nothing entered; no hash produced.", file=sys.stderr)
        return 1
    if password != getpass.getpass("Repeat password: "):
        print("The two entries differ; no hash produced.", file=sys.stderr)
        return 1
    print(f"\nCYLIST_PASSWORD_HASH={hash_password_sync(password)}")
    return 0


def _generate_vault_key() -> int:
    key = base64.urlsafe_b64encode(secrets.token_bytes(_VAULT_KEY_BYTES)).decode()
    print(f"CYLIST_VAULT_KEY={key}")
    print(
        "\nStore this somewhere you can recover it. Vault secrets encrypted "
        "with this key cannot be read without it.",
        file=sys.stderr,
    )
    return 0


async def _sweep(idle_for: timedelta) -> blobs.Swept:
    settings = get_settings()
    database = Database(settings.database_url)
    try:
        return await blobs.sweep(database, LocalBlobStore(settings.blob_dir), idle_for=idle_for)
    finally:
        await database.dispose()


def _blobs_gc(idle_minutes: int) -> int:
    if idle_minutes < 0:
        print("--idle-minutes cannot be negative.", file=sys.stderr)
        return 2
    swept = asyncio.run(_sweep(timedelta(minutes=idle_minutes)))
    print(f"Removed {swept.orphans} unreferenced blob(s) and {swept.incoming} abandoned upload(s).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser(
        "hash-password",
        help="Hash the bootstrap password for CYLIST_PASSWORD_HASH.",
    )
    subcommands.add_parser("generate-vault-key", help="Generate CYLIST_VAULT_KEY.")
    blob_commands = subcommands.add_parser("blobs", help="Uploaded bytes on disk.").add_subparsers(
        dest="blob_command", required=True
    )
    gc = blob_commands.add_parser(
        "gc", help="Remove bytes no row refers to, and abandoned uploads, now."
    )
    gc.add_argument(
        "--idle-minutes",
        type=int,
        default=int(blobs.SWEEP_IDLE_FOR.total_seconds() // 60),
        help=(
            "Leave anything touched more recently than this alone (default: %(default)s). "
            "0 sweeps everything, which is safe for published bytes but would also "
            "take an upload still arriving."
        ),
    )

    args = parser.parse_args(argv)
    if args.command == "blobs":
        return _blobs_gc(args.idle_minutes)
    handlers = {"hash-password": _hash_password, "generate-vault-key": _generate_vault_key}
    return handlers[args.command]()


if __name__ == "__main__":
    raise SystemExit(main())
