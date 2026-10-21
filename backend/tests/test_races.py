"""Two requests doing the same thing at the same moment (CYLIST-125).

Each of these writes checks first and writes second, and the database's
constraints are what really decide. When two requests both pass the check, the
one that loses at the constraint must hear the same 409 the check would have
given it — never a 500 from an integrity error.

Two kinds of test, because two kinds of fix. Where the fix is catching the
constraint's refusal, the check is switched off to put the request exactly
where a race would: past the check, and about to collide. Where the fix is a
lock or a single upsert, the two requests are really sent at once.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.services import goals, hooks, people, templates
from tests.conftest import INVITEE_PASSWORD, client_on, open_account, put_members

ATLAS = {"key": "ATL", "name": "Atlas Billing Migration"}
TEAM = {
    "name": "Aditi K",
    "kind": "team",
    "title": "Backend engineer",
    "responsibilities": "Payments and webhooks.",
}
RECEIVER = "https://hooks.example.test/cylist"


async def _never(*_: object, **__: object) -> None:
    """A pre-check that a racing request has already passed."""


async def _project(client: AsyncClient) -> str:
    await client.post("/projects", json=ATLAS)
    person = (await client.post("/people", json=TEAM)).json()["id"]
    await put_members(client, "ATL", [person])
    return str(person)


async def _columns(client: AsyncClient) -> list[dict[str, Any]]:
    columns: list[dict[str, Any]] = (await client.get("/projects/ATL/columns")).json()["columns"]
    return columns


class TestLosingTheRaceForAName:
    async def test_a_goal(self, signed_in: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
        person = await _project(signed_in)
        body = {"name": "Search revamp", "owner_id": person}
        assert (await signed_in.post("/projects/ATL/goals", json=body)).status_code == 201
        monkeypatch.setattr(goals, "_refuse_duplicate", _never)

        response = await signed_in.post("/projects/ATL/goals", json=body)

        assert response.status_code == 409, response.text
        assert "already has a goal called 'Search revamp'" in response.json()["error"]["message"]

    async def test_a_goal_renamed(
        self, signed_in: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        person = await _project(signed_in)
        await signed_in.post("/projects/ATL/goals", json={"name": "Search", "owner_id": person})
        other = (
            await signed_in.post(
                "/projects/ATL/goals", json={"name": "Billing", "owner_id": person}
            )
        ).json()
        monkeypatch.setattr(goals, "_refuse_duplicate", _never)

        response = await signed_in.patch(f"/goals/{other['id']}", json={"name": "Search"})

        assert response.status_code == 409, response.text

    async def test_a_template(
        self, signed_in: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _project(signed_in)
        assert (
            await signed_in.post("/projects/ATL/templates", json={"name": "Hotfix"})
        ).status_code == 201
        monkeypatch.setattr(templates, "_refuse_duplicate_template", _never)

        response = await signed_in.post("/projects/ATL/templates", json={"name": "Hotfix"})

        assert response.status_code == 409, response.text
        assert "already has a template" in response.json()["error"]["message"]

    async def test_a_hook(self, signed_in: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
        await _project(signed_in)
        body = {"name": "Deploys", "url": RECEIVER}
        assert (await signed_in.post("/projects/ATL/hooks", json=body)).status_code == 201
        monkeypatch.setattr(hooks, "_refuse_taken_name", _never)

        response = await signed_in.post("/projects/ATL/hooks", json=body)

        assert response.status_code == 409, response.text
        assert "already has a hook called 'Deploys'" in response.json()["error"]["message"]

    async def test_a_sign_in_address(
        self, signed_in: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        email = "aditi@cylist.dev"
        first = (await signed_in.post("/people", json=TEAM)).json()
        second = (await signed_in.post("/people", json={**TEAM, "name": "Aditi R"})).json()
        opened = await signed_in.put(
            f"/people/{first['id']}/account", json={"email": email, "password": INVITEE_PASSWORD}
        )
        assert opened.status_code == 200, opened.text
        monkeypatch.setattr(people, "_require_email_free", _never)

        response = await signed_in.put(
            f"/people/{second['id']}/account", json={"email": email, "password": INVITEE_PASSWORD}
        )

        assert response.status_code == 409, response.text
        assert email in response.json()["error"]["message"]


class TestTwoAtOnce:
    async def test_the_same_new_file_uploaded_twice(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        root = (await signed_in.get("/projects/ATL/tree")).json()["id"]
        folders = [
            (await signed_in.post("/projects/ATL/folders", json={"name": name})).json()["id"]
            for name in ("One", "Two", "Three")
        ]
        content = b"cylist race fixture: one new file, three uploads\n" * 64

        responses = await asyncio.gather(
            *(
                signed_in.post(
                    f"/folders/{folder}/upload",
                    files={"file": ("brief.pdf", content, "application/pdf")},
                )
                for folder in folders
            )
        )

        assert [response.status_code for response in responses] == [201, 201, 201]
        assert root not in folders

    async def test_the_same_phone_registered_twice(self, signed_in: AsyncClient) -> None:
        created = await signed_in.post(
            "/tokens", json={"name": "Pixel", "scopes": ["read", "write"]}
        )
        headers = {"Authorization": f"Bearer {created.json()['token']}"}
        body = {"fcm_token": "fcm-race-token-0123456789", "label": "Pixel"}

        responses = await asyncio.gather(
            *(signed_in.post("/devices", json=body, headers=headers) for _ in range(4))
        )

        assert [response.status_code for response in responses] == [200] * 4
        assert len({response.json()["id"] for response in responses}) == 1
        assert len((await signed_in.get("/devices", headers=headers)).json()["items"]) == 1

    async def test_the_same_person_added_to_a_project_twice(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        members = (await signed_in.get("/projects/ATL/members")).json()["members"]
        current = [member["id"] for member in members]
        newcomer = (await signed_in.post("/people", json=TEAM)).json()["id"]

        responses = await asyncio.gather(
            *(put_members(signed_in, "ATL", [*current, newcomer]) for _ in range(4))
        )

        assert [response.status_code for response in responses] == [200] * 4

    async def test_a_role_line_saved_twice(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        body = {"permissions": ["board", "files"]}

        responses = await asyncio.gather(
            *(signed_in.put("/projects/ATL/permissions/everyone-else", json=body) for _ in range(4))
        )

        assert [response.status_code for response in responses] == [200] * 4

    async def test_a_cards_column_dates_saved_twice(self, signed_in: AsyncClient) -> None:
        person = await _project(signed_in)
        card = (
            await signed_in.post(
                "/projects/ATL/tasks",
                json={
                    "title": "Stripe webhook idempotency",
                    "description": "Duplicate deliveries create double payments.",
                    "type": "bug",
                    "assignee_id": person,
                },
            )
        ).json()
        column = (await _columns(signed_in))[0]["id"]
        body = {"column_due_dates": [{"column_id": column, "due_date": "2026-11-02"}]}

        responses = await asyncio.gather(
            *(signed_in.patch(f"/tasks/{card['id']}", json=body) for _ in range(4))
        )

        assert [response.status_code for response in responses] == [200] * 4

    async def test_a_template_deleted_while_a_card_is_made_from_it(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _project(signed_in)
        template = (await signed_in.post("/projects/ATL/templates", json={"name": "Hotfix"})).json()

        created, deleted = await asyncio.gather(
            signed_in.post(
                "/projects/ATL/tasks",
                json={
                    "title": "Hotfix the webhook",
                    "description": "Now.",
                    "type": "bug",
                    "assignee_id": person,
                    "template_id": template["id"],
                },
            ),
            signed_in.delete(f"/templates/{template['id']}"),
        )

        # Either order is a fair outcome; a 500 is not. The card made first
        # holds the delete off with a 409; deleted first, the card is refused —
        # by its template check, or by the foreign key if it had passed that.
        outcome = (created.status_code, deleted.status_code)
        assert outcome in {(201, 409), (422, 200), (409, 200)}, (created.text, deleted.text)

    async def test_a_column_deleted_while_a_card_moves_into_it(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _project(signed_in)
        added = await signed_in.post(
            "/projects/ATL/columns",
            json={"name": "Review", "description": "Waiting on a reviewer."},
        )
        assert added.status_code == 201, added.text
        column = added.json()["id"]
        card = (
            await signed_in.post(
                "/projects/ATL/tasks",
                json={
                    "title": "Stripe webhook idempotency",
                    "description": "Duplicate deliveries create double payments.",
                    "type": "bug",
                    "assignee_id": person,
                },
            )
        ).json()

        moved, deleted = await asyncio.gather(
            signed_in.post(f"/tasks/{card['id']}/move", json={"column_id": column, "position": 0}),
            signed_in.delete(f"/columns/{column}"),
        )

        assert moved.status_code < 500, moved.text
        assert deleted.status_code < 500, deleted.text
        assert (moved.status_code == 200) != (deleted.status_code == 200)


class TestConsumedExactlyOnce:
    """CYLIST-126: a one-time credential redeemed twice at once opens once."""

    async def test_an_invitation_accepted_twice_at_once(
        self, signed_in: AsyncClient, app: FastAPI
    ) -> None:
        _, token = await open_account(signed_in, TEAM, "aditi@cylist.dev")
        passwords = ["first-browser-password", "second-browser-password"]

        async with client_on(app) as one, client_on(app) as two:
            first, second = await asyncio.gather(
                one.post("/auth/accept-invite", json={"token": token, "password": passwords[0]}),
                two.post("/auth/accept-invite", json={"token": token, "password": passwords[1]}),
            )

        assert sorted([first.status_code, second.status_code]) == [200, 422], (
            first.text,
            second.text,
        )
        # The password that works is the one the accept that succeeded set.
        winner = passwords[0] if first.status_code == 200 else passwords[1]
        loser = passwords[1] if winner == passwords[0] else passwords[0]
        login = {"email": "aditi@cylist.dev"}
        assert (
            await signed_in.post("/auth/login", json={**login, "password": winner})
        ).status_code == 200
        assert (
            await signed_in.post("/auth/login", json={**login, "password": loser})
        ).status_code == 401
