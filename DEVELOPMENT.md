# Developing Cylist

Running Cylist locally, how it is put together, and how it is deployed. For what Cylist is and how to use the deployed board, see the [README](README.md).

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
android/    The phone app: Kotlin and Jetpack Compose, built with Gradle.
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
list: a project is a shared workspace for the people on it, and everyone who is
on a board can read it. `vault_reveal` is the one read-shaped entry, and it was already a
distinct, logged, separately scoped act before roles existed.

Three things hold everything, always. The `Admin` role, by being it. The
bootstrap session, which belongs to nobody. And whoever the project's grid
grants it to.

The line called **Everyone else** is what somebody on the project with no role
may do. Somebody who is not on it never gets as far as the grid — see
*Membership* below. A project is created with every box on that line ticked, so
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
in the project's summary. A 403 on `redundancy-list-final.xlsx` would already have
told you the interesting part. A restricted vault branch takes its whole
subtree with it.

Reading is otherwise still unfenced — a project is a shared workspace, and its
board, cards and goals are visible to everyone on it.

### Membership

Being on a project is what lets you see it at all (CYLIST-111). `GET
/projects` lists only the projects the caller is a member of, and every path
that reaches one — its key, a card's reference, the id of a doc, folder, file,
vault tree or node, a goal, a column, a comment, the board socket — answers a
non-member with the same **404** a project that does not exist gets, so the
refusal cannot be used to learn which keys are taken. The activity feed keeps
only entries on the caller's projects and those on no project at all.

The check is `app.services.projects.require_reachable`, called from
`resolved_project` and from every row resolver (`resolved_task`,
`resolved_doc`, …). A new route addressed by a row on a project has to resolve
it through one of those, or call the check itself.

An API token acts as whoever minted it, so an agent sees exactly what its
person sees, whatever its scopes. The bootstrap session is nobody, so it is on
no project and sees all of them — it is what adds the first person to one.
Taking yourself off a project is leaving it: it disappears from your list.

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
<https://cylist.in> — one container holding the API and the
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
claude mcp add --transport http --scope user cylist https://cylist.in/mcp --header "Authorization: Bearer cyl_…"
```

It is the same line in PowerShell. It needs no CLI, no uv and no Python, and
because production is funnelled it works off the tailnet too. The token carries the scopes
`GET /setup` names for an agent — `read`, `write`, `vault:read`,
`vault:reveal` — acts as whoever pressed the button, and is shown once.
[`mcp/README.md`](mcp/README.md) has the details.

**To also put that machine's sessions on the board**, which is what the CLI's
hooks and `/work` do, install the CLI. No clone, no `make`, and nothing
installed outside your home directory:

```bash
# Linux and macOS
curl -fsSL https://raw.githubusercontent.com/dhruva-nu/cylist/main/scripts/install.sh \
  | sh -s -- --url https://cylist.in
```

```powershell
# Windows PowerShell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/dhruva-nu/cylist/main/scripts/install.ps1))) `
  -Url https://cylist.in
```

Both install [uv](https://docs.astral.sh/uv/) if it is missing, install the
`cylist` CLI and the `cylist-mcp` server, and then run `cylist setup`. Neither
needs `sudo` or an administrator. From a clone, `make agent` is the same thing
against your working tree.

`cylist setup` finds the server, asks for your email and password once, mints an
agent token (the board and the vault) for this machine — one that acts as *you*, so the board says
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

Each project carries what its agents work from. Under its **Agents** tab, skills
you upload for them to follow. Under its **Docs** tab, the project's markdown —
section → topic → doc — which agents read and write over MCP. Before an agent
reads the code to answer a question, it asks the docs with `ask_docs`:
[jev-docs](vendor/jev-docs), on [jev](https://api.typesafe.ai) (TypeSafe's System
One model), routes the question to the one section that answers it and reads
that section back against the question. Past the relevance threshold the answer
is `ok` and the agent works from it; otherwise the agent finds the answer in the
code, asks `place_doc` where it belongs — which doc and section, or a doc of its
own — and writes it there with `write_doc`, so the next agent to ask gets it
from the docs. The MCP instructions and the `/work` command both say so. Agents
never create topics. Without `JEV_API_KEY` on the server every question comes
back unanswered, and a doc written without a topic is refused with the topics to
choose from.

### Giving an agent the project's skills

`read_skill` lets an agent read one skill. To have them *installed* — where
Claude Code loads them as skills of its own, `/board-tidy` and all — pull them
into the repository the agent works in:

```bash
cylist skills ls ATL                  # what the project has
cylist skills pull ATL                # every skill, into <repo>/.claude/skills/
cylist skills pull ATL board-tidy.md  # just one; inside `cylist work ATL-41` the key can be left out
cylist skills pull ATL --user         # into ~/.claude/skills/, for every repo on this machine
```

A markdown skill becomes `.claude/skills/<name>/SKILL.md`, with the `name` and
`description` frontmatter Claude Code decides relevance by filled in from the
upload where the file has none. A zip is unpacked as the folder it is — through
one wrapping directory, as zipping a folder produces — and must have a
`SKILL.md` at its root; scripts inside keep their executable bit. A session
that is already running picks a pulled skill up within a few seconds — but only
if its `.claude/skills` existed when the session started, because Claude Code
watches the directories it found and no others. The first pull into a
repository therefore loads from the next session, and says so; every pull after
it is picked up live. The server does the unpacking
(`GET /skills/{id}/folder`), refusing a zip entry that climbs out of the folder,
a symlink, or an archive that unpacks past 20 MB.

A pulled folder carries a `.cylist-skill.json` recording what was written, and a
`.gitignore` of `*` so the copy is never committed by accident — the board keeps
the original. Pulling again updates what it wrote before; a folder it did not
write, or one edited since, is left alone and reported, and `--force` replaces
it. An agent connected through MCP has `download_skill`, which returns the same
folder and the `cylist skills pull` line to install it — or the files to write
itself where the CLI is not installed.

The MCP server in `mcp/` exposes the same surface to Claude Code and other agents.
It registers `reveal_secret` **only** when its token carries `vault:reveal`, and
`add_secret` / `update_secret` only with `write` and `vault:read`, so an
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
green when the session ends. While it works, the card's agent line says what
it is doing right now — "Running pytest test_pr_states.py", "Editing
tasks.py" — from the `PreToolUse` hook, without adding a line to its history.
Open the card and its Agent feed shows what the agent read, edited, ran and
said, as its presence daemon finds it in the session's transcript. When the
agent cannot decide something alone it calls the MCP tool `ask_human`: the
question and its options go on the card, the session turns amber with reason
`question`, and the tool waits — long-polling
`GET /tasks/{ref}/agent-prompts/{id}?wait=20` — until somebody answers with
`POST /tasks/{ref}/agent-prompts/{id}/answer`, so the agent carries on in the
same turn. A permission prompt goes on the card too, through the
`PermissionRequest` hook and the session's presence daemon, and can be
allowed or denied from the board while the terminal's own dialog stays open —
the first answer wins. Nothing else the agent does or says is involved:
Claude Code's own lifecycle hooks report it, so there is nothing for a model to
remember to call and nothing it can misreport.

Set it up once per machine, pointed at the board you actually use:

```bash
uv tool install ./cli        # puts `cylist` on your PATH — see below for why that matters
cylist login --url https://cylist.in
cylist hook install          # eight hooks in ~/.claude/settings.json, plus a /work command
```

`cylist login` asks for an API token, hidden rather than typed as an argument.
Mint one against production the way any agent's token is minted — sign in, then
ask for it. `read` and `write` are the whole of what the hook needs:

```bash
curl -sc /tmp/cyl -X POST https://cylist.in/api/v1/auth/login \
  -H 'content-type: application/json' -d '{"password":"…"}'
curl -sb /tmp/cyl -X POST https://cylist.in/api/v1/tokens \
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

## The Android app

`android/` is a Gradle project of its own, in Kotlin and Jetpack Compose, with
two modules: `app`, the phone app, and `api`, the client for `/api/v1`. The
`api` module is plain Kotlin on the JVM, with no Android in it. It exists
because Android 16's Live Updates, an agent's progress shown in the status bar,
are something only a native app can post. Every version is set in
`android/gradle/libs.versions.toml`.

**You need:** JDK 21 and the Android SDK command-line tools. Gradle itself
comes from the committed wrapper, and the build downloads the SDK platform it
compiles against. Both install into your home directory:

```bash
# JDK 21: any build will do, e.g. Temurin from adoptium.net, unpacked to ~/.local/opt/jdk21
# SDK: "Command line tools only" from developer.android.com/studio, unzipped so
# that ~/Android/Sdk/cmdline-tools/latest/bin/sdkmanager exists
export JAVA_HOME=~/.local/opt/jdk21
export ANDROID_HOME=~/Android/Sdk
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$PATH"
yes | sdkmanager --licenses
sdkmanager platform-tools        # adb, to install onto a phone
```

```bash
make android           # lint, unit tests and the debug APK, as CI runs them
make android-install   # build the debug APK and install it on the phone adb sees
```

The APK lands in `android/app/build/outputs/apk/debug/app-debug.apk`. To try a
pull request on a phone without building anything, download the
`cylist-debug-apk` artifact from its CI run instead. CI signs every APK with a
fresh debug key, so uninstall one before installing another.

**Releases.** The repository's release tag is the app's version too. Publishing
a release (`gh release create vX.Y.Z …`) runs
`.github/workflows/android-release.yml`, which builds the release APK at that
tag, signs it with the release key, and attaches it to the release as
`cylist-vX.Y.Z.apk`. Download it on the phone from the release page and
install it; it installs over the previous release and keeps the sign-in.

- **Version.** `android/buildSrc/src/main/kotlin/ReleaseVersion.kt` turns the
  tag into `versionName` `X.Y.Z` and `versionCode` `X*1_000_000 + Y*1_000 + Z`
  (v0.3.2 is 3002). The build reads the tag from the Gradle property
  `cylist.version` or the environment variable `CYLIST_VERSION`. Without
  either, a build is `0.0.0`, code 1. A tag with a suffix (`v0.4.0-rc1`) or a
  minor or patch above 999 fails the build, because its code could not stay
  above the last release's. More shows the version at the bottom.
- **Signing.** `app/build.gradle.kts` signs the release build only when all
  four of `CYLIST_RELEASE_KEYSTORE` (a path), `CYLIST_RELEASE_KEYSTORE_PASSWORD`,
  `CYLIST_RELEASE_KEY_ALIAS` and `CYLIST_RELEASE_KEY_PASSWORD` are set. With
  none of them it builds `app-release-unsigned.apk`; with only some it fails.
- **Where the key lives.** It lives in the GitHub environment `android-release` (secrets
  `ANDROID_RELEASE_KEYSTORE_BASE64`, `ANDROID_RELEASE_KEYSTORE_PASSWORD`,
  `ANDROID_RELEASE_KEY_ALIAS`, `ANDROID_RELEASE_KEY_PASSWORD`, plus
  `ANDROID_GOOGLE_SERVICES_JSON` for pushes) and in one offline backup, nowhere
  else. The environment variable `ANDROID_RELEASE_CERT_SHA256` holds the
  certificate's SHA-256, and the workflow fails an APK signed with anything
  else. The key is PKCS12, alias `cylist-release`, certificate SHA-256
  `43f7259ec0eb550b56edc8462c6f54de323e2e1638f0f6e38a79fb7a01811517`.
  **Losing it means every phone has to uninstall to take the next release.**
- **A release from before this workflow existed**, or a failed run, is rebuilt
  with *Run workflow* on the Actions tab and the tag. That only works once the
  workflow is on `main`. The APK on the release is replaced.

To build a signed APK locally, export the four variables, then run
`./gradlew assembleRelease -Pcylist.version=vX.Y.Z`. Output goes to
`app/build/outputs/apk/release/app-release.apk`.

**Signing in.** The app opens on a server picker: Production
(`https://cylist.in`), Staging (`https://stage.cylist.in`), Dev 1–5
(`https://devN.cylist.in`), or another address typed in. The choice is checked
with `GET /setup` before the email and password are asked for, so a wrong or
unreachable address says so within about five seconds (`Accounts.defaultHttp`
sets the timeouts). Signing in is the web app's own login used once:
`POST /auth/login` buys a session cookie, which mints a personal token named
for the phone (`"<device name> (Android)"`, scopes `read`, `write` and
`permit`) with
`POST /tokens`, and is logged out straight away. The phone keeps the token,
never the password, sealed with an AES-GCM key held in the Android Keystore
(`app/.../session/SessionStore.kt`), so it survives a restart and a reboot.
Sign out (More tab) revokes the token with `DELETE /tokens/{id}` using the
token itself; any 401 from the server signs the app out with a notice.

The code is in `api/src/main/kotlin/cylist/api/Accounts.kt` (the HTTP side,
plain JVM) and `app/src/main/kotlin/cylist/app/session/Sessions.kt` (what the
app holds). Screens take a `Session` from `Sessions` and talk through
`session.client`; none of them builds a client or reads storage.

To sign in to a backend on this machine from the emulator, choose "Another
server" and type `http://10.0.2.2:<port>`. Debug builds may use plain HTTP to
`10.0.2.2` and `localhost` only (`app/src/debug/res/xml/network_security_config.xml`,
mirrored by `CLEARTEXT_HOSTS` in `app/build.gradle.kts`); everything else, and
every release build, is HTTPS. A fresh backend has no accounts; sign in to it
once in a browser with the bootstrap password and give somebody an account
(`PUT /people/{id}/account`) first.

**The board is live.** While the Board screen is in the foreground it holds the
project's board socket (`cylist.api.BoardSocket`, the same doorbell the web app
rings — see `frontend/src/api/realtime.ts`) and refetches on each
`board.changed`, so a card moved on the web moves on the phone within a second
or two. The socket closes when the app goes to the background and reopens when
it comes back, and its `ready` refetches whatever changed meanwhile. The card
rules — late dates, the agent line, whose cards — are ported from the web app
in `app/src/main/kotlin/cylist/app/board/BoardRules.kt`, each beside the name of
the TypeScript it mirrors; change the two together.

A sideways swipe on the card list moves between columns (the commit, flick,
rubber-band and direction-lock numbers are in `BoardRules.kt` beside
`swipeStep`). The assignee on a card is its own touch target and opens a sheet
of the project's members; picking one sends `PATCH /tasks/{ref}` with
`assignee_id` alone, through `CylistClient.patchTask`, because the generated
`TaskUpdate` writes every field and a null `due_date`, `goal_id`,
`template_id` or `jira_ref` would clear it.

**A card opens over the board.** Tapping one opens the Task detail screen
(`app/src/main/kotlin/cylist/app/task/`): its fields, sub-tasks and checklist
(a tap ticks one), linked Jira and pull-request rows, the agent sessions on it
with their feed (`GET /tasks/{ref}/agent-feed`, newest first) and comments with
a composer held above the keyboard. The column row moves the card. A question
or a permission an agent is waiting on is answered in place
(`POST /tasks/{ref}/agent-prompts/{id}/answer`); a permission needs a browser
session or a token with the `permit` scope, which the phone's has and an
agent's never does. The screen holds the project's board socket like the board,
so the feed and changes made on the web arrive by themselves. What it allows
before asking the server (a card with open sub-tasks or checklist items cannot
go to the last column; leaving the last column is confirmed first; outcomes
belong to the last column) is in `TaskRules.kt`, each rule beside the
TypeScript or Python it mirrors.

**Today is the web planner's day.** The Today tab plans on the project the
Board tab is on, through the same `/today` routes as the web planner (see
`frontend/src/components/Today.tsx`), and sends the phone's own zone as
`timezone`, as the web sends the browser's, so a day planned on one is the
same day on the other. It holds the board socket while it is on screen, so a
card ticked on the board ticks its to-do here; a to-do ticked on the web rings
no doorbell (the plan is not the board), so the phone reads it when Today is
next opened. Its rules are ported beside the TypeScript they mirror: which
cards are offered and the dates in `today/TodayRules.kt` (`components/today.ts`,
`components/dates.ts`), and `@` person / `>` file tags in `today/Tags.kt`
(`components/mentions.ts`). Change the two together; `TagsTest` and
`TodayRulesTest` are the web tests, case for case.

The theme is not Material's. It is the web app's tokens
(`frontend/src/theme/tokens.css`) written out in
`app/src/main/kotlin/cylist/app/theme/Tokens.kt`, and `ThemeTokensTest`
compares the two. Change a colour, type size or corner in `tokens.css` and that
test fails until `Tokens.kt` matches, so CI runs the Android job when
`tokens.css` changes too.

The API client is not written by hand either. `android/api/openapi.json` is a
committed copy of the backend's OpenAPI document, and the `api` module
generates a Kotlin client from it on every build (OpenAPI Generator, OkHttp and
kotlinx.serialization) into `api/build/generated/`. `CylistClient` hands each
generated API class the server and a `cyl_…` token, sent as
`Authorization: Bearer`:

```kotlin
val tasks = CylistClient("https://cylist.in", token).api(::TasksApi)
```

Change what a route takes or returns and the snapshot is stale. Refresh it,
without a server or a database, and commit it with the backend change:

```bash
make openapi         # rewrite android/api/openapi.json from backend/
make openapi-check   # what CI's backend job runs: fail if it is out of date
```

### Agent notifications: FCM and Live Updates

When an agent session on a card starts, changes state, or starts asking
something, the server pushes it to the phones of the person running the agent
and of the card's owner (CYLIST-107). The phone shows it as an Android 16
Live Update that changes in place: working (a chip counting up), needs you (a
question or permission: rings, with the question), waiting for your reply, then
finished. Tapping it opens that card's agent session.

**Server side** (`backend/app/services/push/`). A mapper listener on
`AgentSession` (`triggers.py`) compares each row before and after a flush and
queues a snapshot on the transaction's outbox when the change is worth a push
(`messages.worth_pushing`: new, a change of state, or a waiting session's reason
or question changing; never a working session's note). After commit the
`Pusher` (`delivery.py`) finds the devices (`services/devices.py`: those
registered with a token that is still live) and sends each an FCM HTTP v1 *data*
message (`messages.message_for`), through `fcm.FcmSender`, which mints its own
OAuth token from the service account. A device FCM calls `UNREGISTERED` is
deleted. Phones register with `POST /devices` (`write` scope) and leave with
`DELETE /devices/{id}`; the table is `push_device` (migration 0045).

Set `CYLIST_FCM_SERVICE_ACCOUNT_FILE` to a service-account key's path to send
for real. Unset, which is normal for development and CI, nothing is sent and
each push is a debug log line. The tests fake Google and FCM with
`httpx.MockTransport` (`tests/test_push.py`).

**App side** (`android/app/src/main/kotlin/cylist/app/push/`). Firebase is
configured without the google-services plugin. `app/build.gradle.kts` reads
`google-services.json` from the Gradle property `cylist.googleServices`, the
environment variable `CYLIST_GOOGLE_SERVICES_JSON`, or `app/google-services.json`
(git-ignored), and turns the client whose package name equals the variant's
application id into `BuildConfig.FIREBASE_*`. With no file, or no client for
that id, the fields are empty and push is off in that build, which is why CI
builds without one. Debug builds are `in.cylist.app.debug`, so the Firebase
project needs a client registered for that id as well as `in.cylist.app`.

```bash
cp ~/.secrets/cylist-firebase/google-services.json android/app/   # never commit it
# or: CYLIST_GOOGLE_SERVICES_JSON=~/.secrets/cylist-firebase/google-services.json ./gradlew assembleDebug
```

The app registers its FCM token after every sign-in and launch (`PushRegistrar`)
and unregisters before signing out, through the `beforeSignOut` hook on
`Sessions`. `PushService` receives the data messages, and `AgentNotifier` posts
them with `NotificationCompat` and `setRequestPromotedOngoing`. More › Notifications
shows whether notifications and Live Updates are allowed, and where OxygenOS
keeps its per-app Live Alerts switch for the camera pill.

**Without Firebase**, a debug build can be sent a push by hand. It goes through
the same `PushService.deliver` a real one does, and adb's shell is the only
sender allowed:

```bash
adb shell am broadcast -n in.cylist.app.debug/cylist.app.push.DebugPushReceiver \
  --es task ATL-12 --es task_title "Import the March schedule" \
  --es state waiting --es reason question --es note "Sheet 1 or Mar-2026?"
```

## Status

All six phases of the [plan](PLAN.md) are built: authentication and the audit
trail, projects and people, the Kanban board, files, the vault, polish, and the
agent tooling.

```bash
make seed    # fills an empty database with three worked-through projects
```
