# REST conventions

How the API under `/api/v1` is addressed, as it is today and as new routes
should be. Written from the OpenAPI schema (`android/api/openapi.json`, which
`make openapi-check` keeps true), so each rule below has examples in it and each
exception is named. The routes that break a rule are listed at the end with what
it would take to bring them in line; none has been changed, because a rename is
a break for the frontend (`frontend/src/api/*.ts`), the CLI, the MCP server and
the Android client alike.

## Addressing

**A collection lives under what owns it.** `GET`/`POST /projects/{project_ref}/<plural>`
lists and creates: `goals`, `roles`, `hooks`, `templates`, `skills`, `folders`,
`columns`, `doc-topics`, `docs`, `tasks`, `vault/trees`. The project is in the
path because creating something there is a statement about the project, and
because that is where the caller's permission is decided.

**A row, once it exists, is addressed by itself.** `GET`/`PATCH`/`DELETE
/<plural>/{id}`: `/goals/{goal_ref}`, `/folders/{folder_id}`, `/items/{item_id}`,
`/skills/{skill_id}`, `/templates/{template_id}`, `/vault/nodes/{node_id}`,
`/comments/{comment_id}`. Ids are global, so the project is found from the row;
repeating it in the path would only give a second way to be wrong. Sub-collections
of a row hang off it: `/tasks/{task_ref}/comments`, `/folders/{folder_id}/children`,
`/goals/{goal_ref}/tasks`.

**Two spellings of an identifier.** `{x_ref}` takes the human reference *or* the
id — a project's key (`ATL`), a card's (`ATL-41`), a goal's (`ATL-G1`), a role's
name — and is used where people type or paste addresses. `{x_id}` is a UUID only.
A new resource gets a reference only if a person will say it aloud.

**Names.** Plural nouns, lower-case, kebab-case where there are two words
(`doc-topics`, `cold-storage`, `agent-prompts`). Singular only for a thing there is
one of: `/me`, `/people/{id}/account`, `/permissions`.

## Verbs

| You want to… | Use | Example |
|---|---|---|
| read, list | `GET` | `GET /projects/ATL/goals` |
| make one | `POST` on the collection | `POST /projects/ATL/hooks` |
| change some of its fields | `PATCH` on the row | `PATCH /goals/ATL-G1` |
| replace a whole set | `PUT` on the set | `PUT /projects/ATL/members`, `PUT /roles/{r}/permissions`, `PUT …/columns/order` |
| remove | `DELETE` on the row | `DELETE /hooks/{id}` |
| do something that is not a field edit | `POST` on a sub-resource named for the act | `POST /tasks/ATL-41/move`, `…/finish`, `…/restore`, `POST /vault/nodes/{id}/reveal`, `POST …/hooks/{id}/fire` |
| upsert by a key the caller chose | `PUT` on the row | `PUT /tasks/{ref}/agent-sessions/{client_session_id}` |

An act is a `POST` because it is not idempotent and not a state the row can be
`PATCH`ed into: a move writes history, a reveal writes an audit entry. If it can be
described as "set this field to that", it is a `PATCH` and not an act.

## Responses

* Lists are cursor-paged: `?limit=` and `?after=`, answering `{ "items": […], "next": "…" | null }`.
  `limit` is capped at 100; a cursor that no longer points at anything is refused
  with 422, never silently restarted.
* Every error is `{ "error": { "code", "reason", "message", "details" } }`. `code`
  follows the status and is what a client branches on first; `reason` is the
  finer, stable distinction (`duplicate_name`, `last_admin`, …) and may be null.
  See `app/core/errors.py` for the status each class means; in particular 403 is
  "not allowed by *role*" and 422 is "no caller could ask this".
* Nothing under `/api/v1` is cached by the server; a client that wants less
  traffic uses the realtime socket, not conditional requests.

## Trees

Four things in the schema are parent-linked, and they are not the same shape, so
they do not share a table or a route — only the walks:

| | Shape | Where |
|---|---|---|
| Folders | arbitrary depth, capped at `MAX_DEPTH`, read from the database | `services/files.py` |
| Vault nodes | arbitrary depth inside one tree, loaded whole | `services/vault.py` |
| Docs | fixed: section → topic → doc | `services/docs/` |
| Cards | one level: a card and its sub-tasks | `services/tasks/` |

The two walks they actually repeated are shared: `app/core/tree.py` over a tree
already in memory (`ancestors`, `is_inside`), and `app/services/trees.py` over one
in the database (`subtree_ids`, `ancestors`, each a single recursive query). A
new hierarchy should use them rather than climb a row at a time.

## Where the routes break these rules

| Route | Rule it breaks | Brought in line by | Breaking for |
|---|---|---|---|
| `POST /vault/nodes` (the tree is in the body) | create under the owner | `POST /vault/trees/{tree_id}/nodes` | frontend `vault.ts`, CLI, MCP |
| `PUT /projects/{p}/doc-topics/order` and `PUT /doc-topics/{id}/order` | one noun, two meanings of "order" | `…/doc-topics/order` for topics, `…/doc-topics/{id}/docs/order` for the docs | frontend `docs.ts`, MCP |
| `…/permissions/everyone-else/{clearance,columns}` beside `…/roles/{r}/{clearance,columns,permissions}` | "everyone else" is a role in every way but its address | `…/roles/everyone-else/…` | frontend `roles.ts`, Android |
| `POST /tasks/{ref}/checklist` and `POST /tasks/{ref}/subtasks` | a collection created through a singular | `…/checklist-items`; keep `checklist` as an alias | frontend `tasks.ts`, MCP |
| `GET /projects/{p}/tree` and `GET /projects/{p}/items` | the folder tree and the flat file list are two views of `folders` | `…/folders?view=tree|flat` | frontend `files.ts` |

None of these is urgent, and none should be done alone: each wants the new route
added beside the old, clients moved over, and the old one answering with
`Deprecation`/`Sunset` headers for a release before it goes. Do them together,
once, so clients are updated once.
