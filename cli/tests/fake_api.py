"""A Cylist API that lives in memory, for the CLI's tests.

Not a mock of the CLI's own client — a stand-in for the *server*. Every test
drives ``cylist_cli.main.main`` exactly as a terminal would, and this transport
answers the HTTP it makes. That way the tests cover argument parsing, name
resolution, request shaping, rendering and exit codes in one pass, and a change
that quietly stops sending ``waiting_on`` is caught by the assertion on the
recorded request body.

The payloads below are shaped like the real API's responses (see
``backend/app/schemas``), trimmed to the fields the CLI reads.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

PROJECT_ID = "0192f3c4-0000-7000-8000-00000000a71a"
BACKLOG_ID = "0192f3c4-0001-7000-8000-000000000001"
DOING_ID = "0192f3c4-0001-7000-8000-000000000002"
DONE_ID = "0192f3c4-0001-7000-8000-000000000003"

ADITI_ID = "0192f3c4-0002-7000-8000-00000000ad17"
LENA_ID = "0192f3c4-0002-7000-8000-00000000012a"
LEO_ID = "0192f3c4-0002-7000-8000-0000000001e0"

TASK_ONE_ID = "0192f3c4-0003-7000-8000-000000000001"
TASK_TWO_ID = "0192f3c4-0003-7000-8000-000000000002"

CONTRACTS_ID = "0192f3c4-0004-7000-8000-000000000001"
NESTED_ID = "0192f3c4-0004-7000-8000-000000000002"
MSA_ID = "0192f3c4-0005-7000-8000-000000000001"
PORTAL_ID = "0192f3c4-0005-7000-8000-000000000002"

TREE_ID = "0192f3c4-0006-7000-8000-000000000001"
BILLING_ID = "0192f3c4-0007-7000-8000-000000000001"
STRIPE_ID = "0192f3c4-0007-7000-8000-000000000002"

STRIPE_SECRET = "sk_live_do_not_log_me"

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
LENA = {
    "id": LENA_ID,
    "name": "Lena W",
    "kind": "client",
    "role": "Finance controller, Atlas",
    "responsibilities": "Approves the invoice schedule.",
    "email": "lena@example.com",
    "colour": "#3d5a80",
    "archived_at": None,
    "created_at": "2026-01-05T09:00:00Z",
}
LEO = {
    "id": LEO_ID,
    "name": "Leo Wren",
    "kind": "client",
    "role": "Legal, Atlas",
    "responsibilities": "Signs the contracts.",
    "email": None,
    "colour": "#7d8471",
    "archived_at": None,
    "created_at": "2026-01-06T09:00:00Z",
}

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
    "task_count": 2,
    "column_count": 3,
    "blocked_count": 1,
    "on_hold_count": 0,
    "folder_count": 2,
    "file_count": 2,
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
            "task_count": 1,
        },
        {
            "id": DOING_ID,
            "project_id": PROJECT_ID,
            "name": "In progress",
            "description": "Being worked on.",
            "position": 1,
            "task_count": 1,
        },
        {
            "id": DONE_ID,
            "project_id": PROJECT_ID,
            "name": "Done",
            "description": "Finished.",
            "position": 2,
            "task_count": 0,
        },
    ],
    "min_columns": 2,
    "max_columns": 8,
}


def _task(
    task_id: str,
    number: int,
    column_id: str,
    title: str,
    status: str = "active",
    waiting_on: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": task_id,
        "project_id": PROJECT_ID,
        "reference": f"ATL-{number}",
        "number": number,
        "column_id": column_id,
        "position": 0,
        "title": title,
        "description": "Some detail about the work.",
        "type": "feature",
        "due_date": "2026-03-31",
        "assignee": ADITI,
        "status": status,
        "jira_ref": None,
        "pr_ref": None,
        "waiting_on": waiting_on or [],
        "comment_count": 0,
        "created_at": "2026-02-01T09:00:00Z",
    }


TASK_ONE = _task(TASK_ONE_ID, 1, BACKLOG_ID, "Reconcile the ledger export")
TASK_TWO = _task(
    TASK_TWO_ID, 2, DOING_ID, "Switch the invoice job over", status="blocked", waiting_on=[LENA]
)

TIMELINE = [
    {
        "id": "0192f3c4-0008-7000-8000-000000000001",
        "task_id": TASK_TWO_ID,
        "author": ADITI,
        "body": "Started on this.",
        "kind": "comment",
        "meta": {},
        "created_at": "2026-02-02T09:00:00Z",
    },
    {
        "id": "0192f3c4-0008-7000-8000-000000000002",
        "task_id": TASK_TWO_ID,
        "author": None,
        "body": "Blocked — waiting for the finance sign-off.",
        "kind": "status_change",
        "meta": {
            "from": "active",
            "to": "blocked",
            "reason": "waiting for the finance sign-off",
            "tagged": [LENA_ID],
        },
        "created_at": "2026-02-03T09:00:00Z",
    },
]

FOLDER_TREE = [
    {
        "id": CONTRACTS_ID,
        "name": "Contracts",
        "parent_id": None,
        "children": [{"id": NESTED_ID, "name": "2026", "parent_id": CONTRACTS_ID, "children": []}],
    }
]

MSA = {
    "id": MSA_ID,
    "folder_id": NESTED_ID,
    "kind": "file",
    "name": "msa.txt",
    "url": None,
    "source": "upload",
    "size": 12,
    "mime": "text/plain",
    "added_by": ADITI,
    "created_at": "2026-02-04T09:00:00Z",
}
PORTAL = {
    "id": PORTAL_ID,
    "folder_id": NESTED_ID,
    "kind": "link",
    "name": "portal",
    "url": "https://example.invalid/portal",
    "source": "sharepoint",
    "size": None,
    "mime": None,
    "added_by": LENA,
    "created_at": "2026-02-05T09:00:00Z",
}

MSA_BYTES = b"hello vault\n"

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

STRIPE_NODE = {
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
            "children": [STRIPE_NODE],
        }
    ],
}

ACTIVITY = [
    {
        "id": "0192f3c4-0009-7000-8000-000000000001",
        "occurred_at": "2026-02-03T09:00:00Z",
        "actor_label": "board-tidy agent",
        "channel": "api",
        "verb": "task.status_changed",
        "entity_type": "task",
        "entity_id": TASK_TWO_ID,
        "project_id": PROJECT_ID,
        "payload": {"reference": "ATL-2", "to": "blocked"},
    }
]

IDENTITY = {
    "token_id": "0192f3c4-000a-7000-8000-000000000001",
    "label": "board-tidy agent",
    "channel": "api",
    "scopes": ["read", "write"],
}


@dataclass
class Recorder:
    """Every request the CLI made, so a test can assert on the body it sent."""

    requests: list[httpx.Request] = field(default_factory=list)

    def sent(self, method: str, path: str) -> httpx.Request:
        for request in self.requests:
            if request.method == method and request.url.path.endswith(path):
                return request
        raise AssertionError(
            f"No {method} {path} was sent. Sent: "
            + ", ".join(f"{item.method} {item.url.path}" for item in self.requests)
        )

    def count(self, method: str, path: str) -> int:
        """How many matching requests were sent — 0 proves one was avoided."""
        return sum(
            1
            for request in self.requests
            if request.method == method and request.url.path.endswith(path)
        )

    def body(self, method: str, path: str) -> dict[str, Any]:
        payload = json.loads(self.sent(method, path).content or b"{}")
        assert isinstance(payload, dict)
        return payload

    def paths(self) -> list[str]:
        return [f"{item.method} {item.url.path}" for item in self.requests]


def _error(status: int, code: str, message: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code, "message": message, "details": {}}})


def build(
    recorder: Recorder,
    *,
    overrides: dict[tuple[str, str], httpx.Response] | None = None,
) -> httpx.MockTransport:
    """A transport answering the endpoints the CLI uses.

    ``overrides`` replaces one route, which is how the error-path tests make a
    single endpoint fail without inventing a whole second fake server.
    """
    table = overrides or {}

    def handler(request: httpx.Request) -> httpx.Response:
        recorder.requests.append(request)
        path = request.url.path.removeprefix("/api/v1")
        key = (request.method, path)
        if key in table:
            return table[key]
        return _route(request, path)

    return httpx.MockTransport(handler)


def _route(request: httpx.Request, path: str) -> httpx.Response:
    method = request.method

    if path == "/me":
        return httpx.Response(200, json=IDENTITY)

    if path == "/projects" and method == "GET":
        return httpx.Response(200, json=[PROJECT])
    if path == "/projects" and method == "POST":
        body = json.loads(request.content)
        return httpx.Response(201, json={**PROJECT, "key": body["key"], "name": body["name"]})

    if path in {f"/projects/{ref}" for ref in ("ATL", PROJECT_ID)}:
        return httpx.Response(200, json=PROJECT)
    if path.endswith("/summary"):
        return httpx.Response(200, json=SUMMARY)
    if path.endswith("/columns"):
        return httpx.Response(200, json=COLUMNS)
    if path.endswith("/members") and method == "GET":
        return httpx.Response(200, json={"members": [ADITI, LENA, LEO]})
    if path.endswith("/members") and method == "PUT":
        return httpx.Response(200, json={"members": [ADITI, LENA]})

    if path.endswith("/tasks") and method == "GET":
        return httpx.Response(200, json=[TASK_ONE, TASK_TWO])
    if path.endswith("/tasks") and method == "POST":
        body = json.loads(request.content)
        created = {**TASK_ONE, "title": body["title"], "comments": []}
        return httpx.Response(201, json=created)

    if path in {"/tasks/ATL-1", f"/tasks/{TASK_ONE_ID}"}:
        return httpx.Response(200, json={**TASK_ONE, "comments": []})
    if path in {"/tasks/ATL-2", f"/tasks/{TASK_TWO_ID}"}:
        return httpx.Response(200, json={**TASK_TWO, "comments": TIMELINE})
    if path.endswith("/move"):
        return httpx.Response(200, json={**TASK_TWO, "column_id": DOING_ID, "comments": []})
    if path.endswith("/status"):
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                **TASK_TWO,
                "status": body["status"],
                "waiting_on": [LENA] if body.get("waiting_on") else [],
                "comments": TIMELINE,
            },
        )
    if path.endswith("/comments") and method == "POST":
        return httpx.Response(201, json=TIMELINE[0])

    if path == "/people" and method == "GET":
        kind = request.url.params.get("kind")
        people = [ADITI, LENA, LEO]
        if kind:
            people = [person for person in people if person["kind"] == kind]
        return httpx.Response(200, json=people)
    if path == "/people" and method == "POST":
        body = json.loads(request.content)
        return httpx.Response(201, json={**ADITI, **body})

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
                "items": [MSA, PORTAL],
            },
        )
    if path == f"/folders/{CONTRACTS_ID}/children":
        return httpx.Response(
            200,
            json={
                "folder": {
                    "id": CONTRACTS_ID,
                    "project_id": PROJECT_ID,
                    "parent_id": None,
                    "name": "Contracts",
                    "created_at": "2026-01-20T09:00:00Z",
                },
                "path": [{"id": CONTRACTS_ID, "name": "Contracts"}],
                "folders": [
                    {
                        "id": NESTED_ID,
                        "project_id": PROJECT_ID,
                        "parent_id": CONTRACTS_ID,
                        "name": "2026",
                        "created_at": "2026-01-20T09:00:00Z",
                    }
                ],
                "items": [],
            },
        )
    if path == f"/items/{MSA_ID}/download":
        return httpx.Response(200, content=MSA_BYTES)

    if path.endswith("/vault/trees") and method == "GET":
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
    if path == "/vault/nodes" and method == "POST":
        body = json.loads(request.content)
        return httpx.Response(201, json={**STRIPE_NODE, "name": body["name"]})

    if path == "/activity":
        return httpx.Response(200, json=ACTIVITY)

    return _error(404, "not_found", f"No route for {method} {path} in the fake API.")
