"""``cylist login`` and ``cylist whoami``."""

from __future__ import annotations

import argparse
import getpass
import sys
from typing import Any

from cylist_cli import config as configuration
from cylist_cli import output
from cylist_cli.context import Context
from cylist_cli.errors import CylistError


def register(subparsers: Any) -> None:
    login = subparsers.add_parser(
        "login",
        help="Store an API token for this machine.",
        description=(
            "Save an API token to ~/.config/cylist/config.toml with mode 0600. "
            "The token is read from a prompt that does not echo, or from stdin "
            "with --token-stdin — never from an argument, which the shell would "
            "record in its history."
        ),
    )
    login.add_argument("--url", help="The Cylist server to store alongside the token.")
    login.add_argument(
        "--token-stdin",
        action="store_true",
        help="Read the token from stdin instead of prompting, for scripts.",
    )
    login.set_defaults(handler=_login)

    whoami = subparsers.add_parser(
        "whoami",
        help="Show which token is in use and what it may do.",
    )
    whoami.set_defaults(handler=_whoami)


def _login(args: argparse.Namespace, ctx: Context) -> None:
    url = args.url or ctx.config.url

    if args.token_stdin:
        token = sys.stdin.readline().strip()
    else:
        output.warn(f"Paste an API token for {url} (input is hidden).")
        token = getpass.getpass("Token: ").strip()

    if not token:
        raise CylistError("No token given; nothing was written.")

    # Check before writing. Storing a token that does not work turns the next
    # command's failure into a puzzle about which of the two steps went wrong.
    with ctx.build_client(token, url) as client:
        identity = client.get("/me")

    path = configuration.save(url, token)
    mode = configuration.describe_mode(path)

    if ctx.as_json:
        output.emit_json({"url": url, "config_path": str(path), "mode": mode, **identity})
        return

    scopes = ", ".join(identity.get("scopes", [])) or "none"
    output.echo(f"Signed in to {url} as {identity.get('label', 'unknown')}.")
    output.echo(f"Scopes: {scopes}")
    output.echo(f"Token written to {path} (mode {mode} — owner read/write only).")


def _whoami(_: argparse.Namespace, ctx: Context) -> None:
    identity = ctx.client.get("/me")
    if ctx.as_json:
        output.emit_json(identity)
        return

    output.fields(
        [
            ("Server", ctx.config.url),
            ("Token", ctx.config.token_source),
            ("Label", str(identity.get("label", ""))),
            ("Channel", str(identity.get("channel", ""))),
            ("Scopes", ", ".join(identity.get("scopes", [])) or "none"),
        ]
    )
