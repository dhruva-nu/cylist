"""Small operator commands.

These are the two things you need before the first login::

    uv run python -m app.cli hash-password
    uv run python -m app.cli generate-vault-key

Both print a value to paste into ``.env``. Neither touches the database.

One more is for later, when a vault key is rotated::

    uv run python -m app.cli vault-reseal

It re-encrypts every stored secret under the newest key in the keyring, so the
older keys can be retired — see :mod:`app.services.reseal` and DEPLOY.md.

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

from app.auth.passwords import hash_password_sync
from app.config import Settings, get_settings
from app.core.crypto import VaultCipher, VaultUnavailableError, cipher_for
from app.db import Database
from app.services.reseal import BATCH, ResealReport, reseal

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


def _vault_reseal(settings: Settings, batch: int) -> int:
    try:
        cipher = cipher_for(settings.vault_keyring)
    except VaultUnavailableError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    print(f"Resealing every secret under vault key version {cipher.version}.")
    report = asyncio.run(_reseal(settings.database_url, cipher, batch))
    _print_reseal(report)
    return 1 if report.unreadable else 0


async def _reseal(url: str, cipher: VaultCipher, batch: int) -> ResealReport:
    database = Database(url)
    try:
        return await reseal(database, cipher, batch=batch)
    finally:
        await database.dispose()


def _print_reseal(report: ResealReport) -> None:
    for table, count in report.resealed.items():
        print(f"  {table}: {count} resealed")
    for row in report.unreadable:
        print(
            f"  {row.table} {row.row_id} (version {row.key_version}) left as it was: {row.reason}",
            file=sys.stderr,
        )
    in_use = ", ".join(f"version {v}: {n}" for v, n in report.remaining.items()) or "none"
    print(f"Rows on each key now: {in_use}.")
    retirable = [str(version) for version in report.retirable if version != report.version]
    if retirable:
        print(
            f"No row needs version(s) {', '.join(retirable)} any more; those keys can be retired."
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser(
        "hash-password",
        help="Hash the bootstrap password for CYLIST_PASSWORD_HASH.",
    )
    subcommands.add_parser("generate-vault-key", help="Generate CYLIST_VAULT_KEY.")
    resealing = subcommands.add_parser(
        "vault-reseal",
        help="Re-encrypt every vault and hook secret under the newest vault key.",
    )
    resealing.add_argument(
        "--batch-size",
        type=int,
        default=BATCH,
        help=f"Rows per transaction (default {BATCH}).",
    )

    args = parser.parse_args(argv)
    if args.command == "vault-reseal":
        if args.batch_size < 1:
            parser.error("--batch-size must be at least 1")
        return _vault_reseal(get_settings(), args.batch_size)
    handlers = {"hash-password": _hash_password, "generate-vault-key": _generate_vault_key}
    return handlers[args.command]()


if __name__ == "__main__":
    raise SystemExit(main())
