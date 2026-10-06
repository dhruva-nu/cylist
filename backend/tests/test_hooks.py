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
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import httpx
import httpx2
import pytest
from httpx import AsyncClient
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import func, select

from app.auth.principal import Principal
from app.core.clock import now
from app.db import Database
from app.models.activity import Channel
from app.models.hook import HookDelivery
from app.models.task import Task
from app.services import activity, hook_delivery
from tests.conftest import INVITEE_PASSWORD, VAULT_KEY, open_account

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
    await client.put(
        "/projects/ATL/members",
        json={"person_ids": [member["id"] for member in members] + [person]},
    )
    staging = await _add_column(client, "In staging")
    columns = await _columns(client)
    await client.put(
        "/projects/ATL/columns/order",
        json={"column_ids": [columns["To do"], staging, columns["Done"]]},
    )
    return str(person)


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
        await signed_in.put(
            "/projects/ATL/members",
            json={"person_ids": [m["id"] for m in members] + [member["id"]]},
        )

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
        assert await _deliver(signed_in, Receiver(200), after=timedelta(seconds=31)) == 1

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
        assert await _deliver(signed_in, Receiver(200)) == 1
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
