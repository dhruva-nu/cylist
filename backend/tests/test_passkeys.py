"""Passkeys: registering, signing in, managing, recovering (CYLIST-204)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app import cli
from app.auth.dependencies import SESSION_COOKIE
from app.auth.throttle import SignInThrottle
from app.config import Settings
from app.core.clock import now
from app.db import Database
from app.models.passkey import PasskeyChallenge, PersonCredential, RecoveryRequest
from app.models.person import Person
from tests.conftest import INVITEE_PASSWORD, OWNER_EMAIL, client_on, open_account
from tests.webauthn_device import Device

ADITI = {
    "name": "Aditi K",
    "kind": "team",
    "title": "Backend engineer",
    "responsibilities": "Payments and webhooks.",
}


def _no_throttle(client: AsyncClient) -> None:
    client.app.state.sign_in_throttle = SignInThrottle(  # type: ignore[attr-defined]
        address_burst=10_000, account_burst=10_000
    )


async def register(client: AsyncClient, device: Device, name: str = "OnePlus phone") -> Any:
    options = await client.post("/auth/passkeys/register/options")
    assert options.status_code == 200, options.text
    body = options.json()
    return await client.post(
        "/auth/passkeys/register/verify",
        json={
            "challenge_id": body["challenge_id"],
            "credential": device.create(body["options"]),
            "name": name,
        },
    )


async def login_with(client: AsyncClient, device: Device, **get: Any) -> Any:
    started = (await client.post("/auth/passkeys/login/options")).json()
    return await client.post(
        "/auth/passkeys/login/verify",
        json={
            "challenge_id": started["challenge_id"],
            "credential": device.get(started["options"], **get),
        },
    )


async def activity_verbs(client: AsyncClient) -> list[tuple[str, dict[str, Any]]]:
    items = (await client.get("/activity")).json()["items"]
    return [(entry["verb"], entry.get("payload") or {}) for entry in items]


class TestRegistration:
    async def test_registers_and_lists_a_passkey(self, signed_in: AsyncClient) -> None:
        device = Device()
        response = await register(signed_in, device)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["passkey"]["name"] == "OnePlus phone"
        assert body["passkey_count"] == 1
        assert body["recommend_another"] is True

        listed = (await signed_in.get("/me/passkeys")).json()
        assert [p["name"] for p in listed] == ["OnePlus phone"]
        assert "public_key" not in listed[0]

    async def test_a_second_passkey_stops_the_prompt(self, signed_in: AsyncClient) -> None:
        await register(signed_in, Device(), "Phone")
        second = await register(signed_in, Device(), "Laptop")
        assert second.json()["recommend_another"] is False

    async def test_the_eleventh_is_refused(self, signed_in: AsyncClient) -> None:
        _no_throttle(signed_in)
        for number in range(10):
            assert (await register(signed_in, Device(), f"Key {number}")).status_code == 200
        assert (await signed_in.post("/auth/passkeys/register/options")).status_code == 409

    async def test_needs_a_signed_in_session(self, client: AsyncClient) -> None:
        assert (await client.post("/auth/passkeys/register/options")).status_code == 401

    async def test_the_bootstrap_session_cannot_hold_one(self, bootstrapped: AsyncClient) -> None:
        assert (await bootstrapped.post("/auth/passkeys/register/options")).status_code == 403

    async def test_the_same_authenticator_twice_is_a_conflict(self, signed_in: AsyncClient) -> None:
        device = Device()
        await register(signed_in, device)
        again = await register(signed_in, device)
        assert again.status_code in (409, 422)
        assert len((await signed_in.get("/me/passkeys")).json()) == 1

    async def test_a_foreign_origin_is_refused(self, signed_in: AsyncClient) -> None:
        body = (await signed_in.post("/auth/passkeys/register/options")).json()
        device = Device()
        refused = await signed_in.post(
            "/auth/passkeys/register/verify",
            json={
                "challenge_id": body["challenge_id"],
                "credential": device.create(body["options"], origin="https://evil.example"),
            },
        )
        assert refused.status_code == 422
        assert (await signed_in.get("/me/passkeys")).json() == []

    async def test_another_relying_party_is_refused(self, signed_in: AsyncClient) -> None:
        body = (await signed_in.post("/auth/passkeys/register/options")).json()
        device = Device(rp_id="evil.example")
        refused = await signed_in.post(
            "/auth/passkeys/register/verify",
            json={
                "challenge_id": body["challenge_id"],
                "credential": device.create(body["options"]),
            },
        )
        assert refused.status_code == 422

    async def test_a_challenge_is_spent_by_a_wrong_answer(self, signed_in: AsyncClient) -> None:
        body = (await signed_in.post("/auth/passkeys/register/options")).json()
        device = Device()
        bad = device.create(body["options"], origin="https://evil.example")
        good = device.create(body["options"])
        for credential, expected in ((bad, 422), (good, 422)):
            response = await signed_in.post(
                "/auth/passkeys/register/verify",
                json={"challenge_id": body["challenge_id"], "credential": credential},
            )
            assert response.status_code == expected
        assert (await signed_in.get("/me/passkeys")).json() == []

    async def test_a_challenge_cannot_be_reused_after_it_worked(
        self, signed_in: AsyncClient
    ) -> None:
        body = (await signed_in.post("/auth/passkeys/register/options")).json()
        credential = Device().create(body["options"])
        payload = {"challenge_id": body["challenge_id"], "credential": credential}
        assert (
            await signed_in.post("/auth/passkeys/register/verify", json=payload)
        ).status_code == 200
        assert (
            await signed_in.post("/auth/passkeys/register/verify", json=payload)
        ).status_code == 422

    async def test_an_expired_challenge_is_refused(
        self, signed_in: AsyncClient, database: Database
    ) -> None:
        body = (await signed_in.post("/auth/passkeys/register/options")).json()
        async with database.session() as db:
            await db.execute(
                update(PasskeyChallenge).values(expires_at=now() - timedelta(seconds=1))
            )
        refused = await signed_in.post(
            "/auth/passkeys/register/verify",
            json={
                "challenge_id": body["challenge_id"],
                "credential": Device().create(body["options"]),
            },
        )
        assert refused.status_code == 422

    async def test_registration_is_in_the_audit_trail(self, signed_in: AsyncClient) -> None:
        await register(signed_in, Device(), "OnePlus phone")
        assert ("passkey.registered", {"name": "OnePlus phone"}) in await activity_verbs(signed_in)


class TestSignIn:
    async def test_signs_in_with_the_same_session_a_password_gives(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        device = Device()
        await register(signed_in, device)
        _no_throttle(other_client)

        response = await login_with(other_client, device)
        assert response.status_code == 200, response.text
        assert response.json()["person"]["email"] == OWNER_EMAIL
        assert SESSION_COOKIE in other_client.cookies
        assert (await other_client.get("/me")).json()["person"]["email"] == OWNER_EMAIL

    async def test_each_method_is_in_the_audit_trail(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        device = Device()
        await register(signed_in, device)
        await login_with(other_client, device)
        methods = [
            p.get("method") for v, p in await activity_verbs(signed_in) if v == "session.started"
        ]
        assert "password" in methods
        assert "passkey" in methods

    async def test_a_reused_challenge_is_refused(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        device = Device()
        await register(signed_in, device)
        started = (await other_client.post("/auth/passkeys/login/options")).json()
        payload = {
            "challenge_id": started["challenge_id"],
            "credential": device.get(started["options"]),
        }
        assert (
            await other_client.post("/auth/passkeys/login/verify", json=payload)
        ).status_code == 200
        replay = await other_client.post("/auth/passkeys/login/verify", json=payload)
        assert replay.status_code == 401

    async def test_a_counter_that_does_not_increase_is_refused(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        device = Device()
        await register(signed_in, device)
        _no_throttle(other_client)
        assert (await login_with(other_client, device, count=5)).status_code == 200
        for stale in (5, 4):
            refused = await login_with(other_client, device, count=stale)
            assert refused.status_code == 401
        assert (await login_with(other_client, device, count=6)).status_code == 200

    async def test_a_counterless_authenticator_is_accepted(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        device = Device()
        await register(signed_in, device)
        _no_throttle(other_client)
        for _ in range(2):
            assert (await login_with(other_client, device, count=0)).status_code == 200

    async def test_an_unknown_passkey_is_the_same_refusal(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await register(signed_in, Device())
        stranger = Device()
        stranger.user_handle = b"x"
        refused = await login_with(other_client, stranger)
        assert refused.status_code == 401
        assert refused.json()["error"]["message"] == "That passkey was not accepted."

    async def test_a_foreign_origin_is_refused(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        device = Device()
        await register(signed_in, device)
        assert (
            await login_with(other_client, device, origin="https://evil.example")
        ).status_code == 401

    async def test_unverified_user_is_refused(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        device = Device()
        await register(signed_in, device)
        refused = await login_with(other_client, device, flags=0x01)  # present, not verified
        assert refused.status_code == 401

    async def test_an_expired_challenge_is_refused(
        self, signed_in: AsyncClient, other_client: AsyncClient, database: Database
    ) -> None:
        device = Device()
        await register(signed_in, device)
        started = (await other_client.post("/auth/passkeys/login/options")).json()
        async with database.session() as db:
            await db.execute(
                update(PasskeyChallenge).values(expires_at=now() - timedelta(seconds=1))
            )
        refused = await other_client.post(
            "/auth/passkeys/login/verify",
            json={
                "challenge_id": started["challenge_id"],
                "credential": device.get(started["options"]),
            },
        )
        assert refused.status_code == 401

    async def test_an_archived_person_cannot_sign_in(
        self, signed_in: AsyncClient, other_client: AsyncClient, database: Database
    ) -> None:
        device = Device()
        await register(signed_in, device)
        async with database.session() as db:
            await db.execute(update(Person).values(archived_at=now()))
        assert (await login_with(other_client, device)).status_code == 401

    async def test_attempts_are_throttled(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        other_client.app.state.sign_in_throttle = SignInThrottle(  # type: ignore[attr-defined]
            address_burst=2, address_per_minute=1
        )
        statuses = [
            (await other_client.post("/auth/passkeys/login/options")).status_code for _ in range(3)
        ]
        assert statuses == [200, 200, 429]


class TestManaging:
    async def test_renames_and_removes(self, signed_in: AsyncClient) -> None:
        await register(signed_in, Device(), "Old")
        passkey = (await signed_in.get("/me/passkeys")).json()[0]

        renamed = await signed_in.patch(
            f"/me/passkeys/{passkey['id']}", json={"name": "Work phone"}
        )
        assert renamed.json()["name"] == "Work phone"

        assert (await signed_in.delete(f"/me/passkeys/{passkey['id']}")).status_code == 200
        assert (await signed_in.get("/me/passkeys")).json() == []
        verbs = [v for v, _ in await activity_verbs(signed_in)]
        assert "passkey.renamed" in verbs
        assert "passkey.deleted" in verbs

    async def test_the_last_passkey_goes_while_the_password_works(
        self, signed_in: AsyncClient
    ) -> None:
        await register(signed_in, Device())
        passkey = (await signed_in.get("/me/passkeys")).json()[0]
        assert (await signed_in.delete(f"/me/passkeys/{passkey['id']}")).status_code == 200

    async def test_the_last_passkey_stays_when_there_is_no_password(
        self, signed_in: AsyncClient, database: Database
    ) -> None:
        await register(signed_in, Device(), "Only")
        await register(signed_in, Device(), "Spare")
        async with database.session() as db:
            await db.execute(update(Person).values(password_locked=True))
        first, second = (await signed_in.get("/me/passkeys")).json()

        assert (await signed_in.delete(f"/me/passkeys/{first['id']}")).status_code == 200
        refused = await signed_in.delete(f"/me/passkeys/{second['id']}")
        assert refused.status_code == 422
        assert refused.json()["error"]["reason"] == "last_passkey"
        assert len((await signed_in.get("/me/passkeys")).json()) == 1

    async def test_somebody_elses_passkey_is_not_found(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        _no_throttle(other_client)
        await register(signed_in, Device())
        passkey = (await signed_in.get("/me/passkeys")).json()[0]
        created, token = await open_account(signed_in, ADITI, "aditi@cylist.dev")
        assert created
        await other_client.post(
            "/auth/accept-invite", json={"token": token, "password": INVITEE_PASSWORD}
        )
        assert (await other_client.delete(f"/me/passkeys/{passkey['id']}")).status_code == 404
        assert (
            await other_client.patch(f"/me/passkeys/{passkey['id']}", json={"name": "mine"})
        ).status_code == 404


class TestRecovery:
    async def start(self, client: AsyncClient, email: str = OWNER_EMAIL) -> dict[str, Any]:
        response = await client.post("/auth/recovery/start", json={"email": email})
        assert response.status_code == 200, response.text
        return response.json()

    async def claim(self, client: AsyncClient, started: dict[str, Any], secret: str | None = None):
        return await client.post(
            f"/auth/recovery/{started['request_id']}/claim",
            json={"secret": secret or started["secret"]},
        )

    async def test_a_signed_in_device_approves_a_new_one(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        _no_throttle(other_client)
        started = await self.start(other_client)
        assert (await self.claim(other_client, started)).json() == {
            "state": "pending",
            "identity": None,
        }

        approved = await signed_in.post("/auth/recovery/approve", json={"code": started["code"]})
        assert approved.status_code == 200, approved.text

        claimed = (await self.claim(other_client, started)).json()
        assert claimed["state"] == "signed_in"
        assert claimed["identity"]["person"]["email"] == OWNER_EMAIL
        # The new device can now register a passkey.
        assert (await register(other_client, Device())).status_code == 200

    async def test_a_typed_code_may_be_lower_case_and_unhyphenated(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        started = await self.start(other_client)
        typed = started["code"].replace("-", "").lower()
        assert (
            await signed_in.post("/auth/recovery/approve", json={"code": typed})
        ).status_code == 200

    async def test_a_code_cannot_be_used_twice(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        _no_throttle(other_client)
        started = await self.start(other_client)
        await signed_in.post("/auth/recovery/approve", json={"code": started["code"]})
        assert (await self.claim(other_client, started)).json()["state"] == "signed_in"
        assert (await self.claim(other_client, started)).status_code == 422
        # The approving device was signed out by the recovery, so it cannot even ask.
        assert (
            await signed_in.post("/auth/recovery/approve", json={"code": started["code"]})
        ).status_code == 401

    async def test_the_code_alone_cannot_claim(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        _no_throttle(other_client)
        started = await self.start(other_client)
        await signed_in.post("/auth/recovery/approve", json={"code": started["code"]})
        assert (await self.claim(other_client, started, secret="guess")).status_code == 422
        assert (await self.claim(other_client, started)).json()["state"] == "signed_in"

    async def test_an_expired_code_is_refused(
        self, signed_in: AsyncClient, other_client: AsyncClient, database: Database
    ) -> None:
        started = await self.start(other_client)
        async with database.session() as db:
            await db.execute(
                update(RecoveryRequest).values(expires_at=now() - timedelta(seconds=1))
            )
        approve = await signed_in.post("/auth/recovery/approve", json={"code": started["code"]})
        assert approve.status_code == 422
        assert (await self.claim(other_client, started)).status_code == 422

    async def test_approval_after_expiry_cannot_be_claimed_either(
        self, signed_in: AsyncClient, other_client: AsyncClient, database: Database
    ) -> None:
        started = await self.start(other_client)
        await signed_in.post("/auth/recovery/approve", json={"code": started["code"]})
        async with database.session() as db:
            await db.execute(
                update(RecoveryRequest).values(expires_at=now() - timedelta(seconds=1))
            )
        assert (await self.claim(other_client, started)).status_code == 422

    async def test_an_unknown_email_looks_the_same(self, other_client: AsyncClient) -> None:
        _no_throttle(other_client)
        known = await self.start(other_client, OWNER_EMAIL)
        # no such account here: still a request, still "pending", never approvable
        await other_client.post("/auth/recovery/start", json={"email": "nobody@cylist.dev"})
        stranger = await self.start(other_client, "nobody@cylist.dev")
        assert set(stranger) == set(known)
        assert len(stranger["code"]) == len(known["code"])
        assert (await self.claim(other_client, stranger)).json()["state"] == "pending"

    async def test_somebody_elses_code_cannot_be_approved(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        _no_throttle(other_client)
        await open_account(signed_in, ADITI, "aditi@cylist.dev")
        # the owner's device cannot approve a request made for Aditi's address
        started = await self.start(other_client, "aditi@cylist.dev")
        # Aditi has not accepted her invitation, so she has no account: nobody can approve
        refused = await signed_in.post("/auth/recovery/approve", json={"code": started["code"]})
        assert refused.status_code == 422

    async def test_recovery_ends_every_other_session_and_token(
        self, signed_in: AsyncClient, other_client: AsyncClient, app: Any
    ) -> None:
        _no_throttle(other_client)
        minted = (
            await signed_in.post("/tokens", json={"name": "agent", "scopes": ["read"]})
        ).json()
        bearer = {"Authorization": f"Bearer {minted['token']}"}
        async with client_on(app) as third:
            assert (await third.get("/me", headers=bearer)).status_code == 200

            started = await self.start(other_client)
            await signed_in.post("/auth/recovery/approve", json={"code": started["code"]})
            await self.claim(other_client, started)

            assert (await third.get("/me", headers=bearer)).status_code == 401
        assert (await signed_in.get("/me")).status_code == 401
        assert (await other_client.get("/me")).status_code == 200
        verbs = [v for v, _ in await activity_verbs(other_client)]
        assert "recovery.completed" in verbs
        assert "recovery.approved" in verbs

    async def test_starting_is_throttled(self, other_client: AsyncClient) -> None:
        other_client.app.state.sign_in_throttle = SignInThrottle(  # type: ignore[attr-defined]
            address_burst=2, address_per_minute=1
        )
        codes = [
            (
                await other_client.post("/auth/recovery/start", json={"email": OWNER_EMAIL})
            ).status_code
            for _ in range(3)
        ]
        assert codes == [200, 200, 429]

    async def test_approving_is_throttled(self, signed_in: AsyncClient) -> None:
        signed_in.app.state.sign_in_throttle = SignInThrottle(  # type: ignore[attr-defined]
            address_burst=10_000, account_burst=2, account_per_minute=1
        )
        codes = [
            (await signed_in.post("/auth/recovery/approve", json={"code": "AAAA-AAAA"})).status_code
            for _ in range(3)
        ]
        assert codes == [422, 422, 429]


class TestOperatorBreakGlass:
    async def test_resets_everything_and_issues_a_one_time_code(
        self,
        signed_in: AsyncClient,
        other_client: AsyncClient,
        database: Database,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _no_throttle(other_client)
        await register(signed_in, Device())
        monkeypatch.setattr(cli, "get_settings", lambda: settings)
        monkeypatch.setattr(cli, "Database", lambda url: database)
        monkeypatch.setattr(database, "dispose", _noop)

        code = await cli._recover(
            OWNER_EMAIL, "lost phone, verified by call", True, timedelta(minutes=30)
        )
        assert code is not None

        assert (await signed_in.get("/me")).status_code == 401
        async with database.session() as db:
            assert (await db.scalars(select(PersonCredential))).all() == []
            person = await db.scalar(select(Person).where(Person.email == OWNER_EMAIL))
            assert person is not None
            assert person.password_locked

        # The password no longer opens it.
        refused = await other_client.post(
            "/auth/login", json={"email": OWNER_EMAIL, "password": "correct-horse-battery-staple"}
        )
        assert refused.status_code == 401

        redeemed = await other_client.post("/auth/recovery/redeem", json={"code": code})
        assert redeemed.status_code == 200, redeemed.text
        again = await other_client.post("/auth/recovery/redeem", json={"code": code})
        assert again.status_code == 422

        verbs = await activity_verbs(other_client)
        reset = [p for v, p in verbs if v == "recovery.operator_reset"]
        assert reset[0]["reason"] == "lost phone, verified by call"

    async def test_an_unknown_address_changes_nothing(
        self,
        database: Database,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(cli, "get_settings", lambda: settings)
        monkeypatch.setattr(cli, "Database", lambda url: database)
        monkeypatch.setattr(database, "dispose", _noop)
        assert await cli._recover("nobody@x.dev", "why", False, timedelta(minutes=5)) is None

    async def test_an_expired_operator_code_is_refused(
        self,
        signed_in: AsyncClient,
        other_client: AsyncClient,
        database: Database,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(cli, "get_settings", lambda: settings)
        monkeypatch.setattr(cli, "Database", lambda url: database)
        monkeypatch.setattr(database, "dispose", _noop)
        code = await cli._recover(OWNER_EMAIL, "test", False, timedelta(minutes=5))
        assert code is not None
        async with database.session() as db:
            await db.execute(
                update(RecoveryRequest).values(expires_at=now() - timedelta(seconds=1))
            )
        refused = await other_client.post("/auth/recovery/redeem", json={"code": code})
        assert refused.status_code == 422

    async def test_the_command_is_not_an_http_route(self, signed_in: AsyncClient) -> None:
        for path in ("/auth/recovery/operator", "/auth/recover", "/admin/recover"):
            assert (await signed_in.post(path, json={"email": OWNER_EMAIL})).status_code in (
                404,
                405,
            )


async def _noop() -> None:
    return None
