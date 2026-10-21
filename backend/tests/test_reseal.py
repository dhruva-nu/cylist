"""Rotating the vault key: a second key, a reseal, and retiring the first.

The whole procedure DEPLOY.md describes, against a real database: every step
has to leave every secret readable, or rotation is just a slower way to lose
the vault.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import select, update

from app import cli
from app.config import Settings
from app.core.crypto import SecretUnreadableError, cipher_for
from app.db import Database
from app.models import VaultSecret
from app.models.hook import Hook
from app.services import hooks
from app.services.reseal import reseal, versions_in_use
from tests.conftest import VAULT_KEY, client_for, sign_in

NEW_KEY = base64.urlsafe_b64encode(b"n" * 32).decode()
"""Version 2, as an operator adds it."""

ATLAS = {"key": "ATL", "name": "Atlas Billing Migration"}
RECEIVER = "https://ci.example.com/cylist"


def _rotated(settings: Settings) -> Settings:
    """Both keys held: the state between adding a key and retiring the old one."""
    return settings.model_copy(update={"vault_keys": {2: NEW_KEY}})


def _retired(settings: Settings) -> Settings:
    """Only the new key: ``CYLIST_VAULT_KEY`` taken out of the configuration."""
    return settings.model_copy(update={"vault_key": "", "vault_keys": {2: NEW_KEY}})


@pytest.fixture
async def rotated(settings: Settings, database: Database) -> AsyncIterator[AsyncClient]:
    async with client_for(_rotated(settings), database) as http:
        yield await sign_in(http)


@pytest.fixture
async def retired(settings: Settings, database: Database) -> AsyncIterator[AsyncClient]:
    async with client_for(_retired(settings), database) as http:
        yield await sign_in(http)


async def _tree(client: AsyncClient) -> str:
    await client.post("/projects", json=ATLAS)
    created = await client.post("/projects/ATL/vault/trees", json={"name": "Logins"})
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


async def _secret(client: AsyncClient, tree_id: str, name: str) -> dict[str, Any]:
    response = await client.post(
        "/vault/nodes",
        json={"tree_id": tree_id, "name": name, "kind": "secret", "secret": {"value": name}},
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _hook(client: AsyncClient, name: str) -> dict[str, Any]:
    response = await client.post("/projects/ATL/hooks", json={"name": name, "url": RECEIVER})
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _reveal(client: AsyncClient, node_id: str) -> str:
    response = await client.post(f"/vault/nodes/{node_id}/reveal")
    assert response.status_code == 200, response.text
    return str(response.json()["value"])


async def _hook_secret(database: Database, settings: Settings, hook_id: str) -> str:
    """What the courier would sign with, opened with this configuration's keys."""
    async with database.session() as session:
        hook = await session.get(Hook, UUID(hook_id))
        assert hook is not None
        return hooks.secret_of(cipher_for(settings.vault_keyring), hook)


async def _before_rotation(
    client: AsyncClient,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """A vault and some hooks, all sealed under version 1."""
    tree_id = await _tree(client)
    secrets = [await _secret(client, tree_id, f"Login {n}") for n in range(5)]
    made = [await _hook(client, f"Hook {n}") for n in range(2)]
    assert {secret["secret"]["key_version"] for secret in secrets} == {1}
    return secrets, made


class TestAddingAKey:
    async def test_rows_sealed_before_still_open(
        self,
        signed_in: AsyncClient,
        rotated: AsyncClient,
        settings: Settings,
        database: Database,
    ) -> None:
        secrets, made = await _before_rotation(signed_in)

        assert await _reveal(rotated, secrets[0]["id"]) == "Login 0"
        assert await _hook_secret(database, _rotated(settings), made[0]["id"]) == made[0]["secret"]

    async def test_new_rows_seal_under_the_newest_key(
        self, rotated: AsyncClient, settings: Settings, database: Database
    ) -> None:
        tree_id = await _tree(rotated)

        secret = await _secret(rotated, tree_id, "Fresh")
        hook = await _hook(rotated, "Fresh")

        assert secret["secret"]["key_version"] == 2
        assert await versions_in_use(database) == {2: 2}
        assert await _hook_secret(database, _rotated(settings), hook["id"]) == hook["secret"]

    async def test_a_changed_secret_moves_to_the_newest_key(
        self, signed_in: AsyncClient, rotated: AsyncClient
    ) -> None:
        secrets, _ = await _before_rotation(signed_in)

        response = await rotated.patch(
            f"/vault/nodes/{secrets[0]['id']}", json={"secret": {"value": "changed"}}
        )

        assert response.status_code == 200, response.text
        assert response.json()["secret"]["key_version"] == 2


class TestReseal:
    async def test_moves_every_vault_and_hook_secret_to_the_newest_key(
        self, signed_in: AsyncClient, settings: Settings, database: Database
    ) -> None:
        await _before_rotation(signed_in)

        # A batch smaller than either table, so the cursor has to move.
        report = await reseal(database, cipher_for(_rotated(settings).vault_keyring), batch=2)

        assert report.resealed == {"vault_secret": 5, "hook": 2}
        assert report.unreadable == []
        assert report.remaining == {2: 7}
        assert report.retirable == (1,)

    async def test_after_it_the_old_key_can_be_retired(
        self,
        signed_in: AsyncClient,
        retired: AsyncClient,
        settings: Settings,
        database: Database,
    ) -> None:
        secrets, made = await _before_rotation(signed_in)

        await reseal(database, cipher_for(_rotated(settings).vault_keyring))

        for secret in secrets:
            assert await _reveal(retired, secret["id"]) == secret["name"]
        for hook in made:
            assert await _hook_secret(database, _retired(settings), hook["id"]) == hook["secret"]

    async def test_without_it_a_retired_key_leaves_rows_unreadable(
        self, signed_in: AsyncClient, retired: AsyncClient
    ) -> None:
        """Why the reseal is not optional — and that the failure says so."""
        secrets, _ = await _before_rotation(signed_in)

        response = await retired.post(f"/vault/nodes/{secrets[0]['id']}/reveal")

        assert response.status_code == 500
        assert response.json()["error"]["code"] == "secret_unreadable"
        assert "version 1" in response.json()["error"]["message"]

    async def test_running_it_again_does_nothing(
        self, signed_in: AsyncClient, settings: Settings, database: Database
    ) -> None:
        await _before_rotation(signed_in)
        cipher = cipher_for(_rotated(settings).vault_keyring)
        await reseal(database, cipher)

        again = await reseal(database, cipher)

        assert again.resealed == {"vault_secret": 0, "hook": 0}
        assert again.remaining == {2: 7}

    async def test_the_binding_survives(
        self, signed_in: AsyncClient, settings: Settings, database: Database
    ) -> None:
        """Resealed against the same row and domain: moved elsewhere, it still
        refuses to open."""
        secrets, _ = await _before_rotation(signed_in)
        cipher = cipher_for(_rotated(settings).vault_keyring)
        await reseal(database, cipher)

        async with database.session() as session:
            first = await session.get(VaultSecret, UUID(secrets[0]["id"]))
            assert first is not None
            assert cipher.open(first.secret_ciphertext, node_id=first.node_id, key_version=2)
            with pytest.raises(SecretUnreadableError, match="authentication check"):
                cipher.open(first.secret_ciphertext, node_id=UUID(secrets[1]["id"]), key_version=2)

    async def test_leaves_updated_at_alone(
        self, signed_in: AsyncClient, settings: Settings, database: Database
    ) -> None:
        """Nobody changed a credential, so no page should say somebody did."""
        await _before_rotation(signed_in)
        async with database.session() as session:
            before = dict((await session.execute(select(Hook.id, Hook.updated_at))).tuples().all())

        await reseal(database, cipher_for(_rotated(settings).vault_keyring))

        async with database.session() as session:
            after = dict((await session.execute(select(Hook.id, Hook.updated_at))).tuples().all())
        assert after == before

    async def test_a_row_that_will_not_open_is_reported_and_skipped(
        self, signed_in: AsyncClient, settings: Settings, database: Database
    ) -> None:
        secrets, _ = await _before_rotation(signed_in)
        broken = UUID(secrets[2]["id"])
        async with database.session() as session:
            await session.execute(
                update(VaultSecret)
                .where(VaultSecret.node_id == broken)
                .values(secret_ciphertext=b"\x00" * 40)
            )

        report = await reseal(database, cipher_for(_rotated(settings).vault_keyring), batch=1)

        assert [(row.table, row.row_id, row.key_version) for row in report.unreadable] == [
            ("vault_secret", broken, 1)
        ]
        assert report.resealed == {"vault_secret": 4, "hook": 2}
        assert report.remaining == {1: 1, 2: 6}
        # Still needed by that row, so not offered for retirement.
        assert report.retirable == ()


class TestConfiguration:
    def test_the_single_key_is_version_one(self, settings: Settings) -> None:
        """An existing deployment changes nothing and keeps sealing under 1."""
        assert settings.vault_keyring == ((1, VAULT_KEY),)
        assert cipher_for(settings.vault_keyring).version == 1

    def test_further_keys_come_from_cylist_vault_keys(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CYLIST_VAULT_KEY", VAULT_KEY)
        monkeypatch.setenv("CYLIST_VAULT_KEYS", f'{{"2": "{NEW_KEY}"}}')

        settings = Settings()

        assert settings.vault_keyring == ((1, VAULT_KEY), (2, NEW_KEY))

    def test_version_one_may_be_listed_with_the_rest(self) -> None:
        settings = Settings(vault_key="", vault_keys={1: VAULT_KEY, 2: NEW_KEY})

        assert settings.vault_keyring == ((1, VAULT_KEY), (2, NEW_KEY))

    def test_two_different_version_one_keys_are_refused(self) -> None:
        with pytest.raises(ValidationError, match="version 1"):
            Settings(vault_key=VAULT_KEY, vault_keys={1: NEW_KEY})

    def test_a_version_below_one_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="start at 1"):
            Settings(vault_keys={0: NEW_KEY})


class TestCommand:
    def test_refuses_without_a_key(
        self,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        keyless = settings.model_copy(update={"vault_key": ""})
        monkeypatch.setattr(cli, "get_settings", lambda: keyless)

        assert cli.main(["vault-reseal"]) == 1
        assert "CYLIST_VAULT_KEY" in capsys.readouterr().err

    def test_refuses_a_batch_size_of_nothing(self) -> None:
        with pytest.raises(SystemExit):
            cli.main(["vault-reseal", "--batch-size", "0"])

    @pytest.fixture
    async def sealed_under_one(self, signed_in: AsyncClient) -> None:
        await _before_rotation(signed_in)

    @pytest.mark.usefixtures("sealed_under_one")
    def test_reseals_and_says_which_keys_can_go(
        self,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """End to end, as an operator runs it: its own event loop, its own pool."""
        monkeypatch.setattr(cli, "get_settings", lambda: _rotated(settings))

        assert cli.main(["vault-reseal", "--batch-size", "3"]) == 0

        out = capsys.readouterr().out
        assert "vault_secret: 5 resealed" in out
        assert "hook: 2 resealed" in out
        assert "version 2: 7" in out
        assert "version(s) 1 any more" in out
