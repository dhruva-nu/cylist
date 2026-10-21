"""Push devices: a phone registering to be told, and forgetting itself again."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from app.db import Database
from app.services import devices
from tests.conftest import INVITEE_PASSWORD, open_account

FCM_TOKEN = "fcm-install-one:APA91b-0123456789abcdef"
OTHER_TOKEN = "fcm-install-two:APA91b-fedcba9876543210"
RAHUL = {
    "name": "Rahul S",
    "kind": "team",
    "title": "Android engineer",
    "responsibilities": "The phone app.",
}


async def _token(
    client: AsyncClient, *scopes: str, name: str = "Pixel (Android)"
) -> dict[str, Any]:
    created = await client.post(
        "/tokens", json={"name": name, "scopes": list(scopes) or ["read", "write"]}
    )
    assert created.status_code == 201, created.text
    body = created.json()
    return {"headers": {"Authorization": f"Bearer {body['token']}"}, "id": body["id"]}


async def _register(
    client: AsyncClient, headers: dict[str, str], fcm_token: str = FCM_TOKEN, label: str = "Pixel"
) -> dict[str, Any]:
    response = await client.post(
        "/devices", json={"fcm_token": fcm_token, "label": label}, headers=headers
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


class TestRegistering:
    async def test_a_phone_token_with_write_registers_itself(
        self, signed_in: AsyncClient, owner: Any
    ) -> None:
        phone = await _token(signed_in)

        device = await _register(signed_in, phone["headers"])

        assert device["platform"] == "android"
        assert device["label"] == "Pixel"
        assert device["person_id"] == str(owner.id)
        assert device["token_id"] == phone["id"]
        assert "fcm_token" not in device

    async def test_registering_again_is_the_same_device(self, signed_in: AsyncClient) -> None:
        phone = await _token(signed_in)
        first = await _register(signed_in, phone["headers"])

        again = await _register(signed_in, phone["headers"], label="Pixel 9")

        assert again["id"] == first["id"]
        assert again["label"] == "Pixel 9"
        assert again["last_registered_at"] >= first["last_registered_at"]
        listed = (await signed_in.get("/devices", headers=phone["headers"])).json()["items"]
        assert [device["id"] for device in listed] == [first["id"]]

    async def test_a_new_sign_in_on_the_phone_moves_the_device_to_its_token(
        self, signed_in: AsyncClient
    ) -> None:
        before = await _token(signed_in)
        after = await _token(signed_in)
        first = await _register(signed_in, before["headers"])

        moved = await _register(signed_in, after["headers"])

        assert moved["id"] == first["id"]
        assert moved["token_id"] == after["id"]

    async def test_somebody_else_signing_in_on_the_phone_takes_it_over(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        mine = await _token(signed_in)
        await _register(signed_in, mine["headers"])
        person, invite = await open_account(signed_in, RAHUL, "rahul@example.com")
        accepted = await other_client.post(
            "/auth/accept-invite", json={"token": invite, "password": INVITEE_PASSWORD}
        )
        assert accepted.status_code == 200, accepted.text
        theirs = await _token(other_client)

        taken = await _register(other_client, theirs["headers"])

        assert taken["person_id"] == person["id"]
        assert (await signed_in.get("/devices", headers=mine["headers"])).json()["items"] == []

    async def test_a_read_only_token_cannot_register(self, signed_in: AsyncClient) -> None:
        reader = await _token(signed_in, "read")

        response = await signed_in.post(
            "/devices", json={"fcm_token": FCM_TOKEN, "label": "Pixel"}, headers=reader["headers"]
        )

        assert response.status_code == 403

    async def test_the_bootstrap_session_is_nobody_to_push_to(
        self, bootstrapped: AsyncClient
    ) -> None:
        response = await bootstrapped.post("/devices", json={"fcm_token": FCM_TOKEN, "label": "X"})

        assert response.status_code == 403

    async def test_an_empty_token_is_refused(self, signed_in: AsyncClient) -> None:
        phone = await _token(signed_in)

        response = await signed_in.post(
            "/devices", json={"fcm_token": "", "label": "Pixel"}, headers=phone["headers"]
        )

        assert response.status_code == 422


class TestUnregistering:
    async def test_a_phone_forgets_its_own_device(self, signed_in: AsyncClient) -> None:
        phone = await _token(signed_in)
        device = await _register(signed_in, phone["headers"])

        response = await signed_in.delete(f"/devices/{device['id']}", headers=phone["headers"])

        assert response.status_code == 200
        assert (await signed_in.get("/devices", headers=phone["headers"])).json()["items"] == []
        again = await signed_in.delete(f"/devices/{device['id']}", headers=phone["headers"])
        assert again.status_code == 404

    async def test_nobody_can_forget_somebody_else_s_device(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        mine = await _token(signed_in)
        device = await _register(signed_in, mine["headers"])
        _, invite = await open_account(signed_in, RAHUL, "rahul@example.com")
        await other_client.post(
            "/auth/accept-invite", json={"token": invite, "password": INVITEE_PASSWORD}
        )
        theirs = await _token(other_client)

        response = await other_client.delete(f"/devices/{device['id']}", headers=theirs["headers"])

        assert response.status_code == 404
        assert len((await signed_in.get("/devices", headers=mine["headers"])).json()["items"]) == 1


class TestRecipients:
    async def test_a_live_token_s_device_is_sent_to(
        self, signed_in: AsyncClient, owner: Any, database: Database
    ) -> None:
        phone = await _token(signed_in)
        await _register(signed_in, phone["headers"])

        async with database.session() as session:
            found = await devices.recipients(session, {owner.id})

        assert [device.fcm_token for device in found] == [FCM_TOKEN]

    async def test_revoking_the_phone_s_token_silences_it(
        self, signed_in: AsyncClient, owner: Any, database: Database
    ) -> None:
        phone = await _token(signed_in)
        await _register(signed_in, phone["headers"])
        other = await _token(signed_in)
        await _register(signed_in, other["headers"], fcm_token=OTHER_TOKEN)

        revoked = await signed_in.delete(f"/tokens/{phone['id']}", headers=phone["headers"])
        assert revoked.status_code == 200, revoked.text

        async with database.session() as session:
            found = await devices.recipients(session, {owner.id})
        assert [device.fcm_token for device in found] == [OTHER_TOKEN]

    async def test_nobody_has_no_devices(self, database: Database) -> None:
        async with database.session() as session:
            assert await devices.recipients(session, set()) == []

    async def test_forgetting_tokens_deletes_their_devices(
        self, signed_in: AsyncClient, owner: Any, database: Database
    ) -> None:
        phone = await _token(signed_in)
        await _register(signed_in, phone["headers"])

        async with database.session() as session:
            assert await devices.forget_tokens(session, [FCM_TOKEN, "never-seen"]) == 1
        async with database.session() as session:
            assert await devices.recipients(session, {owner.id}) == []
