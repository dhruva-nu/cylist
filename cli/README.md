# `cylist` — the command line

Everything the web app can do, from a terminal. This package talks to the
Cylist HTTP API over `/api/v1` and imports nothing from `backend/`; if a
command here works, the API is genuinely enough on its own.

```
uv sync
uv run cylist --help
```

To install it on your PATH rather than running it through `uv run`:

```
uv tool install .
```

## Setting up, in one command

From a machine that has never seen a board and has none of this installed,
one line is the whole of it — no clone, no `make`, nothing outside your home
directory and nothing needing `sudo`:

```
# Linux and macOS
curl -fsSL https://raw.githubusercontent.com/dhruva-nu/cylist/main/scripts/install.sh \
  | sh -s -- --url https://cylist.example.ts.net
```

```powershell
# Windows PowerShell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/dhruva-nu/cylist/main/scripts/install.ps1))) `
  -Url https://cylist.example.ts.net
```

The PowerShell invocation looks like that, rather than `irm … | iex`, because
`iex` on a piped string has nowhere to put `-Url`. Saved to a file it is the
ordinary `.\install.ps1 -Url …`.

Both scripts install [uv](https://docs.astral.sh/uv/) if it is missing,
install this CLI and the `cylist-mcp` server from the repository, and then run
the command below. `--ref` installs from a branch or tag rather than `main`,
and `--repo` from a fork; the MCP server is always taken from the same ref as
the CLI, so a machine cannot end up with halves from two branches.

With the CLI already installed, that last step on its own is:

```
cylist setup
```

It finds
the server, asks for the owner's password **once**, mints a `read,write` token
named after this machine, stores it `0600`, installs Claude Code's lifecycle
hooks and the `/work` command, and registers the Cylist MCP server. Run it
again after a reinstall and it re-points everything at where things are now —
without asking for the password, because a token that already works is kept.

```
$ cylist setup
Cylist owner password for http://localhost:8000 (input is hidden).
Password:
Cylist at http://localhost:8000 (dev), also on https://dnu-home-1.tail222f46.ts.net.
Minted 'my-laptop agent' with read, write.
Wrote /home/you/.config/cylist/config.toml (mode 0600 — owner only).
Claude Code hooks in /home/you/.claude/settings.json: SessionStart, UserPromptSubmit, PostToolUse, Stop, Notification, SessionEnd.
Wrote /home/you/.claude/commands/work.md — type /work <REF> in a session.
Registered the 'cylist' MCP server (user scope).

Open a new Claude Code session, then 'cylist work <REF>' or /work <REF>.
```

From a clone, `make agent` does the `uv tool install` first and then this.

| Flag | For |
|---|---|
| `--url URL` | a server that is not `http://localhost:8000` |
| `--password-stdin` | provisioning scripts |
| `--scope user\|project\|local` | which Claude Code scope the MCP server goes in (default `user`) |
| `--no-hooks`, `--no-mcp` | configure the token and nothing else |
| `--mcp-dir PATH` | where the MCP server lives, if it cannot be found |
| `--mcp-source URL` | where to install it from when it cannot be found at all |
| `--no-install` | never fetch anything; print what to run instead |

**Where the MCP server comes from.** Three answers, in this order: whatever
`cylist-mcp` is on your PATH; an `mcp` directory beside the CLI, which a clone
has; and otherwise `uv tool install` from this repository, because a machine
that installed the CLI from git has a checkout of nothing. That last case is
the ordinary one for a laptop joining a board, and it used to end the command
with "could not find the MCP server" and a piece of homework. `--mcp-source`
points it at a fork, and `CYLIST_MCP_SOURCE` does the same from the
environment — which is how the bootstrap scripts keep the CLI and the server
on one ref.

**Why it asks for the password rather than a token.** Minting a token needs the
`admin` scope, and the owner's password already grants everything. So setup
borrows a session with it, mints the narrow token an agent should hold, and
revokes the session on the way out — nobody has to keep an `admin` token around
in order to hand out narrow ones.

**The MCP registration holds no credential.** The MCP server reads this same
0600 file. A token used to be copied into the `env` block of a `.mcp.json` in
the repository, which is how live credentials get committed by accident.

## One server, several addresses

A server is often reachable more than one way — `localhost` on the machine it
runs on, a tailnet name from your laptop, a public hostname from anywhere.
`cylist setup` asks the server which (`GET /setup`) and stores **all** of them:

```toml
url = "http://localhost:8000"
urls = ["http://localhost:8000", "https://dnu-home-1.tail222f46.ts.net"]
token = "cyl_…"
```

Every request tries them in order and moves on only when it cannot *connect*;
the one that answers is used for the rest of the command and remembered for
half an hour. So a laptop set up at home keeps working from a café, and goes
back to the fast local address when it comes home — no reconfiguration, and
nothing to think about. `cylist whoami` names the address that actually
answered.

Only a failure to connect moves down the list. A timeout *after* the connection
was made might mean a write that was applied and whose response was lost, and
re-sending that could create a second card.

## Getting a token by hand

`cylist login` is the older path, for a token you have been given rather than
one minted for you. To mint one yourself, from a credential that already holds
`admin`:

```
curl -sX POST http://localhost:8000/api/v1/tokens \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"my laptop","scopes":["read","write"]}'
```

`cylist login` reads it from a prompt that does not echo, checks it against
`GET /me`, and only then writes it to disk:

```
$ cylist login --url http://localhost:8000
Paste an API token for http://localhost:8000 (input is hidden).
Token:
Signed in to http://localhost:8000 as my laptop.
Scopes: read, write
Token written to /home/you/.config/cylist/config.toml (mode 0600 — owner read/write only).
```

`cylist login --token-stdin` reads it from stdin instead, for provisioning
scripts. It asks the server for its other addresses too, so a login is not a
quieter way of throwing that list away.

### Where configuration comes from

First match wins:

| Source | Sets |
|---|---|
| `--url` | the server URL, and then it is the only one tried |
| `CYLIST_URL`, `CYLIST_TOKEN` | both; `CYLIST_URL` is likewise the only one tried |
| `~/.config/cylist/config.toml` (or `$XDG_CONFIG_HOME/cylist/`) | both, including the address list |
| built-in default | `http://localhost:8000` |

Naming a server means *that* server: quietly reaching a different one because
the named one was down would be the opposite of what was asked.

The config file is created with mode `0600` — owner read/write only — and
re-tightened on every write, so a file that already existed with looser
permissions does not stay that way.

**There is no `--token` flag, deliberately.** A token in an argument is written
to your shell history, is visible in `ps` for the life of the process, and is
captured by any shell trace. The environment variable and the 0600 file are
both strictly better, and leaving the flag out means nobody reaches for the
worse option because it was closer to hand.

## Commands

```
cylist projects [ls] [--all]           cylist project show ATL
cylist project new --key ATL --name "Atlas migration"
cylist project members ATL [--add NAME] [--remove NAME]

cylist board ATL                       columns, side by side

cylist tasks ls ATL [--status blocked] [--assignee "Aditi K"] [--column "In progress"]
cylist task show ATL-41
cylist task history ATL-41 [--page 1] [--per-page 10]
cylist task new ATL --title … --description … --type feature --due 2026-03-31 --assignee "Aditi K"
cylist task move ATL-41 --column "In progress" [--position 0]
cylist task status ATL-41 blocked --reason "…" --waiting-on "Lena W"
cylist task comment ATL-41 "…"

cylist goals ls ATL [--open]           cylist goals show ATL-G1
cylist goals new ATL --name "Search revamp" --owner PERSON_ID [--target 2026-12-01]
cylist goals link ATL-41 ATL-G1        cylist goals unlink ATL-41
cylist goals status ATL-G1 achieved

cylist people ls [--kind team|client] [--project ATL]
cylist people new --name … --kind team --role … --responsibilities …

cylist files ls ATL [Contracts/2026]
cylist files get ATL Contracts/2026/msa.pdf [-o FILE]

cylist vault ls ATL [Logins]
cylist vault reveal ATL Logins/Billing/Stripe [--show | -o FILE]
cylist vault add ATL Logins/Billing/Twilio [--username …] [--value-stdin]

cylist activity [--project ATL] [--entity task] [--limit 20]
cylist whoami

cylist setup                           token, hooks and MCP, in one command
cylist work ATL-41 [-- --model opus]   open Claude Code on a card
cylist hook install                    wire the board up to Claude Code's hooks
cylist hook uninstall
```

Every command takes `--json`, which prints the API's own response unshaped —
what `curl` would have returned. Nothing is renamed or flattened on the way
through, so a script built on `cylist --json` and one built on `curl` see the
same documents.

## Showing an agent's work on the board

`cylist setup` does this along with everything else; `cylist hook install` is
the same step on its own. Either adds one command — `cylist hook` — to Claude
Code's user-level `settings.json`, on six lifecycle events, and writes a
`/work` slash command beside it. It never touches hooks you already have, and running
it again only points it at wherever the binary is now.

After that, a Claude Code session bound to a card shows up on the board while
it runs: the card's border pulses while the agent is working, turns amber the
moment it is waiting on you — a permission prompt, or just the end of its turn
— and green when the session ends. Nothing the model does or says is involved;
the harness's own hooks report it.

Two ways to bind a session, and nothing else binds one:

```
cylist work ATL-41       start a session on a card
/work ATL-41             bind the session you are already in
/work off                unbind it
```

A prompt that merely mentions `ATL-41` never binds — "don't touch ATL-41"
would otherwise put you on it. An unbound session makes no requests at all, so
the sessions you run on other projects never appear on any board.

`cylist hook` is not a command to run yourself. It reads one JSON event from
stdin, always exits 0, and prints nothing but the JSON Claude Code expects —
a board that is down, a token that is missing, a malformed event: none of them
may cost you a prompt. Set `CYLIST_HOOK_DEBUG=1` to see why it did nothing.

### The connection it holds

A bound session gets one small background process, which holds a single
WebSocket to the board for as long as the session is on a card. The hooks
talk to it over a unix socket in `$XDG_RUNTIME_DIR/cylist` rather than making
a request each — or, on Windows, over a loopback port; see below.

That is not about saving handshakes. It is so the board can tell a session
that *finished* from one that was killed: a connection closing says so at
once, where a series of requests that stops arriving says nothing at all, and
the board used to have to guess from how long it had been quiet.

```
cylist hook status       what is running, on which card, and whether it is connected
cylist hook stop         end them (the next prompt starts a fresh one)
cylist hook stop --session <id>
```

One process per bound session, and never for an unbound one. It exits when
the session ends, when you have left it waiting for five minutes, or when the
board has been unreachable for half an hour. Its log is one file per run
under `$XDG_STATE_HOME/cylist/logs` (`%LOCALAPPDATA%\cylist\logs` on
Windows), truncated each time so it cannot grow. `cylist hook uninstall`
stops whatever is still running.

If you would rather not have a background process at all:

```
CYLIST_PRESENCE=off      report over HTTP, exactly as before
CYLIST_PRESENCE=http     the same, said explicitly
CYLIST_PRESENCE=ws       the default
```

`off` and `http` are not degraded modes — that HTTP path is also what carries
the first event of every session, before a daemon exists to carry it, and
what a machine that cannot start a daemon falls back to on its own.

### On Windows

Everything above works the same, and nothing here is a flag you have to set.
Three things underneath it are different, and they are worth knowing if you
are ever looking at why a session is not on the board.

CPython has no `socket.AF_UNIX` on Windows, so the hook reaches the daemon
over a port on `127.0.0.1` instead. A port cannot be given a mode, and any
process on the machine may connect to the loopback, so the daemon writes a
fresh 256-bit token beside the port in `%LOCALAPPDATA%\cylist\run` and
refuses a caller that cannot quote it back before it reads a word of the
message. What keeps that token private is the ACL Windows puts on your
profile directory — the same thing already keeping your API token private in
`config.toml`.

`cylist hook stop` asks the daemon to end over that channel rather than
signalling it. `os.kill` on Windows is `TerminateProcess`: it runs no handler
and no `finally`, so a daemon killed that way would never say goodbye and the
card would sit there looking live until the server's own idle window closed
it. Termination is still the fallback when the channel is the thing that has
gone wrong, and what the daemon could not clean up is cleaned up for it.

The idle window is counted from a clock that stops while the machine is
asleep, because Windows has no `CLOCK_BOOTTIME`. A laptop shut for the night
comes back and starts the five minutes again rather than ending the session
on the spot — which is the right answer anyway: you were waiting on it
before the lid closed and you still are.

You can run that loopback transport anywhere with `CYLIST_IPC=tcp`, which is
how the test suite exercises it on Linux. `CYLIST_IPC=unix` forces the other
way. Neither is something a normal install needs to set.

### On Windows

The hooks work; the background process does not exist. Python on Windows has
no `AF_UNIX`, and the obvious substitute — a loopback TCP port — is reachable
by every other process on the machine, where the unix socket was `0600`. So
Windows gets `CYLIST_PRESENCE=http` without being asked, and
`cylist hook status` says so rather than reporting nothing running.

What that costs is one thing, and it is worth knowing: a session that is
*killed* is noticed by the server's quiet window (ten minutes) instead of the
moment its socket closes. Working, waiting and done are all reported exactly
as they are elsewhere, and a session that ends properly says goodbye.

Two other Windows details, both handled:

* The command written into `settings.json` is quoted, because the path to the
  binary is routinely `C:\Users\…\cylist.exe` and one `Program Files` in it
  would otherwise be read as two arguments. An install recognises its own
  earlier command whichever shape it took, so running setup twice does not
  leave two hooks firing on every event.
* `cylist setup` registers the MCP server through `claude mcp add`, and runs
  the `claude.cmd` shim npm leaves on the path through the command processor —
  `CreateProcess` cannot start a batch file, and says only "not a valid Win32
  application" when asked to.

Configuration and state live in `~/.config/cylist` and `~/.local/state/cylist`
on Windows too, rather than under `%APPDATA%`. Unidiomatic, and deliberate:
the MCP server is a separate package that reads the same token file, and two
platform-dependent path rules that have to agree is a divergence waiting to
happen. `~/.claude` is already in the same place.

## Names, not ids

Anywhere the API wants a UUID, the CLI takes the name a human would say:

```
cylist task move ATL-41 --column "In progress"
cylist task status ATL-41 blocked --reason "…" --waiting-on "Lena W"
cylist task new ATL --assignee "Aditi K" …
```

Matching is case-insensitive and widens from exact to prefix to substring,
stopping at the first step that finds anything. **An ambiguous name is an error,
never a guess:**

```
$ cylist task status ATL-41 blocked --reason "…" --waiting-on "Le"
error: 'Le' matches more than one person in ATL's members: Lena W, Leo Wren. Use the full name or the id.
```

and an unknown one tells you what does exist:

```
$ cylist task move ATL-41 --column Shipped
error: No column called 'Shipped' on ATL's board. Known columns: Backlog, In progress, Done.
```

A value that parses as a UUID is passed straight through, so a script holding
ids pays for no lookups.

## Secrets

`cylist vault reveal` will not print a credential unless you say where it
should go:

```
$ cylist vault reveal ATL Logins/Billing/Stripe
Logins/Billing/Stripe
Username  billing@example.com
     URL  https://dashboard.stripe.com
   Notes  Live key.
 Updated  2026-01-11T09:00:00Z

The value was not fetched. Add --show to print it, or -o FILE to save it.
```

Without a flag the reveal endpoint is not even called, so nothing is written to
the audit log for a command that was never going to hand the value over.

- `--show` prints it to stdout and warns on stderr that it is now in your
  scrollback.
- `-o FILE` writes it to a file created `0600` from the instant it exists,
  and prints nothing.
- `--json` alone returns the metadata; `--json --show` includes the value.

In the other direction, `cylist vault add` reads the credential from a hidden
prompt or `--value-stdin`. A secret is never an argument in either direction,
so it never reaches your shell history.

Every `0600` on this page is a POSIX mode, and Windows has no such thing —
`os.chmod` there moves the read-only bit and nothing else, so these files
read back `0666` however they were created. What keeps them private is the
ACL Windows puts on your profile directory, which is where all of them live:
`config.toml`, the session state, and the daemon's endpoint token. It is the
same assumption pip and uv make about their own credentials. `cylist login`
says which of the two it actually got rather than quoting a mode it did not
set — a token file described as "owner read/write only" when it is `0666`
would be worse than saying nothing at all.

## Exit codes and errors

`0` on success, `1` on any failure, `2` for a usage error from the argument
parser. A failure prints one line to stderr — the server's own
`error.message`, not a traceback:

```
$ cylist project show NOPE
error: No project matches 'NOPE'.
$ echo $?
1
```

With `--json`, the failure is the API's error envelope on stderr instead, so a
script can branch on `error.code`:

```json
{
  "error": {
    "code": "not_found",
    "message": "No project matches 'NOPE'.",
    "details": {},
    "status": 404
  }
}
```

## Why argparse

`argparse` rather than `typer`, for three reasons: the CLI's runtime
dependencies are two — `httpx`, and `websockets` for the connection the
presence daemon holds — which makes "the API is enough" easier to believe;
every failure funnels through one `try` in `main.py` that owns the exit code
and guarantees no traceback reaches the terminal, rather than that being a
property of a framework's error handling being configured correctly; and
argparse is fully typed in typeshed, so `mypy --strict` passes without
placating decorator-inferred signatures. The cost is a verbose parser, which
for twenty subcommands is a fair trade. The reasoning is written out at the
top of `cylist_cli/main.py`.

## Development

```
uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest -q
```

The tests drive `main()` exactly as a terminal would, against an
`httpx.MockTransport` standing in for the server (`tests/fake_api.py`), and
assert on both what was printed and the HTTP that went out.
