# `cylist-mcp` — Cylist as MCP tools

An [MCP](https://modelcontextprotocol.io) server that puts Cylist's HTTP API in
front of an agent: read a board, create and move cards, change a status with a
reason, comment, browse files and the vault. It speaks to `/api/v1` over HTTP
and imports nothing from `backend/` — its authority is exactly the authority of
the token it is configured with.

```
uv sync
```

## Setting it up

One command, from the CLI, which does this and the rest of it — token, Claude
Code's lifecycle hooks, and registering this server:

```
cylist setup
```

See [`cli/README.md`](../cli/README.md). Nothing below is needed if you have
run that; it is here for an MCP client that is not Claude Code, or a
configuration you would rather write yourself.

## Where the token comes from

The server needs a token, and looks in two places:

1. `CYLIST_TOKEN` and `CYLIST_URL` in the environment — the `env` block of an
   MCP client's configuration.
2. `~/.config/cylist/config.toml`, mode 0600, written by `cylist setup`.

**The file is the better one, and not only for convenience.** A token in an
`env` block means a live credential written into a `.mcp.json` that lives in a
repository, which is how they get committed by accident. Reading the CLI's
file means the registration holds no secret at all, and that moving the server
is one edit rather than one per client pointing at it.

The environment wins where it is set, so a client that wants to be explicit —
a different server, a narrower token — says so and is obeyed.

To mint a token by hand, from a credential that already holds the `admin`
scope, granting only what the agent actually needs:

```
curl -sX POST http://localhost:8000/api/v1/tokens \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"board agent","scopes":["read","write"]}'
```

The response contains the plaintext token exactly once.

**An agent that moves cards wants `read,write` and nothing more.** The scopes
are separate so that you can decline to hand out the dangerous one:

| Scope | Lets the agent |
|---|---|
| `read` | see projects, boards, files, and the *structure* of the vault |
| `write` | create and move tasks, comment, change status, add links |
| `vault:read` | read a secret's username, URL and notes — never its value |
| `vault:reveal` | decrypt a stored credential; every use is written to the audit log |
| `admin` | mint and revoke tokens |

Give `vault:reveal` only to an agent whose actual job is to use a credential,
and never give `admin` to an agent at all — with it, a token can widen its own
authority, which is the one thing scopes exist to prevent.

`reveal_secret` is registered **only** when the configured token carries
`vault:reveal`. The server checks `GET /me` at startup and simply omits the
tool otherwise, rather than advertising one that would always fail: a model
reading a 403 cannot tell a missing scope from a mistake it made, so it
reasonably tries again with different arguments and burns turns discovering
that nothing will work.

## One server, several addresses

`GET /setup` on the backend returns every address a deployment answers on, and
`cylist setup` stores all of them in that config file. This server reads the
list and tries them in order, moving on only when it cannot *connect* — so a
laptop that suspends on a tailnet and wakes somewhere else reconnects instead
of answering every tool call with "cannot reach the Cylist API".

Only a failure to connect is retried elsewhere. A timeout after the connection
was made might mean a write that was applied and whose response was lost.

The line it prints to stderr at startup names the address that answered, which
is the answer to "why is it slow" on a machine that thinks it is still at home.

## Point Claude Code at it

`cylist setup` does this. By hand:

```
claude mcp add cylist \
  --scope user \
  -- uv --directory /absolute/path/to/cylist/mcp run cylist-mcp
```

Flags come before the server name; `--` separates them from the command to
run. `--scope` is one of `local` (default — this project, just you), `project`
(shared through `.mcp.json`) or `user` (all your projects) — `user` is the
right one for a board, which is a property of the machine rather than of one
checkout.

No `--env` block: the token comes from `~/.config/cylist/config.toml`. Add
`--env CYLIST_URL=…` and `--env CYLIST_TOKEN=…` only for a server or a token
that is *not* the one this machine is set up with.

Or write `.mcp.json` at the repository root by hand:

```json
{
  "mcpServers": {
    "cylist": {
      "type": "stdio",
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/cylist/mcp", "run", "cylist-mcp"]
    }
  }
}
```

A project-scoped `.mcp.json` is not trusted silently: anyone who clones the
repository is prompted to approve the server the first time Claude Code starts,
and until they do, `claude mcp list` shows it as pending. They can also manage
it from `/mcp` in a session, and reset a refusal with
`claude mcp reset-project-choices`.

That file holds no credential now, which is the point of reading the CLI's.
If you do put a token in an `env` block, treat the file as a secret — keep it
out of the repository, or point `CYLIST_TOKEN` at something your secret
manager substitutes.

Related: `claude mcp list`, `claude mcp get cylist`, `claude mcp remove cylist`.

## The tools

| Tool | Does |
|---|---|
| `list_projects` | every project, with keys and counts |
| `get_project` | one project's numbers **and its board's columns** |
| `list_tasks` | the cards on a board, filterable by status or assignee |
| `get_task` | one card with its whole timeline |
| `read_task_history` | what has been *done* to one card, and by whom — paged |
| `create_task` | add a card to the first column |
| `create_subtask` | split a card into owned work of its own, `ATL-41-2` |
| `finish_subtask` | tick a sub-task off, or reopen one |
| `add_checklist_item` | add a tick-box sub-task to a card |
| `set_checklist_item` | tick, cancel, reopen or retitle one |
| `move_task` | move a card to a named column |
| `set_task_status` | active / hold / blocked / cancelled, with a reason and tags |
| `list_goals` | a project's epics, each with the progress counted from its cards |
| `get_goal` | one goal and every card on it, in board order |
| `create_goal` | start an epic, with an owner and an optional target date |
| `set_task_goal` | put a card on a goal, or take it off the one it is on |
| `set_goal_status` | open / achieved / dropped — achieving is refused over open cards |
| `add_comment` | write to a card's timeline |
| `list_people` | the directory, or one project's members |
| `list_files` | folders and items, by path |
| `add_link` | file a URL into a folder |
| `list_skills` | the skills uploaded for a project's agents |
| `read_skill` | one skill's own text — the instructions to follow |
| `read_scratchpad` | what agents before you learned about this project |
| `note_learned` | leave one line on the scratchpad, 280 characters |
| `list_vault` | trees and structure — never a value |
| `read_activity` | the audit feed |
| `day_report` | what was done on a project on one day, with a paste-ready note |
| `reveal_secret` | decrypt one credential — **only with `vault:reveal`** |

Two conveniences worth knowing, both described in the tool schemas themselves:

- **Human references, not UUIDs.** A project is `ATL`, a task is `ATL-41`, and
  a column, person or folder may be given by name (`"In progress"`,
  `"Aditi K"`, `"Contracts/2026"`). An ambiguous name returns an error listing
  the candidates rather than picking one.
- **`get_project` returns the columns**, so an agent can call `move_task` with
  a column name it has actually seen instead of inventing one.
- **Sub-tasks are not on the board.** Both kinds — the referenced kind and tick
  boxes — belong to the card they were split out of: they have no column, they
  are not in `list_tasks`, and `finish_subtask` completes one rather than
  `move_task`, which refuses them.
- **Sub-tasks gate the last column.** Every one of them must be finished or
  cancelled before `move_task` will put the parent in the board's last column;
  the refusal names what is still outstanding.
- **The last column is done, and may say how.** A card moved into it is
  finished and one moved back out is reopened, both recorded in its history.
  That column alone can be divided into up to three outcomes — "Done",
  "Cancelled", "In prod" — which `get_project` lists beside it and `move_task`
  takes by name; a template can narrow which of them its own cards may end on.
- **Progress is reported by the harness, not by the agent.** A Claude Code
  session bound to a card with `cylist work ATL-41` or `/work ATL-41` shows on
  the board as working, waiting on a human, or done, driven by the harness's
  lifecycle hooks — installed by `cylist setup` alongside this server. There is no tool for it and nothing to announce;
  `agent_session` on a card is what it looks like from the outside, and
  `get_task`'s `agent_sessions` says which sessions have been on it.
- **Goals gate their own closing.** `set_goal_status(..., "achieved")` is
  refused while a card on the goal is neither in the board's last column nor
  cancelled, and the refusal names every card holding it open. Dropping a goal
  is never refused — its cards stay on the board either way.

### Errors are results, not exceptions

Every tool catches API failures and returns a `CallToolResult` with `is_error`
set, a sentence explaining what happened, and the API's error envelope as
structured content — so a model can correct itself and retry:

```
set_task_status(task="ATL-41", status="blocked")
→ is_error: true
  "A reason is required to set a task to 'blocked'. Call again with reason
   set to what is blocking the work."
```

That particular check happens locally, before any HTTP, so the round trip is
not spent learning something the schema already knew. A 403 says explicitly
that the token must be *reissued* rather than the call retried, because
retrying a scope failure is nothing but wasted turns.

## Development

```
uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest -q
```

The tests call tools through `MCPServer.call_tool` against an
`httpx.MockTransport` standing in for the API (`tests/fake_api.py`), and cover
the scope gate in both directions — that `reveal_secret` is absent with a
`read,write` token and present with `vault:reveal`.
