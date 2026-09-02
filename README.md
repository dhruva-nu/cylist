# Cylist

A project workspace that keeps a project's **board**, **files**, **vault** and
**people** in one place — and exposes all of it over one HTTP API, so your
agents can use it as readily as you can.

| Area | What it holds |
| --- | --- |
| **Kanban board** | Tasks as cards across 2–8 named columns, each of which folds down to a rail when you would rather not look at it. A card that goes *on hold* or *blocked* must say why, and can name the person it is waiting on. |
| **Files** | Folders of uploaded files, with SharePoint and Google Drive links sitting alongside them. |
| **Vault** | Logins, keys and links in trees you shape yourself. Secrets are encrypted at rest and revealed only on request. |
| **People** | Team members and clients, what each is responsible for, and who to tag when work stalls. One of them is **you**, and joins every project you start. |

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
rather than components — the `@`-tag matcher in `components/mentions.ts` — which
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

### Auditing

Every mutation writes a row to `activity` recording who did it, what changed,
and whether it came through the browser or the API. `GET /activity` answers
"what did the CLI change at 3am?".

That trail is read back two ways. A card's own history —
`GET /tasks/{ref}/history` — is what has been done to one piece of work.
`GET /projects/{ref}/reports/day` is the other cut: one day of it, grouped by
card, with a paste-ready Markdown note for a stand-up. The day is midnight to
midnight in whichever zone the caller names, so an evening's work stays in the
evening it happened.

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
:8001 and on the tailnet only. The `staging` branch deploys itself there, and
`make staging-refresh` reloads it from production's database and files — so what
it rehearses is a real release against real rows.

```bash
make staging-deploy    # or just push to `staging`
make staging-refresh   # reload it from production
```

Because it is restored from production it holds real vault ciphertext, and its
secrets deserve production's care. DEPLOY.md says what that means.

## The CLI and the MCP server

Everything the web app does is an HTTP call, so two other clients ship with it.

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

The MCP server in `mcp/` exposes the same surface to Claude Code and other agents.
It registers `reveal_secret` **only** when its token carries `vault:reveal`, so an
agent is never offered a tool that will always fail. See `mcp/README.md` for the
`claude mcp add` line.

Mint a token scoped to what the agent actually needs:

```bash
curl -X POST localhost:8000/api/v1/tokens -b cookies \
  -d '{"name":"board agent","scopes":["read","write"]}'
```

## Status

All six phases of the [plan](PLAN.md) are built: authentication and the audit
trail, projects and people, the Kanban board, files, the vault, polish, and the
agent tooling.

```bash
make seed    # fills an empty database with three worked-through projects
```
