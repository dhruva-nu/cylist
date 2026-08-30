"""The audit trail."""

from __future__ import annotations

from httpx import AsyncClient


async def test_signing_in_is_recorded(signed_in: AsyncClient) -> None:
    entries = (await signed_in.get("/activity")).json()

    assert [entry["verb"] for entry in entries] == ["session.started"]
    assert entries[0]["channel"] == "web"


async def test_records_who_issued_a_token_and_through_which_door(
    signed_in: AsyncClient,
) -> None:
    await signed_in.post("/tokens", json={"name": "board agent", "scopes": ["read"]})

    issued = next(
        entry
        for entry in (await signed_in.get("/activity")).json()
        if entry["verb"] == "token.issued"
    )

    assert issued["actor_label"] == "Web session"
    assert issued["entity_type"] == "token"
    assert issued["payload"] == {"name": "board agent", "scopes": ["read"]}


async def test_never_stores_the_token_plaintext(signed_in: AsyncClient) -> None:
    """The audit log is readable with 'read' alone, so it must hold no secrets."""
    created = await signed_in.post("/tokens", json={"name": "agent", "scopes": ["read"]})
    plaintext = created.json()["token"]

    feed = (await signed_in.get("/activity")).text

    assert plaintext not in feed


async def test_can_be_filtered_by_entity_type(signed_in: AsyncClient) -> None:
    await signed_in.post("/tokens", json={"name": "agent", "scopes": ["read"]})

    entries = (await signed_in.get("/activity", params={"entity_type": "token"})).json()

    assert [entry["entity_type"] for entry in entries] == ["token"]


async def test_an_agents_action_is_attributed_to_the_api_channel(
    signed_in: AsyncClient,
) -> None:
    """The audit log must distinguish a person from a bot."""
    created = await signed_in.post("/tokens", json={"name": "agent", "scopes": ["read"]})
    agent = {"Authorization": f"Bearer {created.json()['token']}"}

    entries = (await signed_in.get("/activity", headers=agent)).json()

    assert {entry["channel"] for entry in entries} == {"web"}
