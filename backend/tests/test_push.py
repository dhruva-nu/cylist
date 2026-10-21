"""Pushes: an agent session's change, sent to the phones it is news to through FCM v1.

Three layers, each tested on its own terms:

* what a push says and when one is worth sending (pure functions);
* :class:`FcmSender` against a faked Google — the OAuth exchange, the v1
  request and how each answer is read — through an ``httpx.MockTransport``;
* the whole path: hook reports and an ``ask_human`` question through the real
  API, the commit, the outbox, the pusher, and the messages a fake sender is
  handed.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import FastAPI
from httpx import AsyncClient

from app.db import Database
from app.main import _publisher
from app.models import AgentSessionReason, AgentSessionState, Person, SessionKey
from app.services.push import fcm
from app.services.push.delivery import Pusher
from app.services.push.fcm import (
    FcmSender,
    NullSender,
    SendOutcome,
    ServiceAccount,
    ServiceAccountError,
    sender_for,
)
from app.services.push.messages import (
    NOTE_LIMIT,
    CardContext,
    SessionSnapshot,
    data_for,
    message_for,
    worth_pushing,
)
from tests.conftest import INVITEE_PASSWORD, open_account, put_members

ATLAS = {"key": "ATL", "name": "Atlas Billing Migration"}
ADITI = {
    "name": "Aditi K",
    "kind": "team",
    "title": "Backend engineer",
    "responsibilities": "Payments and webhooks.",
}
PHONE_FCM = "phone-install:APA91b-owner-0001"
ADITI_FCM = "phone-install:APA91b-aditi-0002"
SESSION = "sess-push-1"

# --- A service account of our own ------------------------------------------

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KEY_JSON = json.dumps(
    {
        "type": "service_account",
        "project_id": "cylist-test",
        "private_key_id": "k1",
        "private_key": _KEY.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
        "client_email": "pusher@cylist-test.iam.gserviceaccount.com",
        "token_uri": "https://oauth2.example/token",
    }
)


def _snapshot(**changes: Any) -> SessionSnapshot:
    base = SessionSnapshot(
        agent_session_id=uuid4(),
        task_id=uuid4(),
        token_id=uuid4(),
        state=AgentSessionState.WORKING,
        reason=None,
        note=None,
        client_name="ATL-1",
        started_at=datetime(2026, 10, 19, 9, 0, tzinfo=UTC),
        state_changed_at=datetime(2026, 10, 19, 9, 5, tzinfo=UTC),
    )
    return replace(base, **changes)


CARD = CardContext(reference="ATL-1", title="Dedupe webhooks", project_key="ATL")


class TestWorthPushing:
    def test_a_new_session_is(self) -> None:
        assert worth_pushing(None, _snapshot())

    def test_a_change_of_state_is(self) -> None:
        before = _snapshot()
        after = replace(
            before, state=AgentSessionState.WAITING, reason=AgentSessionReason.TURN_ENDED
        )
        assert worth_pushing(before, after)
        assert worth_pushing(after, replace(after, state=AgentSessionState.DONE))

    def test_a_working_session_s_new_note_is_not(self) -> None:
        before = _snapshot(note="Reading a.py")
        assert not worth_pushing(before, replace(before, note="Running pytest"))

    def test_a_heartbeat_is_not(self) -> None:
        before = _snapshot()
        assert not worth_pushing(before, before)

    def test_a_waiting_session_that_starts_asking_is(self) -> None:
        before = _snapshot(state=AgentSessionState.WAITING, reason=AgentSessionReason.TURN_ENDED)
        after = replace(before, reason=AgentSessionReason.QUESTION, note="Sheet 1 or Mar-2026?")
        assert worth_pushing(before, after)

    def test_a_waiting_session_s_new_question_is(self) -> None:
        before = _snapshot(
            state=AgentSessionState.WAITING, reason=AgentSessionReason.QUESTION, note="One?"
        )
        assert worth_pushing(before, replace(before, note="Two?"))


class TestMessage:
    def test_a_question_carries_the_card_and_the_question(self) -> None:
        snapshot = _snapshot(
            state=AgentSessionState.WAITING,
            reason=AgentSessionReason.QUESTION,
            note="Which sheet is the March schedule?",
        )

        message = message_for("device-token", snapshot, CARD)

        assert message["token"] == "device-token"
        assert message["data"] == {
            "kind": "agent_session",
            "session_id": str(snapshot.agent_session_id),
            "task": "ATL-1",
            "task_title": "Dedupe webhooks",
            "project": "ATL",
            "state": "waiting",
            "reason": "question",
            "note": "Which sheet is the March schedule?",
            "client_name": "ATL-1",
            "started_at": "2026-10-19T09:00:00+00:00",
            "state_changed_at": "2026-10-19T09:05:00+00:00",
        }
        assert message["android"] == {
            "priority": "HIGH",
            "ttl": "86400s",
            "collapse_key": str(snapshot.agent_session_id),
        }
        assert "notification" not in message  # the app draws it, so it can update in place

    def test_working_is_short_lived(self) -> None:
        assert message_for("t", _snapshot(), CARD)["android"]["ttl"] == "600s"

    def test_every_value_is_a_string_and_nothing_is_missing(self) -> None:
        data = data_for(_snapshot(client_name=None), CARD)
        assert all(isinstance(value, str) for value in data.values())
        assert data["reason"] == "" and data["note"] == "" and data["client_name"] == ""

    def test_a_long_note_is_clipped_to_fit_fcm(self) -> None:
        data = data_for(_snapshot(note="x" * 5000), CARD)
        assert len(data["note"]) == NOTE_LIMIT
        assert data["note"].endswith("…")


# --- FCM, faked -------------------------------------------------------------


class FakeGoogle:
    """Google's token endpoint and FCM's send endpoint, answering as told."""

    def __init__(self) -> None:
        self.token_calls: list[dict[str, str]] = []
        self.sends: list[httpx.Request] = []
        self.send_answers: list[httpx.Response] = []
        self.token_answer = httpx.Response(
            200, json={"access_token": "ya29.one", "expires_in": 3600}
        )
        self.minted = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url == "https://oauth2.example/token":
            form = dict(httpx.QueryParams(request.content.decode()))
            self.token_calls.append(form)
            self.minted += 1
            if self.token_answer.status_code == 200:
                return httpx.Response(
                    200, json={"access_token": f"ya29.{self.minted}", "expires_in": 3600}
                )
            return self.token_answer
        self.sends.append(request)
        return self.send_answers.pop(0) if self.send_answers else httpx.Response(200, json={})


def _sender(google: FakeGoogle, clock: Callable[[], float] = lambda: 1_800_000_000.0) -> FcmSender:
    return FcmSender(
        ServiceAccount.from_json(KEY_JSON), transport=httpx.MockTransport(google), clock=clock
    )


def _decode(part: str) -> dict[str, Any]:
    return dict(json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))))


class TestFcmSender:
    async def test_mints_a_token_with_a_signed_assertion_and_sends_v1(self) -> None:
        google = FakeGoogle()
        message = message_for("device-token", _snapshot(), CARD)

        outcome = await _sender(google).send(message)

        assert outcome is SendOutcome.SENT
        [exchange] = google.token_calls
        assert exchange["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"
        header, claims, signature = exchange["assertion"].split(".")
        assert _decode(header) == {"alg": "RS256", "typ": "JWT"}
        assert _decode(claims) == {
            "iss": "pusher@cylist-test.iam.gserviceaccount.com",
            "scope": "https://www.googleapis.com/auth/firebase.messaging",
            "aud": "https://oauth2.example/token",
            "iat": 1_800_000_000,
            "exp": 1_800_003_600,
        }
        _KEY.public_key().verify(
            base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)),
            f"{header}.{claims}".encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        [sent] = google.sends
        assert str(sent.url) == "https://fcm.googleapis.com/v1/projects/cylist-test/messages:send"
        assert sent.headers["Authorization"] == "Bearer ya29.1"
        assert json.loads(sent.content) == {"message": message}

    async def test_reuses_the_access_token_until_it_nearly_expires(self) -> None:
        google = FakeGoogle()
        now = [1_800_000_000.0]
        sender = _sender(google, clock=lambda: now[0])

        await sender.send(message_for("a", _snapshot(), CARD))
        now[0] += 3000
        await sender.send(message_for("b", _snapshot(), CARD))
        assert len(google.token_calls) == 1

        now[0] += 400  # inside the refresh margin
        await sender.send(message_for("c", _snapshot(), CARD))
        assert len(google.token_calls) == 2
        assert google.sends[-1].headers["Authorization"] == "Bearer ya29.2"

    async def test_a_refused_access_token_is_minted_again_once(self) -> None:
        google = FakeGoogle()
        google.send_answers = [httpx.Response(401, json={}), httpx.Response(200, json={})]

        outcome = await _sender(google).send(message_for("t", _snapshot(), CARD))

        assert outcome is SendOutcome.SENT
        assert len(google.token_calls) == 2
        assert [request.headers["Authorization"] for request in google.sends] == [
            "Bearer ya29.1",
            "Bearer ya29.2",
        ]

    async def test_an_unregistered_device_says_so(self) -> None:
        google = FakeGoogle()
        google.send_answers = [
            httpx.Response(
                404,
                json={
                    "error": {
                        "status": "NOT_FOUND",
                        "details": [
                            {
                                "@type": "type.googleapis.com/google.firebase.fcm.v1.FcmError",
                                "errorCode": "UNREGISTERED",
                            }
                        ],
                    }
                },
            )
        ]

        assert await _sender(google).send(message_for("t", _snapshot(), CARD)) is (
            SendOutcome.UNREGISTERED
        )

    async def test_a_bad_request_is_a_failure_and_not_a_forgotten_phone(self) -> None:
        google = FakeGoogle()
        google.send_answers = [
            httpx.Response(400, json={"error": {"status": "INVALID_ARGUMENT"}}),
        ]

        assert await _sender(google).send(message_for("t", _snapshot(), CARD)) is (
            SendOutcome.FAILED
        )

    async def test_a_token_endpoint_that_refuses_is_a_failure(self) -> None:
        google = FakeGoogle()
        google.token_answer = httpx.Response(400, json={"error": "invalid_grant"})

        assert await _sender(google).send(message_for("t", _snapshot(), CARD)) is (
            SendOutcome.FAILED
        )
        assert google.sends == []

    async def test_a_network_failure_is_a_failure(self) -> None:
        def unreachable(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route", request=request)

        sender = FcmSender(
            ServiceAccount.from_json(KEY_JSON), transport=httpx.MockTransport(unreachable)
        )

        assert await sender.send(message_for("t", _snapshot(), CARD)) is SendOutcome.FAILED

    async def test_one_client_serves_every_message_until_the_sender_is_closed(self) -> None:
        # A client closes its transport when it closes, so a client per
        # message would close this one after every send.
        closed: list[bool] = []

        class Watched(httpx.MockTransport):
            async def aclose(self) -> None:
                closed.append(True)

        sender = FcmSender(ServiceAccount.from_json(KEY_JSON), transport=Watched(FakeGoogle()))
        for device in ("a", "b", "c"):
            assert await sender.send(message_for(device, _snapshot(), CARD)) is SendOutcome.SENT
        assert closed == []

        await sender.aclose()
        assert closed == [True]


class TestServiceAccount:
    def test_never_shows_its_key(self) -> None:
        account = ServiceAccount.from_json(KEY_JSON)
        assert "PRIVATE" not in repr(account)
        assert repr(account) == (
            "ServiceAccount(project_id='cylist-test', "
            "client_email='pusher@cylist-test.iam.gserviceaccount.com')"
        )

    @pytest.mark.parametrize(
        ("raw", "problem"),
        [
            ("not json", "not JSON"),
            (json.dumps({"type": "authorized_user"}), "not a service-account key"),
            (json.dumps({"type": "service_account", "project_id": "p"}), "lacks client_email"),
            (
                json.dumps(
                    {
                        "type": "service_account",
                        "project_id": "p",
                        "client_email": "e",
                        "token_uri": "u",
                        "private_key": "nope",
                    }
                ),
                "not a PEM key",
            ),
        ],
    )
    def test_a_broken_key_says_what_is_wrong(self, raw: str, problem: str) -> None:
        with pytest.raises(ServiceAccountError, match=problem):
            ServiceAccount.from_json(raw)

    def test_no_file_configured_means_no_pushes(self) -> None:
        assert isinstance(sender_for(None), NullSender)

    def test_an_unreadable_file_means_no_pushes_rather_than_no_server(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        # On the module's own logger: whether app.* propagates to the root,
        # where caplog listens, depends on which test configured logging last.
        logger = logging.getLogger(fcm.__name__)
        logger.addHandler(caplog.handler)
        try:
            sender = sender_for(tmp_path / "missing.json")
        finally:
            logger.removeHandler(caplog.handler)
        assert isinstance(sender, NullSender)
        assert "could not be loaded" in caplog.text

    def test_a_good_file_sends_through_fcm(self, tmp_path: Path) -> None:
        key = tmp_path / "service-account.json"
        key.write_text(KEY_JSON)
        sender = sender_for(key)
        assert isinstance(sender, FcmSender)
        assert sender.project_id == "cylist-test"


# --- The whole path ---------------------------------------------------------


class FakeSender:
    """Stands in for FCM: keeps every message, answers as told per device."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.answers: dict[str, SendOutcome] = {}

    @property
    def enabled(self) -> bool:
        return True

    async def send(self, message: dict[str, Any]) -> SendOutcome:
        self.messages.append(message)
        return self.answers.get(message["token"], SendOutcome.SENT)

    async def aclose(self) -> None:
        return None

    def states(self) -> list[tuple[str, str]]:
        return [(m["data"]["state"], m["data"]["reason"]) for m in self.messages]


@pytest.fixture
async def pushing(app: FastAPI) -> Pusher:
    """The app's own pusher, which its publisher hands committed pushes to."""
    pusher: Pusher = app.state.pusher
    return pusher


@pytest.fixture
async def wired(
    signed_in: AsyncClient, other_client: AsyncClient, app: FastAPI, database: Database
) -> AsyncClient:
    """The signed-in client, with commits published the way the lifespan does.

    Depends on ``other_client`` so that it is built first: building a client
    rebinds the database's publisher to the hub alone.
    """
    database.publish_to(_publisher(app))
    return signed_in


async def _token(client: AsyncClient, *scopes: str) -> dict[str, str]:
    created = await client.post(
        "/tokens", json={"name": "t", "scopes": list(scopes) or ["read", "write"]}
    )
    assert created.status_code == 201, created.text
    return {"Authorization": f"Bearer {created.json()['token']}"}


async def _card(client: AsyncClient, assignee: str | None = None) -> str:
    await client.post("/projects", json=ATLAS)
    body: dict[str, Any] = {
        "title": "Dedupe webhooks",
        "description": "Duplicate deliveries create double payments.",
        "type": "bug",
    }
    if assignee is not None:
        body["assignee_id"] = assignee
    response = await client.post("/projects/ATL/tasks", json=body)
    assert response.status_code == 201, response.text
    return str(response.json()["reference"])


async def _report(
    client: AsyncClient, agent: dict[str, str], ref: str, state: str, **body: Any
) -> None:
    response = await client.put(
        f"/tasks/{ref}/agent-sessions/{SESSION}", json={"state": state, **body}, headers=agent
    )
    assert response.status_code == 200, response.text


async def _phone(client: AsyncClient, fcm_token: str = PHONE_FCM) -> dict[str, str]:
    phone = await _token(client)
    registered = await client.post(
        "/devices", json={"fcm_token": fcm_token, "label": "Pixel"}, headers=phone
    )
    assert registered.status_code == 200, registered.text
    return phone


class TestPushingAgentSessions:
    async def test_an_agent_asking_a_question_lights_up_the_phone(
        self, wired: AsyncClient, pushing: Pusher, database: Database
    ) -> None:
        sender = FakeSender()
        await _phone(wired)
        agent = await _token(wired)
        ref = await _card(wired)

        await _report(wired, agent, ref, "working", client_name=ref)
        asked = await wired.post(
            f"/tasks/{ref}/agent-prompts",
            json={"question": "Which sheet is the March schedule?", "options": ["Sheet 1", "Mar"]},
            headers=agent,
        )
        assert asked.status_code == 201, asked.text
        await pushing.drain(database, sender)

        assert sender.states() == [("working", ""), ("waiting", "question")]
        question = sender.messages[-1]
        assert question["token"] == PHONE_FCM
        assert question["data"]["task"] == ref
        assert question["data"]["task_title"] == "Dedupe webhooks"
        assert question["data"]["note"] == "Which sheet is the March schedule?"
        assert question["android"]["priority"] == "HIGH"

    async def test_turn_ended_and_finished_are_pushed_and_heartbeats_are_not(
        self, wired: AsyncClient, pushing: Pusher, database: Database
    ) -> None:
        sender = FakeSender()
        await _phone(wired)
        agent = await _token(wired)
        ref = await _card(wired)

        await _report(wired, agent, ref, "working")
        await _report(wired, agent, ref, "working")  # heartbeat
        await _report(wired, agent, ref, "working", note="Running pytest")  # a tool call
        await _report(wired, agent, ref, "waiting", reason="turn_ended")
        await _report(wired, agent, ref, "done", reason="session_ended")
        await pushing.drain(database, sender)

        assert sender.states() == [
            ("working", ""),
            ("waiting", "turn_ended"),
            ("done", "session_ended"),
        ]

    async def test_a_dropped_socket_ending_the_session_is_pushed(
        self, wired: AsyncClient, pushing: Pusher, database: Database, owner: Person
    ) -> None:
        from app.services import agent_reports

        sender = FakeSender()
        await _phone(wired)
        agent = await _token(wired)
        ref = await _card(wired)
        await _report(wired, agent, ref, "working")
        await pushing.drain(database, FakeSender())

        async with database.session() as session:
            await agent_reports.end_session(session, SessionKey(owner.id, SESSION))
        await pushing.drain(database, sender)

        assert sender.states() == [("done", "connection_lost")]

    async def test_a_change_that_rolls_back_is_never_pushed(
        self, wired: AsyncClient, pushing: Pusher, database: Database, owner: Person
    ) -> None:
        from app.services import agent_reports

        await _phone(wired)
        agent = await _token(wired)
        ref = await _card(wired)
        await _report(wired, agent, ref, "working")
        await pushing.drain(database, FakeSender())

        with pytest.raises(RuntimeError):
            async with database.session() as session:
                await agent_reports.end_session(session, SessionKey(owner.id, SESSION))
                await session.flush()
                raise RuntimeError("the request failed after the flush")

        assert pushing.pending() == 0

    async def test_the_card_owner_is_told_too_but_not_the_agent_person(
        self,
        wired: AsyncClient,
        other_client: AsyncClient,
        pushing: Pusher,
        database: Database,
    ) -> None:
        sender = FakeSender()
        await _phone(wired)
        await wired.post("/projects", json=ATLAS)
        aditi, invite = await open_account(wired, ADITI, "aditi@cylist.dev")
        accepted = await other_client.post(
            "/auth/accept-invite", json={"token": invite, "password": INVITEE_PASSWORD}
        )
        assert accepted.status_code == 200, accepted.text
        await _phone(other_client, ADITI_FCM)
        members = (await wired.get("/projects/ATL/members")).json()["members"]
        await put_members(wired, "ATL", [m["id"] for m in members] + [aditi["id"]])
        agent = await _token(wired)
        ref = await _card(wired, assignee=aditi["id"])

        await _report(wired, agent, ref, "working")
        await pushing.drain(database, sender)

        assert sorted(m["token"] for m in sender.messages) == sorted([PHONE_FCM, ADITI_FCM])

    async def test_a_card_owned_by_the_agent_tells_only_whoever_runs_it(
        self, wired: AsyncClient, pushing: Pusher, database: Database
    ) -> None:
        sender = FakeSender()
        await _phone(wired)
        await wired.post("/projects", json=ATLAS)
        members = (await wired.get("/projects/ATL/members")).json()["members"]
        machine = next(m["id"] for m in members if m["is_agent"])
        agent = await _token(wired)
        ref = await _card(wired, assignee=machine)

        await _report(wired, agent, ref, "working")
        await pushing.drain(database, sender)

        assert [m["token"] for m in sender.messages] == [PHONE_FCM]

    async def test_a_signed_out_phone_is_not_told(
        self, wired: AsyncClient, pushing: Pusher, database: Database
    ) -> None:
        sender = FakeSender()
        phone = await _phone(wired)
        me = (await wired.get("/me", headers=phone)).json()
        await wired.delete(f"/tokens/{me['token_id']}", headers=phone)
        agent = await _token(wired)
        ref = await _card(wired)

        await _report(wired, agent, ref, "working")
        await pushing.drain(database, sender)

        assert sender.messages == []

    async def test_a_device_fcm_says_is_gone_is_forgotten(
        self, wired: AsyncClient, pushing: Pusher, database: Database
    ) -> None:
        sender = FakeSender()
        sender.answers[PHONE_FCM] = SendOutcome.UNREGISTERED
        phone = await _phone(wired)
        agent = await _token(wired)
        ref = await _card(wired)

        await _report(wired, agent, ref, "working")
        await pushing.drain(database, sender)

        assert (await wired.get("/devices", headers=phone)).json() == []

    async def test_a_token_endpoint_answering_html_costs_one_phone_not_the_rest(
        self,
        wired: AsyncClient,
        pushing: Pusher,
        database: Database,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        google = FakeGoogle()
        portal = [True]

        def google_behind_a_portal(request: httpx.Request) -> httpx.Response:
            # The first exchange meets a captive portal's page, with a 200.
            if request.url == "https://oauth2.example/token" and portal:
                portal.clear()
                return httpx.Response(200, text="<html><body>Sign in to Wi-Fi</body></html>")
            return google(request)

        sender = FcmSender(
            ServiceAccount.from_json(KEY_JSON),
            transport=httpx.MockTransport(google_behind_a_portal),
        )
        await _phone(wired)
        await _phone(wired, "phone-install:APA91b-owner-0003")
        agent = await _token(wired)
        ref = await _card(wired)
        await _report(wired, agent, ref, "working")

        logger = logging.getLogger(fcm.__name__)
        logger.addHandler(caplog.handler)
        try:
            assert await pushing.drain(database, sender) == 1
        finally:
            logger.removeHandler(caplog.handler)
            await sender.aclose()

        assert len(google.sends) == 1
        # Once or twice — caplog may hear it on the root too, as well as here.
        failures = [r for r in caplog.records if r.message == "Push failed before FCM answered"]
        assert failures
        assert all("not JSON" in r.__dict__["context"]["detail"] for r in failures)

    async def test_with_no_key_configured_nothing_is_sent_and_nothing_breaks(
        self, wired: AsyncClient, pushing: Pusher, database: Database
    ) -> None:
        await _phone(wired)
        agent = await _token(wired)
        ref = await _card(wired)

        await _report(wired, agent, ref, "working")

        assert await pushing.drain(database, NullSender()) == 0


def test_the_module_never_logs_a_whole_device_token() -> None:
    assert fcm.redact("abcdefghijklmnopqrstuvwxyz") == "…uvwxyz"
