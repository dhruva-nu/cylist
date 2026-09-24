# Cylist

A project workspace that keeps a project's **board**, **files**, **vault** and
**people** in one place — and exposes all of it over one HTTP API, so your
agents can use it as readily as you can.

| Area | What it holds |
| --- | --- |
| **Kanban board** | Tasks as cards across 2–8 named columns, each of which folds down to a rail when you would rather not look at it. A card that goes *on hold* or *blocked* must say why, and can name the person it is waiting on. The last column is where a card is done, and can be divided into up to three outcomes — Done, Cancelled, In prod — so the board says how work ended as well as that it did. While an agent is on a card, the card's own border says so: working, waiting on you, or finished. |
| **Goals** | The epics a board's cards are written under. Each has a colour of its own, which every card on it wears down its left-hand edge, and a page listing what is left on it. Cards are grouped into lanes by goal on the board when you want to read it that way. |
| **Files** | Folders of uploaded files, with SharePoint and Google Drive links sitting alongside them. |
| **Vault** | Logins, keys and links in trees you shape yourself. Secrets are encrypted at rest and revealed only on request. |
| **People** | Team members and clients, what each is responsible for, and who to tag when work stalls. A team member can be given an account by invitation, and then signs in as themselves: whoever starts a project joins it, cards are assigned to a person, and the audit trail names which of you did what. Clients are named on the work, never signed in to it. |

React · FastAPI · PostgreSQL. See [PLAN.md](PLAN.md) for the full design and
build order, and the [approved mock](https://claude.ai/code/artifact/27e0344e-ef48-4e2a-a592-5e5c53c1f935)
for the intended UX.

---

## Getting started

**You need:** [uv](https://docs.astral.sh/uv/), Node 22+, and Docker to run
the development database.

```bash
make setup          # install backend and frontend dependencies
make db             # start PostgreSQL on :5432, and a test database on :5433

cp .env.example backend/.env
make hash-password  # paste the output into backend/.env
make vault-key      # paste the output into backend/.env

make migrate        # create the schema
make dev            # API on :8000, web app on :5173
```

Open <http://localhost:5173> and sign in with the password you just hashed.
Nobody has an account yet, so that password — the deployment's, belonging to
nobody — is what gets you in. Open your own account from the card at the top
of the home screen, and the deployment password stops working. From then on
everybody signs in with their own email and password, and invites the rest of
the team from the People tab.

The API's interactive docs are at <http://localhost:8000/api/v1/docs>.

## Everyday commands

```bash
make check          # lint, type-check, test, verify migrations — what CI runs
make test           # both test suites: pytest, then vitest
make revision m="add projects"   # autogenerate a migration from the models
make help           # every target
```

The backend half of `make test` needs no setup at all: with no
`CYLIST_TEST_DATABASE_URL` in the environment it starts an **embedded
PostgreSQL**, uses it, and throws it away. Point that variable at a running
database to use that one instead — which is what CI does. Either way the tests
run against real PostgreSQL, because the schema leans on `ARRAY`, `JSONB` and
timezone-aware timestamps.

The frontend half is Vitest over the parts of the UI that are plain functions
rather than components — the tag matcher in `components/mentions.ts`, which
reads `@somebody` and `>some file` out of prose — which
is where a wrong answer is possible without anything looking broken.

## How it is put together

```
frontend/   React + Vite. Types are generated from the API's OpenAPI document,
            so the UI cannot drift from the server.
backend/    FastAPI. Routers handle HTTP; business rules live in app/services
            so a future in-process MCP server can call them directly.
```

**The API is the product.** The web app is one client of `/api/v1`; the CLI and
MCP server planned in Phase 6 are two more. Nothing is implemented in React
that an agent cannot also do over HTTP.

### Authentication

Two ways in, one code path:

- `Authorization: Bearer cyl_…` — scripts, the CLI, MCP servers.
- an HttpOnly session cookie — the browser, issued by `POST /auth/login`.

Both are rows in `api_token`, so both can be revoked and both appear in the
audit trail. Tokens carry **scopes**, and you should grant the narrowest set
that works:

| Scope | Allows |
| --- | --- |
| `read` | Read projects, boards, file listings and vault *structure*. |
| `write` | Create and change projects, people, tasks, files, vault nodes. |
| `vault:read` | Read a vault entry's username, URL and notes. |
| `vault:reveal` | Decrypt a stored secret. Every use is logged. |
| `admin` | Manage API tokens. |

An agent that tidies your board wants `read` and `write` — and nothing else.
It cannot mint itself a broader token, because that needs `admin`.

### Roles

Scopes say what a *credential* may do anywhere. A **role** says who somebody is
on one board — "Reviewer", "QA", "Designer" — and it is the project's admin who
invents them. Every project is created with one role, `Admin`, worn by whoever
created it; everybody added afterwards has no role until an admin says what
they are.

Only a holder of `Admin` can create a role, rename one, hand one out, or say
what any of them may do. Because a credential acts as whoever minted it, an
agent token owned by an admin can do that too — narrowing *that* is what a
smaller scope on the token is for.

Roles are per project, so the same person can be the Admin of one board and a
Reviewer on another. `GET /projects/{ref}/roles` lists them, and each entry in
`GET /projects/{ref}/members` carries the one its member wears — alongside
`title`, which is the job description the directory holds and is not a role at
all.

### Permissions

A role carries a set of permissions saying what its holders may do *here*, from
a vocabulary the server fixes:

| Permission | Allows |
|---|---|
| `tasks` | Create, edit, move and finish cards, sub-tasks and checklists. |
| `comments` | Say something on a card. |
| `goals` | Create, rename, retarget, drop and finish goals. |
| `goal_assign` | Say which goal a card counts towards. |
| `goal_owner` | Hand an existing goal to somebody else. |
| `board` | Columns, and card templates — the shape of the board. |
| `files` | Folders, uploads and links. |
| `vault` | Add, change and delete credentials. Not read them. |
| `vault_reveal` | Decrypt and read a stored secret. |
| `people` | Say who is on this project. |
| `agents` | The skills and notes this project's agents work from. |
| `project` | Rename, describe and archive the project. |

A role's *name* is the admin's invention; what it may do cannot be, because
every entry is a fence a particular endpoint recognises. Reading is not on the
list: a project is a shared workspace, and everyone who can reach a board can
read it. `vault_reveal` is the one read-shaped entry, and it was already a
distinct, logged, separately scoped act before roles existed.

Three things hold everything, always. The `Admin` role, by being it. The
bootstrap session, which belongs to nobody. And whoever the project's grid
grants it to.

The line called **Everyone else** is what somebody on the project with no role
may do — and what somebody who is not on it may do, since membership has never
been a fence here. A project is created with every box on that line ticked, so
a new board behaves exactly as boards did before permissions existed, and its
admin narrows it from there. A new role starts with whatever that line allows,
so naming somebody a Reviewer is never a demotion nobody asked for.

Scopes and permissions are both checked and neither stands in for the other: a
read-only token held by an admin still cannot write, and a `write` token held
by somebody whose role does not allow cards still cannot move one.

### Where on the board, and how sensitive

Two of those permissions are too blunt on their own, so each is narrowed by
rules about the *thing* rather than the area. Both are **restrictions**: the
flat permission is still the gate, and a missing rule narrows nothing. That is
also why a column added next month is open to everybody who may move cards,
rather than closed until somebody notices.

**Where on the board.** Per role, per column, two answers: may a card be moved
*into* it, and may the sub-stages a card passes through there be set — both the
template's rule for the column and a card's own progress bar, because they are
the same decision. A role with `tasks` switched off is not asked about columns
at all.

**How sensitive.** Every uploaded file, link and vault node carries a level —
`public`, `internal` or `restricted` — and every role carries the highest it
may read. Folders and vault trees carry a *default* that new children inherit,
so "everything in Contracts is restricted" is said once rather than on each
upload. Something above your clearance is answered **as though it were never
there**: left out of listings, a 404 by id, a 404 to download, and not counted
on the project hub. A 403 on `redundancy-list-final.xlsx` would already have
told you the interesting part. A restricted vault branch takes its whole
subtree with it.

Reading is otherwise still unfenced — a project is a shared workspace, and its
board, cards and goals are visible to everyone on it.

`GET /projects/{ref}/permissions` returns the whole grid — the vocabulary, the
levels, each role's line with its columns and clearance, and `mine`, what the
caller may do here, which is what the web app hides buttons by. Five PUTs
replace one line of it:

```
PUT /projects/{ref}/roles/{role}/permissions
PUT /projects/{ref}/roles/{role}/columns
PUT /projects/{ref}/roles/{role}/clearance
PUT /projects/{ref}/permissions/everyone-else            # …/columns, …/clearance too
```

The **Roles** tab on a project is the screen all of that is drawn from.

### Auditing

Every mutation writes a row to `activity` recording who did it, what changed,
and whether it came through the browser or the API. `GET /activity` answers
"what did the CLI change at 3am?".

That trail is read back two ways. A card's own history —
`GET /tasks/{ref}/history` — is the full record of what has been done to one
piece of work. `GET /projects/{ref}/reports/day` is the other cut: one day of
it, grouped by card, with a paste-ready Markdown note for a stand-up. The day
is midnight to midnight in whichever zone the caller names, so an evening's
work stays in the evening it happened.

The report is deliberately shorter than the record. A card dragged To do → In
progress → Dev in one day arrives as the one move it amounted to, and a
comment's line quotes what was said rather than reporting that something was
said.

### Logs

The `activity` trail above is what *people and agents did*. The log is the
other record: what the *process* did — requests served, credentials refused,
blobs written, exceptions nobody caught. They are kept apart deliberately, so
the log does not become a second audit trail that nobody can query.

Every line carries the id of the request that produced it, and that id goes
back to the caller as `X-Request-ID`. So a report of "it failed at about
half past two" becomes one `grep`:

```bash
make prod-logs | grep 7f3a2b1c9d04
```

which gives the access line, whatever the services said while serving it, and
the traceback underneath — provably all the same request, however many were in
flight. A caller that sets `X-Request-ID` itself keeps its own, which is how a
CLI or MCP run ties its logs to the server's.

Logs go to stderr, which is what `make logs` and `make prod-logs` follow.
To also keep them in a file on the server, set one variable:

```bash
CYLIST_LOG_TO_FILE=true
```

They land in `CYLIST_DATA_DIR/logs/cylist.log` — inside the volume every
deployment already mounts and backs up — and rotate at 10 MB, five files deep.
`CYLIST_LOG_LEVEL` and `CYLIST_LOG_FORMAT` (`text` or `json`) are the other two
knobs; see `backend/.env.example` for all of them, and [DEPLOY.md](DEPLOY.md)
for reading them on the server.

Credentials never appear in the log — no tokens, no cookies, not even their
hashes — and neither do request bodies or query strings. That is a property
worth keeping: it is what makes a log safe to paste into a bug report.

## Secrets and backups

`CYLIST_VAULT_KEY` encrypts every vault secret with AES-256-GCM. **If you lose
it, those secrets are unrecoverable** — keep a copy somewhere you trust.

A complete backup is two things, and you need both to restore:

```bash
pg_dump "$CYLIST_DATABASE_URL" > cylist.sql   # the data
rsync -a "$CYLIST_DATA_DIR"/ backup/data/     # the uploaded files
```

## Deploying

`main` deploys itself. Every push that passes CI is built and released to
**dnu-home-1** by a self-hosted runner on that machine, and served over HTTPS at
<https://dnu-home-1.tail222f46.ts.net> — one container holding the API and the
built SPA, in front of a Postgres that publishes no port at all.

`make deploy` runs the same script by hand on the server. See
[DEPLOY.md](DEPLOY.md) for the shape of it, the one-time setup, and how to roll
a bad release back.

**Staging** is the same machine, the same image and the same deploy script, on
:8001 and on the tailnet only. It holds the `staging` branch, deployed when you
ask for it rather than on every push, and `make staging-refresh` reloads it from
production's database and files — so what it rehearses is a real release against
real rows.

```bash
make staging-deploy    # or run the "Deploy staging" workflow from `staging`
make staging-refresh   # reload it from production
```

Because it is restored from production it holds real vault ciphertext, and its
secrets deserve production's care. DEPLOY.md says what that means.

## The CLI and the MCP server

Everything the web app does is an HTTP call, so two other clients ship with it.

**To give Claude Code on any machine the board's tools, one line and nothing
installed.** The MCP server is served by Cylist itself, at `/mcp`. Open any
project's **Agents** page, press **Get the line**, and run what it shows on the
machine:

```bash
claude mcp add --transport http --scope user cylist https://dnu-home-1.tail222f46.ts.net/mcp --header "Authorization: Bearer cyl_…"
```

It is the same line in PowerShell. It needs no CLI, no uv and no Python, and
because production is funnelled it works off the tailnet too. The token is
`read,write`, acts as whoever pressed the button, and is shown once.
[`mcp/README.md`](mcp/README.md) has the details.

**To also put that machine's sessions on the board**, which is what the CLI's
hooks and `/work` do, install the CLI. No clone, no `make`, and nothing
installed outside your home directory:

```bash
# Linux and macOS
curl -fsSL https://raw.githubusercontent.com/dhruva-nu/cylist/main/scripts/install.sh \
  | sh -s -- --url https://dnu-home-1.tail222f46.ts.net
```

```powershell
# Windows PowerShell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/dhruva-nu/cylist/main/scripts/install.ps1))) `
  -Url https://dnu-home-1.tail222f46.ts.net
```

Both install [uv](https://docs.astral.sh/uv/) if it is missing, install the
`cylist` CLI and the `cylist-mcp` server, and then run `cylist setup`. Neither
needs `sudo` or an administrator. From a clone, `make agent` is the same thing
against your working tree.

`cylist setup` finds the server, asks for your email and password once, mints a
`read,write` token for this machine — one that acts as *you*, so the board says
whose agent moved a card — stores it `0600`, installs Claude Code's
lifecycle hooks and `/work`, and registers the MCP server — installing it
first if this machine has no copy. It is safe to run again, and it stores
**every** address the server says it answers on, so the same setup works on
the tailnet, off it, and on the server itself without being told which.
`cylist whoami` names the one that answered.

Linux, macOS and Windows, with the same board behaviour on each. What differs
is only how a hook reaches the daemon holding its session open: a unix socket
where there is one, and on Windows a loopback port guarded by a per-daemon
secret, because a port — unlike a socket — cannot be given a mode.

```bash
cd cli && uv sync && uv run cylist --help
```

```
cylist projects                       cylist board ATL
cylist task new ATL --title "…" --type bug --due 2026-09-05 --assignee "Aditi K"
cylist task status ATL-41 blocked --reason "…" --waiting-on "Lena W"
cylist files ls ATL                   cylist vault reveal ATL Logins/Stripe --show
```

Names work where a human would use one — `--assignee "Aditi K"`, `--column "In progress"`
— and an ambiguous name is an error rather than a guess. Every command takes `--json`.
Leave `--assignee` off and the card is yours: a card goes to whoever wrote it
unless it names somebody else, and `Agent` is the name to give it when the work
is for a machine.

Each project carries what its agents work from, under its **Agents** tab: skills
you upload for them to follow, and a scratchpad they write one-line findings back
onto. `note_learned` is how an agent leaves something it worked out the hard way,
capped at 280 characters so the next one reads the pad rather than skimming it.
The MCP instructions and the `/work` command tell an agent to read the pad before
it starts on a card and to write to it as it learns, not at the end; a line that
says what one already there says is refused, so the pad stays a list of facts
rather than a log.

The MCP server in `mcp/` exposes the same surface to Claude Code and other agents.
It registers `reveal_secret` **only** when its token carries `vault:reveal`, so an
agent is never offered a tool that will always fail. It reads the same 0600 file
the CLI does, so the registration Claude Code holds contains no credential at
all — see `mcp/README.md`.

To mint a token by hand, scoped to what the agent actually needs:

```bash
curl -X POST localhost:8000/api/v1/tokens -b cookies \
  -d '{"name":"board agent","scopes":["read","write"]}'
```

## Showing an agent's work on the board

A Claude Code session bound to a card appears on the board while it runs. The
card's border pulses teal while the agent is working, turns amber the moment it
is waiting on you — a permission prompt, or simply the end of its turn — and
green when the session ends. Nothing the agent does or says is involved:
Claude Code's own lifecycle hooks report it, so there is nothing for a model to
remember to call and nothing it can misreport.

Set it up once per machine, pointed at the board you actually use:

```bash
uv tool install ./cli        # puts `cylist` on your PATH — see below for why that matters
cylist login --url https://dnu-home-1.tail222f46.ts.net
cylist hook install          # six hooks in ~/.claude/settings.json, plus a /work command
```

`cylist login` asks for an API token, hidden rather than typed as an argument.
Mint one against production the way any agent's token is minted — sign in, then
ask for it. `read` and `write` are the whole of what the hook needs:

```bash
curl -sc /tmp/cyl -X POST https://dnu-home-1.tail222f46.ts.net/api/v1/auth/login \
  -H 'content-type: application/json' -d '{"password":"…"}'
curl -sb /tmp/cyl -X POST https://dnu-home-1.tail222f46.ts.net/api/v1/tokens \
  -H 'content-type: application/json' \
  -d '{"name":"claude on this machine","scopes":["read","write"]}'
```

Hooks load when a session starts, so open a **new** Claude Code session
afterwards. Then bind it to a card:

```
/work ATL-41             bind the session you are in; it renames itself to the card
/work off                unbind it
cylist work ATL-41       start a session already bound to a card
```

Nothing else binds a session. A prompt that merely mentions `ATL-41` never does
— otherwise "don't touch ATL-41" would put you on it — and an **unbound session
makes no requests at all**, so the sessions you run on unrelated projects never
appear on any board.

Three things are worth knowing before the first time it looks broken.

- **The hook reports to whichever server `cylist login` pointed at**, since it
  reads the same `~/.config/cylist/config.toml` you do. Pointing it at
  production is what puts a session on the real board; `cylist login --url
  http://localhost:8000` instead when you are working on Cylist itself, which
  needs its own token from your own database.
- **A machine with no token reports nothing and says nothing about it.** That is
  the usual reason for a card that stays blank. Set `CYLIST_HOOK_DEBUG=1` in a
  session's environment and the hook explains itself on stderr instead.
- **`hook install` records the absolute path of the `cylist` binary**, because a
  hook runs with whatever PATH it inherits. That is why it is worth installing
  as a tool rather than leaving it in a checkout's virtualenv, which a
  `uv sync` can replace underneath it. Run `cylist hook install` again after
  moving or reinstalling it and the recorded path is corrected in place.

Failing silently is deliberate: `cylist hook` sits in front of every prompt you
type, so a board that is unreachable, a token that has been revoked or an event
it cannot parse all cost you nothing and interrupt nothing. `cylist hook
uninstall` takes it all back out and leaves any other hooks you have alone.

`cylist board ATL` says the same thing in the terminal. `cli/README.md` has the
rest, including what each lifecycle event reports.

## Status

All six phases of the [plan](PLAN.md) are built: authentication and the audit
trail, projects and people, the Kanban board, files, the vault, polish, and the
agent tooling.

```bash
make seed    # fills an empty database with three worked-through projects
```
