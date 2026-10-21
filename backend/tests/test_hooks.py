"""Hooks: which board changes go out, how they are signed, and what happens when
the receiver is not there.

Three things are worth keeping apart. **Matching** is the rule a user writes —
"a Hotfix card moved into In staging" — and the tests here are mostly about
what it must *not* fire on, since a hook that fires too often is the one that
deploys something nobody asked for. **Queueing** is the transaction: a change
that rolls back must not be announced. **Delivery** is the network, which the
tests answer in place of a receiver through an ``httpx.MockTransport``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import httpx2
import pytest
from httpx import AsyncClient
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import func, select, update

from app.auth.principal import Principal
from app.core.clock import now
from app.db import Database
from app.models.activity import Activity, Channel
from app.models.hook import Hook, HookDelivery
from app.models.task import Task
from app.services import activity, hook_delivery
from tests.conftest import INVITEE_PASSWORD, VAULT_KEY, open_account, put_members

ATLAS = {"key": "ATL", "name": "Atlas Billing Migration"}
HERMES = {"key": "HRM", "name": "Hermes Notifications"}
TEAM = {
    "name": "Aditi K",
    "kind": "team",
    "title": "Backend engineer",
    "responsibilities": "Payments and webhooks.",
}
RECEIVER = "https://ci.example.com/cylist"
MCP_URL = "http://test/mcp"


# --- Setting a board up ----------------------------------------------------


async def _project(client: AsyncClient) -> str:
    """ATL, with a member to own cards and an In staging column before Done."""
    await client.post("/projects", json=ATLAS)
    person = (await client.post("/people", json=TEAM)).json()["id"]
    members = (await client.get("/projects/ATL/members")).json()["members"]
    await put_members(client, "ATL", [member["id"] for member in members] + [person])
    staging = await _add_column(client, "In staging")
    columns = await _columns(client)
    await client.put(
        "/projects/ATL/columns/order",
        json={"column_ids": [columns["To do"], staging, columns["Done"]]},
    )
    return str(person)


async def _agent(client: AsyncClient) -> str:
    """The agent's id. Every project has it on, so it can own an ATL card."""
    people = (await client.get("/people")).json()
    return str(next(person["id"] for person in people if person["is_agent"]))


async def _add_column(client: AsyncClient, name: str, project: str = "ATL") -> str:
    response = await client.post(
        f"/projects/{project}/columns", json={"name": name, "description": f"{name}."}
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def _columns(client: AsyncClient) -> dict[str, str]:
    body = (await client.get("/projects/ATL/columns")).json()
    return {column["name"]: column["id"] for column in body["columns"]}


async def _template(client: AsyncClient, name: str = "Hotfix") -> str:
    response = await client.post("/projects/ATL/templates", json={"name": name, "stages": []})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def _task(client: AsyncClient, person: str, **extra: object) -> str:
    response = await client.post(
        "/projects/ATL/tasks",
        json={
            "title": "Stripe webhook idempotency",
            "description": "Duplicate deliveries create double payments.",
            "type": "bug",
            "assignee_id": person,
            **extra,
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["reference"])


async def _move(client: AsyncClient, reference: str, column_id: str, position: int = 0) -> None:
    response = await client.post(
        f"/tasks/{reference}/move", json={"column_id": column_id, "position": position}
    )
    assert response.status_code == 200, response.text


async def _comment(client: AsyncClient, reference: str, body: str) -> None:
    response = await client.post(f"/tasks/{reference}/comments", json={"body": body})
    assert response.status_code == 201, response.text


async def _hook(client: AsyncClient, **fields: object) -> dict[str, Any]:
    response = await client.post(
        "/projects/ATL/hooks", json={"name": "Hook", "url": RECEIVER, **fields}
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def _database(client: AsyncClient) -> Database:
    return client.app.state.database  # type: ignore[attr-defined,no-any-return]


async def _queued(client: AsyncClient, hook_id: str | None = None) -> list[HookDelivery]:
    async with _database(client).session() as session:
        query = select(HookDelivery).order_by(HookDelivery.created_at, HookDelivery.id)
        if hook_id is not None:
            query = query.where(HookDelivery.hook_id == hook_id)
        return list(await session.scalars(query))


class Receiver:
    """Answers in a hook receiver's place, and remembers what it was sent."""

    def __init__(self, *answers: int) -> None:
        self.answers = list(answers) or [200]
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return httpx.Response(status, text="" if status < 300 else "receiver says no")

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))

    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(request.content) for request in self.requests]


async def _deliver(
    client: AsyncClient, receiver: Receiver, *, after: timedelta | None = None
) -> int:
    at = now() + after if after is not None else None
    async with receiver.client() as http:
        return await hook_delivery.deliver_due(_database(client), http, VAULT_KEY, at=at)


# --- The catalogue and the rules -------------------------------------------


class TestRules:
    async def test_the_events_a_hook_can_name_are_listed(self, signed_in: AsyncClient) -> None:
        events = (await signed_in.get("/hooks/events")).json()
        verbs = [event["verb"] for event in events]
        assert "task.moved" in verbs
        assert "task.created" in verbs
        assert "vault.secret_revealed" not in verbs
        moved = next(event for event in events if event["verb"] == "task.moved")
        assert moved["category"] == "moves"
        assert moved["category_name"] == "Card moves"
        # Grouped: every category's events are listed together.
        categories = [event["category"] for event in events]
        assert categories == sorted(categories, key=categories.index)
        assert len(set(verbs)) == len(verbs) == 24

    async def test_a_new_hook_shows_its_secret_once(self, signed_in: AsyncClient) -> None:
        await _project(signed_in)
        created = await _hook(signed_in, verbs=["task.moved"])
        assert created["secret"].startswith("whsec_")
        assert created["secret_hint"] == created["secret"][-4:]

        listed = (await signed_in.get("/projects/ATL/hooks")).json()
        assert [hook["name"] for hook in listed] == ["Hook"]
        assert "secret" not in listed[0]
        assert listed[0]["last_delivery"] is None

    async def test_a_hook_names_columns_and_templates_back(self, signed_in: AsyncClient) -> None:
        await _project(signed_in)
        columns = await _columns(signed_in)
        hotfix = await _template(signed_in)
        created = await _hook(
            signed_in,
            verbs=["task.moved"],
            to_column_id=columns["In staging"],
            template_id=hotfix,
        )
        assert created["to_column_name"] == "In staging"
        assert created["template_name"] == "Hotfix"

    @pytest.mark.parametrize(
        ("fields", "status"),
        [
            ({"verbs": ["vault.secret_revealed"]}, 422),
            ({"url": "ftp://ci.example.com/"}, 422),
            ({"url": "/relative"}, 422),
            # Each fine by ``urlsplit`` or not, but none a URL the client
            # could ever send to.
            ({"url": "http://ci.example.com:deploy/"}, 422),
            ({"url": "http://[::1/hook"}, 422),
            ({"secret": "short"}, 422),
        ],
    )
    async def test_a_hook_that_cannot_work_is_refused(
        self, signed_in: AsyncClient, fields: dict[str, object], status: int
    ) -> None:
        await _project(signed_in)
        response = await signed_in.post(
            "/projects/ATL/hooks", json={"name": "Hook", "url": RECEIVER, **fields}
        )
        assert response.status_code == status, response.text

    async def test_a_column_from_another_board_is_refused(self, signed_in: AsyncClient) -> None:
        await _project(signed_in)
        await signed_in.post("/projects", json=HERMES)
        elsewhere = await _add_column(signed_in, "Review", project="HRM")
        response = await signed_in.post(
            "/projects/ATL/hooks",
            json={"name": "Hook", "url": RECEIVER, "to_column_id": elsewhere},
        )
        assert response.status_code == 422, response.text

    async def test_two_hooks_cannot_share_a_name(self, signed_in: AsyncClient) -> None:
        await _project(signed_in)
        await _hook(signed_in)
        response = await signed_in.post(
            "/projects/ATL/hooks", json={"name": "Hook", "url": RECEIVER}
        )
        assert response.status_code == 409, response.text

    async def test_a_filter_sent_as_null_is_cleared(self, signed_in: AsyncClient) -> None:
        await _project(signed_in)
        columns = await _columns(signed_in)
        hook = await _hook(signed_in, to_column_id=columns["Done"], task_type="bug")

        response = await signed_in.patch(
            f"/projects/ATL/hooks/{hook['id']}", json={"to_column_id": None, "enabled": False}
        )
        assert response.status_code == 200, response.text
        updated = response.json()
        assert updated["to_column_id"] is None
        assert updated["task_type"] == "bug"
        assert updated["enabled"] is False

    async def test_only_the_projects_admin_sees_its_hooks(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await _project(signed_in)
        await _hook(signed_in)
        member, token = await open_account(signed_in, {**TEAM, "name": "Ravi M"}, "ravi@cylist.dev")
        accepted = await other_client.post(
            "/auth/accept-invite", json={"token": token, "password": INVITEE_PASSWORD}
        )
        assert accepted.status_code == 200, accepted.text
        members = (await signed_in.get("/projects/ATL/members")).json()["members"]
        await put_members(signed_in, "ATL", [m["id"] for m in members] + [member["id"]])

        assert (await other_client.get("/projects/ATL/hooks")).status_code == 403
        refused = await other_client.post(
            "/projects/ATL/hooks", json={"name": "Mine", "url": RECEIVER}
        )
        assert refused.status_code == 403


# --- What fires ------------------------------------------------------------


class TestMatching:
    async def test_a_hotfix_moved_into_staging_fires_exactly_once(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _project(signed_in)
        columns = await _columns(signed_in)
        hotfix = await _template(signed_in)
        hook = await _hook(
            signed_in,
            verbs=["task.moved"],
            to_column_id=columns["In staging"],
            template_id=hotfix,
        )
        card = await _task(signed_in, person, template_id=hotfix)

        await _move(signed_in, card, columns["In staging"])

        queued = await _queued(signed_in, hook["id"])
        assert len(queued) == 1
        body = queued[0].payload
        assert body["event"] == "task.moved"
        assert body["task"]["reference"] == card
        assert body["task"]["template"]["name"] == "Hotfix"
        assert body["from_column"]["name"] == "To do"
        assert body["to_column"] == {"id": columns["In staging"], "name": "In staging"}
        assert body["project"]["key"] == "ATL"
        assert body["summary"] == "Moved from To do to In staging."

    async def test_a_move_by_an_agent_over_mcp_fires_the_same_way(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _project(signed_in)
        columns = await _columns(signed_in)
        hotfix = await _template(signed_in)
        hook = await _hook(
            signed_in,
            verbs=["task.moved"],
            to_column_id=columns["In staging"],
            template_id=hotfix,
        )
        card = await _task(signed_in, person, template_id=hotfix)
        token = (
            await signed_in.post("/tokens", json={"name": "Agent", "scopes": ["read", "write"]})
        ).json()["token"]
        app = signed_in.app  # type: ignore[attr-defined]

        async with (
            app.state.mcp.run(),
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                headers={"Authorization": f"Bearer {token}"},
            ) as http,
            Client(streamable_http_client(MCP_URL, http_client=http), mode="legacy") as mcp,
        ):
            moved = await mcp.call_tool("move_task", {"task": card, "column": "In staging"})
        assert not moved.is_error, moved.content

        [delivery] = await _queued(signed_in, hook["id"])
        assert delivery.payload["actor"]["channel"] == "api"
        assert delivery.payload["to_column"]["name"] == "In staging"

    async def test_it_does_not_fire_for_a_card_of_another_template(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _project(signed_in)
        columns = await _columns(signed_in)
        hotfix = await _template(signed_in)
        await _hook(
            signed_in, verbs=["task.moved"], to_column_id=columns["In staging"], template_id=hotfix
        )
        await _move(signed_in, await _task(signed_in, person), columns["In staging"])
        design = await _template(signed_in, "Design")
        await _move(
            signed_in, await _task(signed_in, person, template_id=design), columns["In staging"]
        )
        assert await _queued(signed_in) == []

    async def test_it_does_not_fire_for_another_column(self, signed_in: AsyncClient) -> None:
        person = await _project(signed_in)
        columns = await _columns(signed_in)
        hotfix = await _template(signed_in)
        await _hook(
            signed_in, verbs=["task.moved"], to_column_id=columns["In staging"], template_id=hotfix
        )
        await _move(signed_in, await _task(signed_in, person, template_id=hotfix), columns["Done"])
        assert await _queued(signed_in) == []

    async def test_a_card_reordered_within_its_column_has_not_arrived_again(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _project(signed_in)
        columns = await _columns(signed_in)
        hook = await _hook(signed_in, verbs=["task.moved"], to_column_id=columns["In staging"])
        first = await _task(signed_in, person)
        second = await _task(signed_in, person)
        await _move(signed_in, first, columns["In staging"])
        await _move(signed_in, second, columns["In staging"])
        await _move(signed_in, second, columns["In staging"], position=1)
        assert len(await _queued(signed_in, hook["id"])) == 2

    async def test_any_card_moved_to_done_fires(self, signed_in: AsyncClient) -> None:
        person = await _project(signed_in)
        columns = await _columns(signed_in)
        hook = await _hook(signed_in, verbs=["task.moved"], to_column_id=columns["Done"])
        hotfix = await _template(signed_in)
        for extra in ({}, {"template_id": hotfix}, {"type": "feature"}):
            await _move(signed_in, await _task(signed_in, person, **extra), columns["Done"])
        queued = await _queued(signed_in, hook["id"])
        assert len(queued) == 3
        assert all(delivery.payload["to_column"]["name"] == "Done" for delivery in queued)

    async def test_a_move_out_of_a_column_is_matched_by_from(self, signed_in: AsyncClient) -> None:
        person = await _project(signed_in)
        columns = await _columns(signed_in)
        hook = await _hook(signed_in, verbs=["task.moved"], from_column_id=columns["In staging"])
        card = await _task(signed_in, person)
        await _move(signed_in, card, columns["In staging"])
        assert await _queued(signed_in, hook["id"]) == []
        await _move(signed_in, card, columns["Done"])
        assert len(await _queued(signed_in, hook["id"])) == 1

    async def test_no_events_named_means_every_change(self, signed_in: AsyncClient) -> None:
        person = await _project(signed_in)
        hook = await _hook(signed_in)
        card = await _task(signed_in, person)
        await signed_in.post(f"/tasks/{card}/comments", json={"body": "On it."})
        await _add_column(signed_in, "Review")
        events = [delivery.event for delivery in await _queued(signed_in, hook["id"])]
        assert events == ["task.created", "task.commented", "column.created"]

    async def test_a_filter_on_the_card_ignores_changes_to_no_card(
        self, signed_in: AsyncClient
    ) -> None:
        await _project(signed_in)
        await _hook(signed_in, task_type="bug")
        await _add_column(signed_in, "Review")
        assert await _queued(signed_in) == []

    async def test_an_agent_only_hook_fires_only_for_the_agents_cards(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _project(signed_in)
        agent = await _agent(signed_in)
        columns = await _columns(signed_in)
        hook = await _hook(signed_in, verbs=["task.moved"], agent_only=True)
        assert hook["agent_only"] is True

        await _move(signed_in, await _task(signed_in, person), columns["Done"])
        assert await _queued(signed_in, hook["id"]) == []

        mine = await _task(signed_in, agent)
        await _move(signed_in, mine, columns["In staging"])
        [delivery] = await _queued(signed_in, hook["id"])
        assert delivery.payload["task"]["reference"] == mine
        assert delivery.payload["task"]["assignee"]["id"] == agent

    async def test_handing_a_card_to_the_agent_fires_and_taking_it_back_does_not(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _project(signed_in)
        agent = await _agent(signed_in)
        hook = await _hook(signed_in, verbs=["task.updated"], agent_only=True)
        card = await _task(signed_in, person)

        handed = await signed_in.patch(f"/tasks/{card}", json={"assignee_id": agent})
        assert handed.status_code == 200, handed.text
        assert len(await _queued(signed_in, hook["id"])) == 1

        taken = await signed_in.patch(f"/tasks/{card}", json={"assignee_id": person})
        assert taken.status_code == 200, taken.text
        assert len(await _queued(signed_in, hook["id"])) == 1

    async def test_agent_only_is_off_unless_asked_and_null_turns_it_off(
        self, signed_in: AsyncClient
    ) -> None:
        await _project(signed_in)
        assert (await _hook(signed_in))["agent_only"] is False
        hook = await _hook(signed_in, name="Agent work", agent_only=True)

        cleared = await signed_in.patch(
            f"/projects/ATL/hooks/{hook['id']}", json={"agent_only": None}
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["agent_only"] is False

        left = await signed_in.patch(f"/projects/ATL/hooks/{hook['id']}", json={"enabled": False})
        assert left.json()["agent_only"] is False

    async def test_a_mentions_only_hook_fires_only_for_a_comment_that_tags_somebody(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _project(signed_in)
        hook = await _hook(signed_in, mentions_only=True)
        assert hook["mentions_only"] is True
        card = await _task(signed_in, person)

        await _comment(signed_in, card, "Nobody tagged; mail deploy@example.com.")
        await _comment(signed_in, card, "@Aditi Kumar is somebody else.")
        await _move(signed_in, card, (await _columns(signed_in))["Done"])
        assert await _queued(signed_in, hook["id"]) == []

        await _comment(signed_in, card, "@aditi k could you look?")
        [delivery] = await _queued(signed_in, hook["id"])
        assert delivery.event == "task.commented"
        assert delivery.payload["detail"]["mentioned"] == [{"id": person, "name": "Aditi K"}]

    async def test_a_hook_on_one_person_fires_only_when_they_are_tagged(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _project(signed_in)
        agent = await _agent(signed_in)
        agent_name = next(
            member["name"]
            for member in (await signed_in.get("/projects/ATL/members")).json()["members"]
            if member["id"] == agent
        )
        hook = await _hook(signed_in, mentioned_person_id=agent)
        assert hook["mentioned_person_name"] == agent_name
        card = await _task(signed_in, person)

        await _comment(signed_in, card, "@Aditi K over to you.")
        assert await _queued(signed_in, hook["id"]) == []

        await _comment(signed_in, card, f"@Aditi K and @{agent_name}, please pair on it.")
        [delivery] = await _queued(signed_in, hook["id"])
        assert {tagged["id"] for tagged in delivery.payload["detail"]["mentioned"]} == {
            person,
            agent,
        }

    async def test_a_hook_cannot_wait_on_somebody_off_the_project(
        self, signed_in: AsyncClient
    ) -> None:
        await _project(signed_in)
        stranger = (await signed_in.post("/people", json={**TEAM, "name": "Ravi S"})).json()["id"]
        response = await signed_in.post(
            "/projects/ATL/hooks",
            json={"name": "Hook", "url": RECEIVER, "mentioned_person_id": stranger},
        )
        assert response.status_code == 422, response.text

    async def test_mention_filters_are_off_unless_asked_and_null_clears_them(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _project(signed_in)
        plain = await _hook(signed_in)
        assert plain["mentions_only"] is False
        assert plain["mentioned_person_id"] is None
        hook = await _hook(signed_in, name="Tagged", mentions_only=True, mentioned_person_id=person)

        cleared = await signed_in.patch(
            f"/projects/ATL/hooks/{hook['id']}",
            json={"mentions_only": None, "mentioned_person_id": None},
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["mentions_only"] is False
        assert cleared.json()["mentioned_person_id"] is None

    async def test_a_disabled_hook_queues_nothing(self, signed_in: AsyncClient) -> None:
        person = await _project(signed_in)
        await _hook(signed_in, enabled=False)
        await _task(signed_in, person)
        assert await _queued(signed_in) == []

    async def test_a_filter_on_a_deleted_column_matches_nothing(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _project(signed_in)
        review = await _add_column(signed_in, "Review")
        hook = await _hook(signed_in, verbs=["task.moved"], to_column_id=review)
        deleted = await signed_in.delete(f"/columns/{review}")
        assert deleted.status_code in (200, 204), deleted.text

        columns = await _columns(signed_in)
        await _move(signed_in, await _task(signed_in, person), columns["Done"])
        assert await _queued(signed_in) == []
        read = (await signed_in.get(f"/projects/ATL/hooks/{hook['id']}")).json()
        assert read["to_column_name"] is None

    async def test_a_change_that_rolls_back_queues_nothing(self, signed_in: AsyncClient) -> None:
        person = await _project(signed_in)
        await _hook(signed_in, verbs=["task.commented"])
        card = await _task(signed_in, person)
        database = _database(signed_in)
        principal = Principal(
            token_id=None,
            person_id=None,
            label="test",
            scopes=frozenset(),
            channel=Channel.API,
        )

        async def comment(session: Any) -> None:
            task = await session.scalar(select(Task).where(Task.number == int(card.split("-")[1])))
            await activity.record(
                session,
                principal,
                "task.commented",
                entity_type="task",
                entity_id=task.id,
                project_id=task.project_id,
                payload={"reference": card, "comment": "Rolled back."},
            )

        with pytest.raises(RuntimeError):
            async with database.session() as session:
                await comment(session)
                raise RuntimeError("the request failed after the change was recorded")
        assert await _queued(signed_in) == []

        # And the same change, committed, is what the test would have caught.
        async with database.session() as session:
            await comment(session)
        assert len(await _queued(signed_in)) == 1


# --- Getting it there ------------------------------------------------------


class TestDelivery:
    async def test_a_delivery_is_signed_with_the_hooks_secret(self, signed_in: AsyncClient) -> None:
        person = await _project(signed_in)
        hook = await _hook(signed_in, verbs=["task.created"])
        card = await _task(signed_in, person)
        receiver = Receiver(200)

        assert await _deliver(signed_in, receiver) == 1

        [request] = receiver.requests
        assert str(request.url) == RECEIVER
        assert request.headers["X-Cylist-Event"] == "task.created"
        signature = request.headers[hook_delivery.SIGNATURE_HEADER]
        assert hook_delivery.verify(hook["secret"], signature, request.content)
        assert not hook_delivery.verify("whsec_somebody-elses-secret", signature, request.content)
        assert not hook_delivery.verify(hook["secret"], signature, request.content + b" ")
        assert receiver.bodies()[0]["task"]["reference"] == card

        [delivery] = await _queued(signed_in)
        assert delivery.state == "delivered"
        assert request.headers["X-Cylist-Delivery"] == str(delivery.id)
        assert delivery.attempt_count == 1
        assert await _deliver(signed_in, Receiver(200)) == 0

    async def test_an_old_signature_does_not_verify(self) -> None:
        body = b'{"event":"task.moved"}'
        signed = hook_delivery.sign("whsec_0123456789abcdef", 1_000_000, body)
        assert hook_delivery.verify("whsec_0123456789abcdef", signed, body, at=1_000_100)
        assert not hook_delivery.verify("whsec_0123456789abcdef", signed, body, at=1_001_000)

    async def test_a_receiver_that_is_down_gets_it_on_a_retry(self, signed_in: AsyncClient) -> None:
        person = await _project(signed_in)
        hook = await _hook(signed_in, verbs=["task.created"])
        await _task(signed_in, person)

        assert await _deliver(signed_in, Receiver(503)) == 1
        [delivery] = await _queued(signed_in)
        assert delivery.state == "pending"
        assert delivery.last_error == "HTTP 503: receiver says no"

        # Not before its time, and then once it has come.
        assert await _deliver(signed_in, Receiver(200)) == 0
        assert await _deliver(signed_in, Receiver(200), after=timedelta(seconds=37)) == 1

        log = (await signed_in.get(f"/projects/ATL/hooks/{hook['id']}/deliveries")).json()
        assert [entry["state"] for entry in log] == ["delivered"]
        assert [attempt["status_code"] for attempt in log[0]["attempts"]] == [503, 200]
        listed = (await signed_in.get("/projects/ATL/hooks")).json()
        assert listed[0]["last_delivery"]["state"] == "delivered"

    async def test_a_network_error_is_retried_too(self, signed_in: AsyncClient) -> None:
        person = await _project(signed_in)
        await _hook(signed_in, verbs=["task.created"])
        await _task(signed_in, person)

        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as http:
            await hook_delivery.deliver_due(_database(signed_in), http, VAULT_KEY)
        [delivery] = await _queued(signed_in)
        assert delivery.state == "pending"
        assert delivery.last_status_code is None
        assert delivery.last_error is not None
        assert "ConnectError" in delivery.last_error

    async def test_it_gives_up_after_the_last_attempt_and_can_be_sent_again(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _project(signed_in)
        hook = await _hook(signed_in, verbs=["task.created"])
        await _task(signed_in, person)

        for _ in range(hook_delivery.MAX_ATTEMPTS):
            assert await _deliver(signed_in, Receiver(500), after=timedelta(days=1)) == 1
        [delivery] = await _queued(signed_in)
        assert delivery.state == "failed"
        assert delivery.attempt_count == hook_delivery.MAX_ATTEMPTS
        assert await _deliver(signed_in, Receiver(200), after=timedelta(days=2)) == 0

        response = await signed_in.post(
            f"/projects/ATL/hooks/{hook['id']}/deliveries/{delivery.id}/redeliver"
        )
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "pending"
        # Seven refusals in a row have opened the hook's breaker too, so it is
        # sent once that has closed — and the success closes it.
        assert await _deliver(signed_in, Receiver(200)) == 0
        assert await _deliver(signed_in, Receiver(200), after=timedelta(days=2)) == 1
        [delivery] = await _queued(signed_in)
        assert delivery.state == "delivered"

    async def test_a_deleted_hook_sends_nothing_more(self, signed_in: AsyncClient) -> None:
        person = await _project(signed_in)
        hook = await _hook(signed_in, verbs=["task.created"])
        await _task(signed_in, person)
        deleted = await signed_in.delete(f"/projects/ATL/hooks/{hook['id']}")
        assert deleted.status_code == 204
        assert await _deliver(signed_in, Receiver(200)) == 0

    async def test_a_rotated_secret_signs_from_then_on(self, signed_in: AsyncClient) -> None:
        person = await _project(signed_in)
        hook = await _hook(signed_in, verbs=["task.created"])
        rotated = (await signed_in.post(f"/projects/ATL/hooks/{hook['id']}/secret", json={})).json()
        assert rotated["secret"] != hook["secret"]
        await _task(signed_in, person)
        receiver = Receiver(200)
        await _deliver(signed_in, receiver)
        [request] = receiver.requests
        signature = request.headers[hook_delivery.SIGNATURE_HEADER]
        assert hook_delivery.verify(rotated["secret"], signature, request.content)
        assert not hook_delivery.verify(hook["secret"], signature, request.content)

    async def test_the_courier_sends_what_is_queued_when_woken(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _project(signed_in)
        await _hook(signed_in, verbs=["task.created"])
        await _task(signed_in, person)
        receiver = Receiver(200)
        courier = hook_delivery.Courier()
        running = asyncio.create_task(
            courier.run(_database(signed_in), VAULT_KEY, httpx.MockTransport(receiver))
        )
        try:
            for _ in range(100):
                if receiver.requests:
                    break
                await asyncio.sleep(0.02)
            await _task(signed_in, person)
            courier.wake()
            for _ in range(100):
                if len(receiver.requests) == 2:
                    break
                await asyncio.sleep(0.02)
        finally:
            running.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await running
        assert len(receiver.requests) == 2
        assert {delivery.state for delivery in await _queued(signed_in)} == {"delivered"}


# --- One bad receiver -------------------------------------------------------


SLOW = "https://slow.example.com/cylist"


async def _hook_row(client: AsyncClient, hook_id: str) -> Hook:
    async with _database(client).session() as session:
        hook = await session.get(Hook, UUID(hook_id))
        assert hook is not None
        return hook


class TestOneBadReceiver:
    """Whatever one receiver does — hang, flood, refuse for hours, or sit
    behind a URL that cannot be sent to — every other hook's deliveries still
    go, and an operator can see it happening."""

    async def test_a_receiver_that_trickles_is_cut_off_in_total(
        self, signed_in: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        person = await _project(signed_in)
        await _hook(signed_in, verbs=["task.created"])
        await _task(signed_in, person)
        monkeypatch.setattr(hook_delivery, "TOTAL_TIMEOUT", timedelta(seconds=0.3))

        async def drip() -> AsyncIterator[bytes]:
            # A byte well inside the per-read timeout, for ever.
            while True:
                await asyncio.sleep(0.02)
                yield b"."

        async def trickle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=drip())

        async with httpx.AsyncClient(transport=httpx.MockTransport(trickle)) as http:
            sent = await asyncio.wait_for(
                hook_delivery.deliver_due(_database(signed_in), http, VAULT_KEY), 5
            )
        assert sent == 1
        [delivery] = await _queued(signed_in)
        assert delivery.state == "pending"
        assert delivery.last_status_code is None
        assert delivery.last_error == "TimeoutError: no answer within 0.3 seconds"

    async def test_no_more_than_the_cap_of_an_answer_is_read(self, signed_in: AsyncClient) -> None:
        person = await _project(signed_in)
        await _hook(signed_in, verbs=["task.created"])
        await _task(signed_in, person)
        pulled = 0

        async def flood() -> AsyncIterator[bytes]:
            nonlocal pulled
            while True:
                pulled += 1
                yield b"x" * 4096

        class Flood(httpx.AsyncBaseTransport):
            """Streams its answer as it is read, as the network does —
            ``MockTransport`` reads the whole of it first."""

            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                assert request.headers["Accept-Encoding"] == "identity"
                return httpx.Response(500, content=flood())

        async with httpx.AsyncClient(transport=Flood()) as http:
            sent = await asyncio.wait_for(
                hook_delivery.deliver_due(_database(signed_in), http, VAULT_KEY), 5
            )
        assert sent == 1
        assert pulled * 4096 <= hook_delivery.RESPONSE_MAX_BYTES + 4096
        [delivery] = await _queued(signed_in)
        assert delivery.last_status_code == 500
        assert delivery.last_error is not None
        assert delivery.last_error.startswith("HTTP 500: xxx")
        assert len(delivery.last_error) == hook_delivery.ERROR_MAX_LENGTH

    async def test_a_url_that_cannot_be_sent_to_fails_alone(self, signed_in: AsyncClient) -> None:
        person = await _project(signed_in)
        broken = await _hook(signed_in, name="Broken", verbs=["task.created"])
        await _hook(signed_in, name="Fine", verbs=["task.created"])
        # Saved before URLs were read the way the client reads them.
        async with _database(signed_in).session() as session:
            await session.execute(
                update(Hook)
                .where(Hook.id == UUID(broken["id"]))
                .values(url="http://ci.example.com:deploy/")
            )
        await _task(signed_in, person)
        receiver = Receiver(200)

        assert await _deliver(signed_in, receiver) == 2

        assert len(receiver.requests) == 1
        [bad] = await _queued(signed_in, broken["id"])
        assert bad.state == "pending"
        assert bad.attempt_count == 1
        assert bad.last_error is not None
        assert bad.last_error.startswith("InvalidURL")
        states = {delivery.state for delivery in await _queued(signed_in)}
        assert states == {"pending", "delivered"}
        # Recorded, so not sent again until its retry is due.
        assert await _deliver(signed_in, Receiver(200)) == 0

    async def test_anything_a_send_raises_is_a_failed_attempt(
        self, signed_in: AsyncClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        person = await _project(signed_in)
        await _hook(signed_in, name="Buggy", url=SLOW, verbs=["task.created"])
        await _hook(signed_in, name="Fine", verbs=["task.created"])
        await _task(signed_in, person)

        def answer(request: httpx.Request) -> httpx.Response:
            if str(request.url) == SLOW:
                raise RuntimeError("something nobody planned for")
            return httpx.Response(200)

        with caplog.at_level(logging.ERROR, logger=hook_delivery.__name__):
            async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
                assert await hook_delivery.deliver_due(_database(signed_in), http, VAULT_KEY) == 2
        errors = {delivery.last_error for delivery in await _queued(signed_in)}
        assert errors == {None, "RuntimeError: something nobody planned for"}
        assert "failed unexpectedly" in caplog.text

    async def test_a_pass_takes_only_a_few_of_one_hooks_rows(self, signed_in: AsyncClient) -> None:
        person = await _project(signed_in)
        busy = await _hook(signed_in, name="Busy", url=SLOW, verbs=["task.created"])
        await _task(signed_in, person)
        await _hook(signed_in, name="Quiet", verbs=["task.created"])
        for _ in range(hook_delivery.PER_HOOK + 2):
            await _task(signed_in, person)
        receiver = Receiver(200)

        # Busy has seven waiting and Quiet six, yet each gets its share.
        assert await _deliver(signed_in, receiver) == 2 * hook_delivery.PER_HOOK
        hosts = [request.url.host for request in receiver.requests]
        assert hosts.count("slow.example.com") == hook_delivery.PER_HOOK
        assert await _deliver(signed_in, receiver) == 3 + 2
        assert len(await _queued(signed_in, busy["id"])) == hook_delivery.PER_HOOK + 3

    async def test_a_slow_receiver_does_not_hold_up_the_others(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _project(signed_in)
        await _hook(signed_in, name="Slow", url=SLOW, verbs=["task.created"])
        fast = await _hook(signed_in, name="Fast", verbs=["task.created"])
        released = asyncio.Event()
        answered: list[str] = []

        async def answer(request: httpx.Request) -> httpx.Response:
            if str(request.url) == SLOW:
                await released.wait()
            answered.append(str(request.url))
            return httpx.Response(200)

        courier = hook_delivery.Courier()
        running = asyncio.create_task(
            courier.run(_database(signed_in), VAULT_KEY, httpx.MockTransport(answer))
        )
        try:
            # Each card queues one for each hook. Slow never answers; Fast's
            # keep going out as they are queued, not after Slow's.
            for count in range(1, 4):
                await _task(signed_in, person)
                courier.wake()
                for _ in range(100):
                    if answered.count(RECEIVER) == count:
                        break
                    await asyncio.sleep(0.02)
                assert answered.count(RECEIVER) == count
            assert SLOW not in answered
            fast_states = {d.state for d in await _queued(signed_in, fast["id"])}
            assert fast_states == {"delivered"}

            released.set()
            for _ in range(100):
                if answered.count(SLOW) == 3:
                    break
                courier.wake()
                await asyncio.sleep(0.02)
            assert answered.count(SLOW) == 3
        finally:
            running.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await running

    async def test_retries_are_spread_around_their_step(self) -> None:
        moments = set()
        for _ in range(50):
            delivery = HookDelivery(
                hook_id=uuid4(), event="task.created", payload={}, attempts=[], attempt_count=0
            )
            attempt = hook_delivery.Attempt(
                at=now(), status_code=503, error="HTTP 503", duration_ms=1
            )
            hook_delivery.record(delivery, attempt)
            assert delivery.next_attempt_at is not None
            wait = delivery.next_attempt_at - attempt.at
            assert timedelta(seconds=24) <= wait <= timedelta(seconds=36)
            moments.add(wait)
        assert len(moments) > 1

    async def test_a_delivery_that_gives_up_says_so_in_the_log(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        hook_id = uuid4()
        delivery = HookDelivery(
            id=uuid4(),
            hook_id=hook_id,
            event="task.moved",
            payload={},
            attempts=[],
            attempt_count=hook_delivery.MAX_ATTEMPTS - 1,
        )
        attempt = hook_delivery.Attempt(at=now(), status_code=502, error="HTTP 502", duration_ms=1)
        with caplog.at_level(logging.WARNING, logger=hook_delivery.__name__):
            hook_delivery.record(delivery, attempt)
        assert delivery.state == "failed"
        [entry] = caplog.records
        assert entry.levelno == logging.WARNING
        message = entry.getMessage()
        assert str(hook_id) in message
        assert "task.moved" in message
        assert f"attempt {hook_delivery.MAX_ATTEMPTS}" in message

    async def test_a_receiver_that_keeps_failing_is_paused_then_probed(
        self, signed_in: AsyncClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        person = await _project(signed_in)
        hook = await _hook(signed_in, verbs=["task.created"])
        for _ in range(hook_delivery.BREAKER_THRESHOLD):
            await _task(signed_in, person)

        # Four, then the fifth: five refusals in a row open the breaker.
        assert await _deliver(signed_in, Receiver(500)) == hook_delivery.PER_HOOK
        assert (await _hook_row(signed_in, hook["id"])).paused_until is None
        with caplog.at_level(logging.WARNING, logger=hook_delivery.__name__):
            assert await _deliver(signed_in, Receiver(500)) == 1
        paused = await _hook_row(signed_in, hook["id"])
        assert paused.consecutive_failures == hook_delivery.BREAKER_THRESHOLD
        assert paused.paused_until is not None
        # Its receiver failing is not somebody editing the hook.
        assert paused.updated_at == paused.created_at
        assert f"Hook {hook['id']} has failed 5 attempts in a row" in caplog.text

        # Every retry has come due, but nothing goes while the breaker is open,
        # and the wait spends none of their attempts.
        receiver = Receiver(200)
        assert await _deliver(signed_in, receiver, after=timedelta(seconds=40)) == 0
        assert receiver.requests == []
        assert {d.attempt_count for d in await _queued(signed_in)} == {1}

        # Then one goes alone. Refused, the breaker opens again for longer.
        assert await _deliver(signed_in, Receiver(500), after=timedelta(minutes=2)) == 1
        assert await _deliver(signed_in, receiver, after=timedelta(minutes=1)) == 0
        reopened = await _hook_row(signed_in, hook["id"])
        assert reopened.paused_until is not None
        assert reopened.paused_until - now() > timedelta(minutes=1, seconds=50)

        # Answered, it closes, and the rest follow.
        assert await _deliver(signed_in, receiver, after=timedelta(minutes=5)) == 1
        closed = await _hook_row(signed_in, hook["id"])
        assert (closed.consecutive_failures, closed.paused_until) == (0, None)
        assert await _deliver(signed_in, receiver, after=timedelta(minutes=5)) == 4
        assert {d.state for d in await _queued(signed_in)} == {"delivered"}

    async def test_a_new_url_starts_with_its_breaker_closed(self, signed_in: AsyncClient) -> None:
        await _project(signed_in)
        hook = await _hook(signed_in)
        async with _database(signed_in).session() as session:
            await session.execute(
                update(Hook)
                .where(Hook.id == UUID(hook["id"]))
                .values(consecutive_failures=9, paused_until=now() + timedelta(hours=1))
            )
        response = await signed_in.patch(
            f"/projects/ATL/hooks/{hook['id']}", json={"url": "https://ci.example.com/v2"}
        )
        assert response.status_code == 200, response.text
        moved = await _hook_row(signed_in, hook["id"])
        assert (moved.consecutive_failures, moved.paused_until) == (0, None)

    async def test_a_url_that_cannot_be_sent_to_is_refused_on_change(
        self, signed_in: AsyncClient
    ) -> None:
        await _project(signed_in)
        hook = await _hook(signed_in)
        response = await signed_in.patch(
            f"/projects/ATL/hooks/{hook['id']}", json={"url": "http://ci.example.com:deploy/"}
        )
        assert response.status_code == 422, response.text


class TestSendTest:
    @staticmethod
    def _answering(
        client: AsyncClient, receiver: Callable[[httpx.Request], httpx.Response]
    ) -> None:
        client.app.state.hook_transport = httpx.MockTransport(receiver)  # type: ignore[attr-defined]

    async def test_a_test_send_answers_with_what_came_back(self, signed_in: AsyncClient) -> None:
        await _project(signed_in)
        hook = await _hook(signed_in, enabled=False)
        receiver = Receiver(204)
        self._answering(signed_in, receiver)

        response = await signed_in.post(f"/projects/ATL/hooks/{hook['id']}/test")
        assert response.status_code == 200, response.text
        sent = response.json()
        assert sent["state"] == "delivered"
        assert sent["event"] == "hook.test"
        assert sent["attempts"][0]["status_code"] == 204
        assert receiver.bodies()[0]["project"]["key"] == "ATL"

    async def test_a_refused_test_is_not_retried(self, signed_in: AsyncClient) -> None:
        await _project(signed_in)
        hook = await _hook(signed_in)
        self._answering(signed_in, Receiver(404))

        sent = (await signed_in.post(f"/projects/ATL/hooks/{hook['id']}/test")).json()
        assert sent["state"] == "failed"
        assert sent["last_status_code"] == 404
        assert sent["next_attempt_at"] is None
        async with _database(signed_in).session() as session:
            pending = await session.scalar(
                select(func.count())
                .select_from(HookDelivery)
                .where(HookDelivery.state == "pending")
            )
        assert pending == 0


# --- Fired by hand ---------------------------------------------------------


class TestFiring:
    @staticmethod
    def _answering(client: AsyncClient, receiver: Receiver) -> None:
        client.app.state.hook_transport = httpx.MockTransport(receiver)  # type: ignore[attr-defined]

    async def test_a_manual_hook_hears_nothing_of_the_boards_changes(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _project(signed_in)
        hook = await _hook(signed_in, manual=True)
        assert hook["manual"] is True
        assert hook["verbs"] == []

        card = await _task(signed_in, person)
        await _comment(signed_in, card, "Looks done.")
        await _move(signed_in, card, (await _columns(signed_in))["Done"])
        assert await _queued(signed_in, hook["id"]) == []

    async def test_firing_sends_a_signed_event_about_the_project(
        self, signed_in: AsyncClient
    ) -> None:
        await _project(signed_in)
        hook = await _hook(signed_in, name="Deploy", manual=True)
        receiver = Receiver(202)
        self._answering(signed_in, receiver)

        response = await signed_in.post(
            f"/projects/ATL/hooks/{hook['id']}/fire", json={"message": "  Release 1.4  "}
        )
        assert response.status_code == 200, response.text
        sent = response.json()
        assert sent["event"] == "hook.fired"
        assert sent["state"] == "delivered"
        assert sent["activity_id"] is None

        [request] = receiver.requests
        assert request.headers["X-Cylist-Event"] == "hook.fired"
        signature = request.headers[hook_delivery.SIGNATURE_HEADER]
        assert hook_delivery.verify(hook["secret"], signature, request.content)
        [body] = receiver.bodies()
        assert body["project"]["key"] == "ATL"
        assert body["task"] is None
        assert body["detail"] == {
            "hook": {"id": hook["id"], "name": "Deploy"},
            "message": "Release 1.4",
        }

        listed = (await signed_in.get("/projects/ATL/hooks")).json()
        assert listed[0]["last_delivery"]["event"] == "hook.fired"
        async with _database(signed_in).session() as session:
            fired = await session.scalar(select(Activity).where(Activity.verb == "hook.fired"))
        assert fired is not None
        assert activity.describe(fired) == "Fired the hook “Deploy”."

    async def test_firing_needs_no_message(self, signed_in: AsyncClient) -> None:
        await _project(signed_in)
        hook = await _hook(signed_in, manual=True)
        receiver = Receiver(200)
        self._answering(signed_in, receiver)

        response = await signed_in.post(f"/projects/ATL/hooks/{hook['id']}/fire")
        assert response.status_code == 200, response.text
        assert receiver.bodies()[0]["detail"]["message"] is None

    async def test_a_refused_firing_is_tried_again(self, signed_in: AsyncClient) -> None:
        await _project(signed_in)
        hook = await _hook(signed_in, manual=True)
        self._answering(signed_in, Receiver(503))

        sent = (await signed_in.post(f"/projects/ATL/hooks/{hook['id']}/fire", json={})).json()
        assert sent["state"] == "pending"
        assert sent["next_attempt_at"] is not None

        receiver = Receiver(200)
        assert await _deliver(signed_in, receiver, after=timedelta(seconds=37)) == 1
        assert receiver.bodies()[0]["event"] == "hook.fired"
        [delivery] = await _queued(signed_in, hook["id"])
        assert delivery.state == "delivered"

    async def test_only_a_manual_hook_that_is_on_can_be_fired(self, signed_in: AsyncClient) -> None:
        await _project(signed_in)
        receiver = Receiver(200)
        self._answering(signed_in, receiver)
        on_changes = await _hook(signed_in, name="On changes")
        switched_off = await _hook(signed_in, name="Off", manual=True, enabled=False)

        for hook in (on_changes, switched_off):
            response = await signed_in.post(f"/projects/ATL/hooks/{hook['id']}/fire")
            assert response.status_code == 409, response.text
        assert receiver.requests == []
        assert await _queued(signed_in) == []

    @pytest.mark.parametrize(
        "fields",
        [
            {"verbs": ["task.moved"]},
            {"task_type": "bug"},
            {"agent_only": True},
            {"mentions_only": True},
        ],
    )
    async def test_a_manual_hook_takes_no_events_or_filters(
        self, signed_in: AsyncClient, fields: dict[str, object]
    ) -> None:
        await _project(signed_in)
        response = await signed_in.post(
            "/projects/ATL/hooks",
            json={"name": "Hook", "url": RECEIVER, "manual": True, **fields},
        )
        assert response.status_code == 422, response.text

    async def test_a_hook_can_be_made_manual_and_put_back(self, signed_in: AsyncClient) -> None:
        await _project(signed_in)
        columns = await _columns(signed_in)
        hook = await _hook(signed_in, verbs=["task.moved"], to_column_id=columns["Done"])
        path = f"/projects/ATL/hooks/{hook['id']}"

        # Its rules have to go with it: left behind, they would do nothing.
        kept = await signed_in.patch(path, json={"manual": True})
        assert kept.status_code == 422, kept.text

        made = await signed_in.patch(path, json={"manual": True, "verbs": [], "to_column_id": None})
        assert made.status_code == 200, made.text
        assert made.json()["manual"] is True

        back = await signed_in.patch(path, json={"manual": False, "verbs": ["task.created"]})
        assert back.status_code == 200, back.text
        assert back.json()["manual"] is False
        assert back.json()["verbs"] == ["task.created"]
