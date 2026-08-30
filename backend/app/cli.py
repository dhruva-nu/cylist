"""Small operator commands.

These are the two things you need before the first login::

    uv run python -m app.cli hash-password
    uv run python -m app.cli generate-vault-key

Both print a value to paste into ``.env``. Neither touches the database.

This is not the ``cylist`` CLI for day-to-day use — that one arrives with the
agent tooling and talks to the HTTP API.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import secrets
import sys

from app.auth.passwords import hash_password

_VAULT_KEY_BYTES = 32


def _hash_password() -> int:
    password = getpass.getpass("New owner password: ")
    if not password:
        print("Nothing entered; no hash produced.", file=sys.stderr)
        return 1
    if password != getpass.getpass("Repeat password: "):
        print("The two entries differ; no hash produced.", file=sys.stderr)
        return 1
    print(f"\nCYLIST_PASSWORD_HASH={hash_password(password)}")
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("hash-password", help="Hash a password for CYLIST_PASSWORD_HASH.")
    subcommands.add_parser("generate-vault-key", help="Generate CYLIST_VAULT_KEY.")

    args = parser.parse_args(argv)
    handlers = {"hash-password": _hash_password, "generate-vault-key": _generate_vault_key}
    return handlers[args.command]()


if __name__ == "__main__":
    raise SystemExit(main())
