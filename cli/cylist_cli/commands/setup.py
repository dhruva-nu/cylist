"""``cylist setup`` — one command, from nothing to an agent on the board.

Setting this up used to be six things done in order, each of which had to be
right: mint a token with ``curl`` against an endpoint that needs ``admin``,
hand it to ``cylist login``, run ``cylist hook install``, ``uv sync`` the MCP
server, work out its absolute path, and write that path *and a copy of the
token* into a ``claude mcp add`` line. Six chances to mistype something, one
of which leaves a live credential in a file in the repository.

They are all the same act — *let the agents on this machine use this board* —
so they are one command. It is safe to run again: every step either does
nothing or re-points itself at where things are now.

Three decisions are worth explaining, because each removes a step rather than
automating one.

**The password mints the token.** ``POST /tokens`` needs ``admin``, and the
owner's password already grants everything (``POST /auth/login``). So setup
asks for the password it can prompt for, uses the session to mint a
``read,write`` token — which is all an agent should ever hold — and revokes
the session on the way out. Nobody has to hold an ``admin`` token to give an
agent a narrow one.

**The MCP server reads the CLI's configuration.** It used to be told
``CYLIST_URL`` and ``CYLIST_TOKEN`` through the ``env`` block of a Claude Code
config file, which is how a live token ended up in a project's ``.mcp.json``.
Both processes now read ``~/.config/cylist/config.toml``, mode 0600, so the
registration holds no secret and there is one place to change when anything
moves.

**The server says where else it can be reached.** ``GET /setup`` returns every
address it answers on, and all of them are stored. That is what makes a laptop
that set itself up on a tailnet keep working in a café — see
:mod:`cylist_cli.endpoints` for the order they are tried in.
"""

from __future__ import annotations

import argparse
import getpass
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Any

import httpx

from cylist_cli import config as configuration
from cylist_cli import endpoints, output, system
from cylist_cli.client import Client
from cylist_cli.commands import hook
from cylist_cli.context import Context
from cylist_cli.errors import ApiError, CylistError

AGENT_SCOPES = ("read", "write")
"""What a token for an agent gets when the server has no opinion.

The server states this itself (``GET /setup``'s ``agent_scopes``) so that
widening it is a server-side decision; this is the answer for a deployment too
old to be asked.
"""

MCP_SERVER_NAME = "cylist"
"""The name the MCP server is registered under, and so what ``/mcp`` lists."""

SCOPES = ("user", "project", "local")
"""Claude Code's three configuration scopes. ``user`` is the useful default:
the board is a property of the machine, not of one checkout of one repository.
"""

PROBE_TIMEOUT = httpx.Timeout(10.0, connect=3.0)
"""Per address, while looking for the server. Short: the whole point of trying
several is that the wrong ones are cheap."""

CLAUDE_TIMEOUT = 30
"""Seconds allowed to ``claude mcp``. It edits a JSON file; if it has not
finished in half a minute something is wrong that waiting will not fix."""

Runner = Callable[[Sequence[str]], tuple[int, str]]


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "setup",
        help="Set this machine up: token, hooks and the MCP server, in one command.",
        description=(
            "Finds the server, mints a read,write token for this machine, stores it "
            "0600, installs Claude Code's lifecycle hooks and the /work command, and "
            "registers the Cylist MCP server. Asks for the owner's password once, "
            "unless a working token is already configured. Safe to run again."
        ),
    )
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the owner's password from stdin instead of prompting, for scripts.",
    )
    parser.add_argument(
        "--scope",
        choices=SCOPES,
        default="user",
        help="Which Claude Code scope to register the MCP server in. Default user.",
    )
    parser.add_argument(
        "--mcp-dir",
        help="The 'mcp' directory to run the server from, if it cannot be found.",
    )
    parser.add_argument(
        "--no-hooks",
        action="store_true",
        help="Do not touch Claude Code's hooks; configure the token only.",
    )
    parser.add_argument(
        "--no-mcp",
        action="store_true",
        help="Do not register the MCP server with Claude Code.",
    )
    parser.set_defaults(handler=_setup)


# --- Finding the server ----------------------------------------------------


@dataclass(frozen=True)
class Server:
    """A server that answered, and everything it said about itself."""

    url: str
    """The address that answered, which is the one to prefer from here."""

    urls: tuple[str, ...]
    """Every address to keep, that one first."""

    environment: str
    agent_scopes: tuple[str, ...]

    @property
    def others(self) -> tuple[str, ...]:
        return tuple(url for url in self.urls if url != self.url)


def describe(client: Client) -> dict[str, Any]:
    """Read ``GET /setup``, tolerating a server too old to have it.

    A 404 means a deployment from before this endpoint existed: reachable,
    and everything else works against it — it simply has no other addresses
    to offer. Worth handling rather than failing on, because during a rollout
    the CLI is new for a while before the server is.
    """
    try:
        described = client.get("/setup")
    except ApiError as exc:
        if exc.status_code != httpx.codes.NOT_FOUND:
            raise
        return {}
    return described if isinstance(described, dict) else {}


def advertised_urls(client: Client) -> tuple[str, ...]:
    """Every other address this server answers on, or nothing at all.

    Best-effort: a caller that has already done what it came to do —
    ``cylist login`` has a token and has checked it — should not fail over
    this. It is an improvement to what gets stored, not a requirement.
    """
    try:
        return _advertised(describe(client))
    except CylistError:
        return ()


def _discover(ctx: Context) -> Server:
    """Find the server, and ask it what else it answers on.

    Tried in the order :mod:`cylist_cli.endpoints` gives, which for a first
    run is whatever was configured or ``http://localhost:8000``, and for a
    re-run is every address already stored.
    """
    candidates = endpoints.order(ctx.config.urls)
    refused: list[str] = []

    for url in candidates:
        with ctx.build_client(None, url, timeout=PROBE_TIMEOUT) as client:
            try:
                described = describe(client)
            except CylistError as exc:
                refused.append(f"{url} ({exc.message})")
                continue

        return Server(
            url=url,
            # The address that answered comes first whatever the server said,
            # and appears once: a repeat is a second wait on the same timeout.
            urls=configuration.dedupe((url, *_advertised(described))),
            environment=str(described.get("environment") or "unknown"),
            agent_scopes=_scopes(described),
        )

    raise CylistError(
        "Cannot find a Cylist server. Tried "
        + ", ".join(refused)
        + ". Point at it with --url, or start it with 'make dev'."
    )


def _advertised(described: dict[str, Any]) -> tuple[str, ...]:
    urls = described.get("urls")
    if not isinstance(urls, list):
        return ()
    return tuple(str(url).rstrip("/") for url in urls if isinstance(url, str) and url.strip())


def _scopes(described: dict[str, Any]) -> tuple[str, ...]:
    scopes = described.get("agent_scopes")
    if not isinstance(scopes, list) or not scopes:
        return AGENT_SCOPES
    return tuple(str(scope) for scope in scopes)


# --- Getting a token -------------------------------------------------------


@dataclass(frozen=True)
class Credential:
    token: str
    name: str
    scopes: tuple[str, ...]
    minted: bool
    """False when a token that was already configured turned out to be fine —
    the case on a re-run, and the reason a re-run needs no password."""


def _credential(args: argparse.Namespace, ctx: Context, server: Server) -> Credential:
    """Reuse the configured token if it is usable, otherwise mint one."""
    existing = ctx.config.token
    if existing:
        identity = _identify(ctx, server, existing)
        if identity is not None:
            held = tuple(str(scope) for scope in identity.get("scopes", []))
            if not set(server.agent_scopes) - set(held):
                return Credential(
                    token=existing,
                    name=str(identity.get("label") or "the configured token"),
                    scopes=held,
                    minted=False,
                )

    return _mint(args, ctx, server)


def _identify(ctx: Context, server: Server, token: str) -> dict[str, Any] | None:
    """Ask the server about a token. ``None`` if it will not have it.

    A token that is expired, revoked or simply from another server is not an
    error here: it is the reason to mint a new one.
    """
    with ctx.build_client(token, server.url, timeout=PROBE_TIMEOUT) as client:
        try:
            identity = client.get("/me")
        except ApiError as exc:
            if exc.status_code in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
                return None
            raise
    return identity if isinstance(identity, dict) else None


def _mint(args: argparse.Namespace, ctx: Context, server: Server) -> Credential:
    """Exchange the owner's password for a token scoped to what an agent needs.

    The session the password buys is dropped again before this returns. It
    holds every scope — it is the owner at the keyboard — and there is no
    reason for one to outlive the thirty milliseconds it takes to mint
    something narrower.
    """
    password = _password(args, server)
    name = _token_name()

    with ctx.build_client(None, server.url) as client:
        client.post("/auth/login", {"password": password})
        try:
            issued = client.post("/tokens", {"name": name, "scopes": list(server.agent_scopes)})
        finally:
            _drop_session(client)

    token = str(issued.get("token") or "")
    if not token:
        raise CylistError("The server issued a token but did not return it. Nothing was stored.")
    return Credential(
        token=token,
        name=str(issued.get("name") or name),
        scopes=tuple(str(scope) for scope in issued.get("scopes", server.agent_scopes)),
        minted=True,
    )


def _drop_session(client: Any) -> None:
    """Revoke the session cookie, and never fail over it.

    A session left behind is untidy; a setup that fails *after* minting a
    token, over the tidying, would be worse — the token exists and the user
    would have no idea whether to run this again.
    """
    try:
        client.post("/auth/logout")
    except CylistError:
        return


def _password(args: argparse.Namespace, server: Server) -> str:
    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        output.warn(f"Cylist owner password for {server.url} (input is hidden).")
        password = getpass.getpass("Password: ")

    if not password:
        raise CylistError("No password given; nothing was changed.")
    return password


def _token_name() -> str:
    """Named after the machine, so the token list says whose laptop this is."""
    machine = platform.node().split(".")[0] or "unknown machine"
    return f"{machine} agent"[:120]


# --- Registering the MCP server -------------------------------------------


@dataclass(frozen=True)
class Registration:
    """What became of the attempt to register the MCP server."""

    argv: tuple[str, ...]
    """The command Claude Code will run to start the server."""

    scope: str
    registered: bool
    detail: str
    """One line saying what happened — including what to do by hand when
    nothing could be done automatically."""


def _register(args: argparse.Namespace, spec: tuple[str, ...] | None) -> Registration:
    if spec is None:
        return Registration(
            argv=(),
            scope=args.scope,
            registered=False,
            detail=(
                "Could not find the MCP server. Point at its directory with "
                "--mcp-dir, or install it with 'uv tool install ./mcp'."
            ),
        )

    claude = shutil.which("claude")
    if claude is None:
        return Registration(
            argv=spec,
            scope=args.scope,
            registered=False,
            detail=(
                "Claude Code is not on PATH. Register it yourself with: "
                + _add_command("claude", spec, args.scope)
            ),
        )

    # Removed first, because `add` refuses a name it already knows and this
    # command's promise is that running it again puts things right — the binary
    # may well have moved since the last time.
    _runner([claude, "mcp", "remove", MCP_SERVER_NAME, "--scope", args.scope])
    code, said = _runner(_add_argv(claude, spec, args.scope))
    if code != 0:
        return Registration(
            argv=spec,
            scope=args.scope,
            registered=False,
            detail=f"'claude mcp add' failed: {said or f'exit {code}'}",
        )
    return Registration(
        argv=spec,
        scope=args.scope,
        registered=True,
        detail=f"Registered the '{MCP_SERVER_NAME}' MCP server ({args.scope} scope).",
    )


def _add_argv(claude: str, spec: Sequence[str], scope: str) -> list[str]:
    """``claude mcp add``, with the command as arguments rather than as JSON.

    Not ``add-json``: its payload is a JSON document on the command line, and
    on Windows a batch-file shim has to be run through the command processor,
    which would have to be trusted to carry a string full of quotes through
    intact. Positional arguments have no quotes to lose.
    """
    return [claude, "mcp", "add", MCP_SERVER_NAME, "--scope", scope, "--", *spec]


def _add_command(claude: str, spec: Sequence[str], scope: str) -> str:
    """The same thing as a line somebody can paste."""
    return system.shell_command(_add_argv(claude, spec, scope))


def _mcp_spec(args: argparse.Namespace) -> tuple[str, ...] | None:
    """How to start the MCP server, or ``None`` if it cannot be found.

    Installed on PATH it is one word. In a checkout it is ``uv run`` against
    the ``mcp`` directory, which also installs its dependencies the first time
    — so a clone needs no separate ``uv sync``.

    No ``env`` block either way: the server reads the same 0600 config file
    this command has just written, which is what keeps a live token out of
    Claude Code's configuration.
    """
    installed = shutil.which("cylist-mcp")
    if installed:
        return (str(Path(installed).resolve()),)

    directory = _mcp_dir(args)
    if directory is None:
        return None
    uv = shutil.which("uv")
    if uv is None:
        return None
    return (str(Path(uv).resolve()), "--directory", str(directory), "run", "cylist-mcp")


def _mcp_dir(args: argparse.Namespace) -> Path | None:
    """Where the MCP server's source is: as given, beside us, or below here."""
    if args.mcp_dir:
        given = Path(args.mcp_dir).expanduser().resolve()
        if not (given / "pyproject.toml").is_file():
            raise CylistError(f"{given} does not look like the MCP server's directory.")
        return given

    # ``cli/cylist_cli/commands/setup.py`` → the repository root is four up,
    # which holds for a checkout and simply misses for an installed wheel.
    beside = Path(__file__).resolve().parents[3] / "mcp"
    here = Path.cwd() / "mcp"
    for candidate in (beside, here):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return None


def _run(argv: Sequence[str]) -> tuple[int, str]:
    """Run a command, and return its status and whatever it said."""
    try:
        completed = subprocess.run(  # noqa: S603 - argv is built here, never from input
            _executable(argv),
            capture_output=True,
            text=True,
            check=False,
            timeout=CLAUDE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    return completed.returncode, (completed.stdout + completed.stderr).strip()


def _executable(argv: Sequence[str]) -> list[str]:
    """The same command, runnable.

    Claude Code installs on Windows as ``claude.cmd``, a batch shim, and
    ``CreateProcess`` cannot start one of those — the error is a bare
    "%1 is not a valid Win32 application", which says nothing about batch
    files at all. The command processor can, so it goes in front.
    """
    listed = list(argv)
    if not system.windows() or not listed:
        return listed
    if PurePath(listed[0]).suffix.lower() not in {".cmd", ".bat"}:
        return listed
    return [os.environ.get("COMSPEC", "cmd.exe"), "/c", *listed]


_runner: Runner = _run
"""Replaced by the tests, which must not run the real ``claude``."""


# --- The command -----------------------------------------------------------


def _setup(args: argparse.Namespace, ctx: Context) -> None:
    server = _discover(ctx)
    credential = _credential(args, ctx, server)
    path = configuration.save(server.url, credential.token, urls=server.urls)

    installed = None if args.no_hooks else hook.install_hooks()
    registration = None if args.no_mcp else _register(args, _mcp_spec(args))

    if ctx.as_json:
        output.emit_json(
            {
                "url": server.url,
                "urls": list(server.urls),
                "environment": server.environment,
                "token": {
                    "name": credential.name,
                    "scopes": list(credential.scopes),
                    "minted": credential.minted,
                },
                "config_path": str(path),
                "hooks": None
                if installed is None
                else {
                    "settings": str(installed.settings_path),
                    "command": installed.command,
                    "added": installed.added,
                    "work_command": str(installed.work_command),
                },
                "mcp": None
                if registration is None
                else {
                    "name": MCP_SERVER_NAME,
                    "command": list(registration.argv),
                    "scope": registration.scope,
                    "registered": registration.registered,
                    "detail": registration.detail,
                },
            }
        )
        return

    _report(server, credential, path, installed, registration)


def _report(
    server: Server,
    credential: Credential,
    path: Path,
    installed: hook.HookInstall | None,
    registration: Registration | None,
) -> None:
    also = f", also on {', '.join(server.others)}" if server.others else ""
    output.echo(f"Cylist at {server.url} ({server.environment}){also}.")

    scopes = ", ".join(credential.scopes) or "none"
    if credential.minted:
        output.echo(f"Minted '{credential.name}' with {scopes}.")
    else:
        output.echo(f"Kept the token already configured ({credential.name}, {scopes}).")
    output.echo(f"Wrote {path} ({configuration.describe_protection(path)}).")

    if installed is not None:
        what = ", ".join(installed.added) if installed.added else "nothing new"
        output.echo(f"Claude Code hooks in {installed.settings_path}: {what}.")
        output.echo(f"Wrote {installed.work_command} — type /work <REF> in a session.")
    if registration is not None:
        output.echo(registration.detail)

    output.echo()
    if installed is None and registration is None:
        output.echo("Ready. 'cylist board <KEY>' to see a board.")
        return
    output.echo("Open a new Claude Code session, then 'cylist work <REF>' or /work <REF>.")
