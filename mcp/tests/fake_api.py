"""An in-memory Cylist API for the MCP server's tests.

Same idea as the CLI's fake: a stand-in for the *server*, so a test exercises
the real tool body, the real name resolution and the real request shaping, and
can then assert on the HTTP that came out the other side.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

PROJECT_ID = "0192f3c4-0000-7000-8000-00000000a71a"
BACKLOG_ID = "0192f3c4-0001-7000-8000-000000000001"
DOING_ID = "0192f3c4-0001-7000-8000-000000000002"

ADITI_ID = "0192f3c4-0002-7000-8000-00000000ad17"
LENA_ID = "0192f3c4-0002-7000-8000-00000000012a"
LEO_ID = "0192f3c4-0002-7000-8000-0000000001e0"

TASK_ID = "0192f3c4-0003-7000-8000-000000000002"
CONTRACTS_ID = "0192f3c4-0004-7000-8000-000000000001"
NESTED_ID = "0192f3c4-0004-7000-8000-000000000002"

TREE_ID = "0192f3c4-0006-7000-8000-000000000001"
BILLING_ID = "0192f3c4-0007-7000-8000-000000000001"
STRIPE_ID = "0192f3c4-0007-7000-8000-000000000002"

STRIPE_SECRET = "sk_live_do_not_log_me"  # a fixture, not a credential

ADITI = {
    "id": ADITI_ID,
    "name": "Aditi K",
    "kind": "team",
    "role": "Engineer, Atlas",
    "responsibilities": "Ships the migration.",
    "email": "aditi@example.com",
    "colour": "#c8553d",
    "archived_at": None,
    "created_at": "2026-01-04T09:00:00Z",
}
LENA = {**ADITI, "id": LENA_ID, "name": "Lena W", "kind": "client", "email": "lena@example.com"}
LEO = {**ADITI, "id": LEO_ID, "name": "Leo Wren", "kind": "client", "email": None}

PROJECT = {
    "id": PROJECT_ID,
    "key": "ATL",
    "name": "Atlas migration",
    "description": "Moving Atlas off the legacy billing stack.",
    "colour": "#e8b647",
    "archived_at": None,
    "created_at": "2026-01-02T09:00:00Z",
    "member_count": 3,
}

SUMMARY = {
    **PROJECT,
    "team_count": 1,
    "client_count": 2,
    "task_count": 1,
    "column_count": 2,
    "blocked_count": 0,
    "on_hold_count": 0,
    "folder_count": 2,
    "file_count": 1,
    "vault_tree_count": 1,
    "vault_secret_count": 1,
}

COLUMNS = {
    "columns": [
        {
            "id": BACKLOG_ID,
            "project_id": PROJECT_ID,
            "name": "Backlog",
            "description": "Not started.",
            "position": 0,
            "task_count": 0,
        },
        {
            "id": DOING_ID,
            "project_id": PROJECT_ID,
            "name": "In progress",
            "description": "Being worked on.",
            "position": 1,
            "task_count": 1,
        },
    ],
    "min_columns": 2,
    "max_columns": 8,
}

TASK = {
    "id": TASK_ID,
    "project_id": PROJECT_ID,
    "reference": "ATL-2",
    "number": 2,
    "column_id": DOING_ID,
    "position": 0,
    "title": "Switch the invoice job over",
    "description": "Point the nightly job at the new ledger.",
    "type": "feature",
    "due_date": "2026-03-31",
    "assignee": ADITI,
    "status": "active",
    "jira_ref": None,
    "pr_ref": None,
    "waiting_on": [],
    "comment_count": 0,
    "created_at": "2026-02-01T09:00:00Z",
    "comments": [],
}

FOLDER_TREE = [
    {
        "id": CONTRACTS_ID,
        "name": "Contracts",
        "parent_id": None,
        "children": [{"id": NESTED_ID, "name": "2026", "parent_id": CONTRACTS_ID, "children": []}],
    }
]

ITEM = {
    "id": "0192f3c4-0005-7000-8000-000000000002",
    "folder_id": NESTED_ID,
    "kind": "link",
    "name": "Signed MSA",
    "url": "https://example.invalid/msa",
    "source": "sharepoint",
    "size": None,
    "mime": None,
    "added_by": LENA,
    "created_at": "2026-02-05T09:00:00Z",
}

VAULT_TREES = [
    {
        "id": TREE_ID,
        "project_id": PROJECT_ID,
        "name": "Logins",
        "position": 0,
        "node_count": 2,
        "secret_count": 1,
        "created_at": "2026-01-10T09:00:00Z",
        "updated_at": "2026-01-10T09:00:00Z",
    }
]

VAULT_TREE_DETAIL = {
    **VAULT_TREES[0],
    "nodes": [
        {
            "id": BILLING_ID,
            "tree_id": TREE_ID,
            "parent_id": None,
            "name": "Billing",
            "kind": "branch",
            "position": 0,
            "created_at": "2026-01-11T09:00:00Z",
            "updated_at": "2026-01-11T09:00:00Z",
            "secret": None,
            "children": [
                {
                    "id": STRIPE_ID,
                    "tree_id": TREE_ID,
                    "parent_id": BILLING_ID,
                    "name": "Stripe",
                    "kind": "secret",
                    "position": 0,
                    "created_at": "2026-01-11T09:00:00Z",
                    "updated_at": "2026-01-11T09:00:00Z",
                    "secret": {
                        "username": "billing@example.com",
                        "url": "https://dashboard.stripe.com",
                        "notes": "Live key.",
                        "key_version": 1,
                        "updated_at": "2026-01-11T09:00:00Z",
                    },
                    "children": [],
                }
            ],
        }
    ],
}

ACTIVITY = [
    {
        "id": "0192f3c4-0009-7000-8000-000000000001",
        "occurred_at": "2026-02-03T09:00:00Z",
        "actor_label": "board-tidy agent",
        "channel": "api",
        "verb": "task.moved",
        "entity_type": "task",
        "entity_id": TASK_ID,
        "project_id": PROJECT_ID,
        "payload": {"reference": "ATL-2"},
    }
]


def identity(scopes: list[str]) -> dict[str, Any]:
    return {
        "token_id": "0192f3c4-000a-7000-8000-000000000001",
        "label": "board-tidy agent",
        "channel": "api",
        "scopes": scopes,
    }


@dataclass
class Recorder:
    """Every request the server made."""

    requests: list[httpx.Request] = field(default_factory=list)

    def sent(self, method: str, path: str) -> httpx.Request:
        for request in self.requests:
            if request.method == method and request.url.path.endswith(path):
                return request
        raise AssertionError(f"No {method} {path} was sent. Sent: " + ", ".join(self.paths()))

    def body(self, method: str, path: str) -> dict[str, Any]:
        payload = json.loads(self.sent(method, path).content or b"{}")
        assert isinstance(payload, dict)
        return payload

    def paths(self) -> list[str]:
        return [f"{item.method} {item.url.path}" for item in self.requests]


def build(
    recorder: Recorder,
    *,
    scopes: list[str] | None = None,
    overrides: dict[tuple[str, str], httpx.Response] | None = None,
) -> httpx.MockTransport:
    """An async transport answering the endpoints the tools use."""
    table = overrides or {}
    granted = scopes if scopes is not None else ["read", "write"]

    def handler(request: httpx.Request) -> httpx.Response:
        recorder.requests.append(request)
        path = request.url.path.removeprefix("/api/v1")
        key = (request.method, path)
        if key in table:
            return table[key]
        return _route(request, path, granted)

    return httpx.MockTransport(handler)


def _route(request: httpx.Request, path: str, scopes: list[str]) -> httpx.Response:
    method = request.method

    if path == "/me":
        return httpx.Response(200, json=identity(scopes))

    if path == "/projects":
        return httpx.Response(200, json=[PROJECT])
    if path in {"/projects/ATL", f"/projects/{PROJECT_ID}"}:
        return httpx.Response(200, json=PROJECT)
    if path.endswith("/summary"):
        return httpx.Response(200, json=SUMMARY)
    if path.endswith("/columns"):
        return httpx.Response(200, json=COLUMNS)
    if path.endswith("/members"):
        return httpx.Response(200, json={"members": [ADITI, LENA, LEO]})

    if path.endswith("/tasks") and method == "GET":
        return httpx.Response(200, json=[TASK])
    if path.endswith("/tasks") and method == "POST":
        body = json.loads(request.content)
        return httpx.Response(201, json={**TASK, "title": body["title"]})
    if path in {"/tasks/ATL-2", f"/tasks/{TASK_ID}"}:
        return httpx.Response(200, json=TASK)
    if path.endswith("/move"):
        body = json.loads(request.content)
        return httpx.Response(200, json={**TASK, "column_id": body["column_id"]})
    if path.endswith("/status"):
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                **TASK,
                "status": body["status"],
                "waiting_on": [LENA] if body.get("waiting_on") else [],
            },
        )
    if path.endswith("/comments") and method == "POST":
        body = json.loads(request.content)
        return httpx.Response(
            201,
            json={
                "id": "0192f3c4-0008-7000-8000-000000000009",
                "task_id": TASK_ID,
                "author": None,
                "body": body["body"],
                "kind": "comment",
                "meta": {},
                "created_at": "2026-02-06T09:00:00Z",
            },
        )

    if path == "/people":
        kind = request.url.params.get("kind")
        people = [ADITI, LENA, LEO]
        if kind:
            people = [person for person in people if person["kind"] == kind]
        return httpx.Response(200, json=people)

    if path.endswith("/tree"):
        return httpx.Response(200, json=FOLDER_TREE)
    if path == f"/folders/{NESTED_ID}/children":
        return httpx.Response(
            200,
            json={
                "folder": {
                    "id": NESTED_ID,
                    "project_id": PROJECT_ID,
                    "parent_id": CONTRACTS_ID,
                    "name": "2026",
                    "created_at": "2026-01-20T09:00:00Z",
                },
                "path": [
                    {"id": CONTRACTS_ID, "name": "Contracts"},
                    {"id": NESTED_ID, "name": "2026"},
                ],
                "folders": [],
                "items": [ITEM],
            },
        )
    if path == f"/folders/{NESTED_ID}/links":
        body = json.loads(request.content)
        return httpx.Response(201, json={**ITEM, "name": body["name"], "url": body["url"]})

    if path.endswith("/vault/trees"):
        return httpx.Response(200, json=VAULT_TREES)
    if path == f"/vault/trees/{TREE_ID}":
        return httpx.Response(200, json=VAULT_TREE_DETAIL)
    if path == f"/vault/nodes/{STRIPE_ID}/reveal":
        return httpx.Response(
            200,
            json={
                "node_id": STRIPE_ID,
                "name": "Stripe",
                "value": STRIPE_SECRET,
                "revealed_at": "2026-02-10T09:00:00Z",
            },
        )

    if path == "/activity":
        return httpx.Response(200, json=ACTIVITY)

    return httpx.Response(
        404,
        json={
            "error": {
                "code": "not_found",
                "message": f"No route for {method} {path} in the fake API.",
                "details": {},
            }
        },
    )
