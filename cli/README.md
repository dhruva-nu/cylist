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

## Getting a token

The CLI authenticates with a bearer token, the same kind an agent uses. Mint
one from a session that already has the `admin` scope:

```
curl -sX POST http://localhost:8000/api/v1/tokens \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"my laptop","scopes":["read","write"]}'
```

Then hand it to `cylist login`, which reads it from a prompt that does not
echo, checks it against `GET /me`, and only then writes it to disk:

```
$ cylist login --url http://localhost:8000
Paste an API token for http://localhost:8000 (input is hidden).
Token:
Signed in to http://localhost:8000 as my laptop.
Scopes: read, write
Token written to /home/you/.config/cylist/config.toml (mode 0600 — owner read/write only).
```

`cylist login --token-stdin` reads it from stdin instead, for provisioning
scripts.

### Where configuration comes from

First match wins:

| Source | Sets |
|---|---|
| `--url` | the server URL |
| `CYLIST_URL`, `CYLIST_TOKEN` | both |
| `~/.config/cylist/config.toml` (or `$XDG_CONFIG_HOME/cylist/`) | both |
| built-in default | `http://localhost:8000` |

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

cylist work ATL-41 [-- --model opus]   open Claude Code on a card
cylist hook install                    wire the board up to Claude Code's hooks
cylist hook uninstall
```

Every command takes `--json`, which prints the API's own response unshaped —
what `curl` would have returned. Nothing is renamed or flattened on the way
through, so a script built on `cylist --json` and one built on `curl` see the
same documents.

## Showing an agent's work on the board

`cylist hook install` adds one command — `cylist hook` — to Claude Code's
user-level `settings.json`, on six lifecycle events, and writes a `/work`
slash command beside it. It never touches hooks you already have, and running
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
