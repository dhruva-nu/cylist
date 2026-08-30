"""Argument parsing, dispatch, and the only place an exception is caught.

**Why argparse rather than typer.** Three reasons, in order of weight:

1. *No runtime dependency but httpx.* The CLI's whole job is to prove the API
   is enough on its own. A tool that installs in one step, from the standard
   library plus one HTTP client, makes that easier to believe than one that
   pulls in a framework and its click dependency to parse ``--json``.
2. *The exit path is visible.* Every failure in this package funnels through
   :func:`main` below, which decides the status code and what reaches stderr.
   Typer installs its own exception handling and its own ``rich`` traceback
   rendering, and "never show the user a traceback" then becomes a matter of
   configuring someone else's error path correctly rather than owning it.
3. *mypy --strict likes it.* argparse is fully typed in typeshed. Typer's
   decorator-and-annotation style is expressive, but its inferred signatures
   are a recurring source of strict-mode noise in exchange for help text we
   are writing by hand anyway.

The cost is real: the parser below is verbose where typer would be terse. For
roughly twenty subcommands that is an acceptable trade, and it buys a CLI whose
behaviour is entirely readable in this file plus ``commands/``.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

import httpx

from cylist_cli import __version__, output
from cylist_cli import config as configuration
from cylist_cli.commands import REGISTRARS
from cylist_cli.context import Context
from cylist_cli.errors import ApiError, CylistError

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_INTERRUPTED = 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cylist",
        description=(
            "Cylist from the command line. Everything here is an HTTP call to "
            "the same /api/v1 the web app uses — there is no private door."
        ),
        epilog=(
            "Configuration: CYLIST_URL and CYLIST_TOKEN, or ~/.config/cylist/config.toml "
            "written by 'cylist login'. There is no --token flag on purpose: an argument "
            "would be recorded in your shell history."
        ),
    )
    parser.add_argument("--version", action="version", version=f"cylist {__version__}")
    # dest is not `url`: a subcommand may have a --url of its own that means
    # something else entirely (`vault add --url` is the login page a credential
    # belongs to), and argparse gives every parser in the chain one shared
    # namespace. Without a distinct dest the two silently overwrite each other
    # and the CLI ends up pointing at whatever the subcommand's URL was.
    parser.add_argument(
        "--url",
        dest="server_url",
        help=f"The Cylist server. Default {configuration.DEFAULT_URL}.",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Print the API's own JSON instead of a table. Available on every command.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True, metavar="<command>")
    for register in REGISTRARS:
        register(subparsers)
    return parser


def main(argv: Sequence[str] | None = None, *, transport: httpx.BaseTransport | None = None) -> int:
    """Run one command and return its exit status.

    ``transport`` exists for the tests, which drive every command against an
    ``httpx.MockTransport`` rather than a live server.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    ctx = Context(
        config=configuration.load(args.server_url),
        as_json=bool(args.as_json),
        transport=transport,
    )

    try:
        args.handler(args, ctx)
    except CylistError as exc:
        _report(exc, as_json=ctx.as_json)
        return EXIT_FAILURE
    except KeyboardInterrupt:  # pragma: no cover - depends on a real terminal
        output.warn("Interrupted.")
        return EXIT_INTERRUPTED
    except BrokenPipeError:  # pragma: no cover - depends on a real pipe
        # `cylist tasks ls ATL | head` closes the pipe under us. That is the
        # user getting what they asked for, not an error worth a message.
        return EXIT_OK
    finally:
        ctx.close()

    return EXIT_OK


def _report(exc: CylistError, *, as_json: bool) -> None:
    """One line on stderr, or the error envelope when --json was asked for."""
    if as_json:
        envelope: dict[str, Any] = {
            "error": {"code": exc.code, "message": exc.message, "details": exc.details}
        }
        if isinstance(exc, ApiError):
            envelope["error"]["status"] = exc.status_code
        print(json.dumps(envelope, indent=2, default=str), file=sys.stderr)
        return

    output.warn(f"error: {exc.message}")


def run() -> None:
    """Console-script entry point."""
    sys.exit(main())
