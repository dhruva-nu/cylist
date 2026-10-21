"""API tokens: minting, scope enforcement and revocation."""

from __future__ import annotations

from httpx import AsyncClient

from app.auth.tokens import TOKEN_PREFIX, generate_token, hash_token, looks_like_token


class TestTokenGeneration:
    def test_tokens_are_prefixed_and_unique(self) -> None:
        first, _ = generate_token()
        second, _ = generate_token()

        assert first.startswith(TOKEN_PREFIX)
        assert first != second

    def test_the_digest_matches_the_plaintext(self) -> None:
        plaintext, digest = generate_token()

        assert digest == hash_token(plaintext)
        assert plaintext not in digest

    def test_shape_check_rejects_foreign_credentials(self) -> None:
        assert looks_like_token("cyl_abc")
        assert not looks_like_token("ghp_abc")
        assert not looks_like_token(TOKEN_PREFIX)
        assert not looks_like_token("")


class TestIssuing:
    async def test_returns_the_plaintext_exactly_once(self, signed_in: AsyncClient) -> None:
        created = await signed_in.post(
            "/tokens", json={"name": "board agent", "scopes": ["read", "write"]}
        )
        assert created.status_code == 201
        assert created.json()["token"].startswith(TOKEN_PREFIX)

        listed = await signed_in.get("/tokens")
        assert listed.status_code == 200
        assert "token" not in listed.json()["items"][0]

    async def test_the_new_token_authenticates(self, signed_in: AsyncClient) -> None:
        created = await signed_in.post("/tokens", json={"name": "reader", "scopes": ["read"]})
        plaintext = created.json()["token"]

        identity = await signed_in.get("/me", headers={"Authorization": f"Bearer {plaintext}"})

        assert identity.status_code == 200
        assert identity.json()["label"] == "reader"
        assert identity.json()["scopes"] == ["read"]
        assert identity.json()["channel"] == "api"

    async def test_requires_at_least_one_scope(self, signed_in: AsyncClient) -> None:
        response = await signed_in.post("/tokens", json={"name": "useless", "scopes": []})

        assert response.status_code == 422

    async def test_deduplicates_scopes(self, signed_in: AsyncClient) -> None:
        response = await signed_in.post(
            "/tokens", json={"name": "dupes", "scopes": ["read", "read", "write"]}
        )

        assert response.json()["scopes"] == ["read", "write"]


class TestScopeEnforcement:
    async def test_a_narrow_token_cannot_mint_a_broader_one(self, signed_in: AsyncClient) -> None:
        """The whole point of scopes: an agent cannot escalate itself."""
        created = await signed_in.post(
            "/tokens", json={"name": "board agent", "scopes": ["read", "write"]}
        )
        agent = {"Authorization": f"Bearer {created.json()['token']}"}

        response = await signed_in.post(
            "/tokens", json={"name": "sneaky", "scopes": ["admin"]}, headers=agent
        )

        assert response.status_code == 403
        assert response.json()["error"]["details"]["missing_scopes"] == ["admin"]

    async def test_a_write_token_cannot_read_the_audit_log_without_read(
        self, signed_in: AsyncClient
    ) -> None:
        created = await signed_in.post("/tokens", json={"name": "writer", "scopes": ["write"]})
        writer = {"Authorization": f"Bearer {created.json()['token']}"}

        assert (await signed_in.get("/activity", headers=writer)).status_code == 403


class TestNoTokenOutgrowsItsMinter:
    """CYLIST-117: ``admin`` lets a caller mint, not mint anything at all."""

    async def _bearer(self, client: AsyncClient, *scopes: str) -> dict[str, str]:
        created = await client.post("/tokens", json={"name": "minter", "scopes": list(scopes)})
        return {"Authorization": f"Bearer {created.json()['token']}"}

    async def test_an_admin_token_cannot_mint_scopes_it_lacks(self, signed_in: AsyncClient) -> None:
        admin_only = await self._bearer(signed_in, "admin")

        response = await signed_in.post(
            "/tokens",
            json={"name": "wider", "scopes": ["admin", "vault:reveal", "write"]},
            headers=admin_only,
        )

        assert response.status_code == 403
        assert response.json()["error"]["details"]["missing_scopes"] == ["vault:reveal", "write"]

    async def test_it_can_mint_what_it_holds(self, signed_in: AsyncClient) -> None:
        minter = await self._bearer(signed_in, "admin", "read", "write")

        response = await signed_in.post(
            "/tokens", json={"name": "agent", "scopes": ["read", "write"]}, headers=minter
        )

        assert response.status_code == 201

    async def test_no_api_token_can_grant_permit_even_holding_it(
        self, signed_in: AsyncClient
    ) -> None:
        """``permit`` answers an agent's permission prompt, and agents hold
        bearer tokens — so no bearer token may be what hands it out."""
        everything = await self._bearer(signed_in, "admin", "permit", "read")

        response = await signed_in.post(
            "/tokens", json={"name": "self-approver", "scopes": ["permit"]}, headers=everything
        )

        assert response.status_code == 403
        assert response.json()["error"]["details"]["scope"] == "permit"

    async def test_a_browser_session_still_can(self, signed_in: AsyncClient) -> None:
        """How the phone app gets the scope: it signs in, then mints its own."""
        response = await signed_in.post(
            "/tokens", json={"name": "Pixel (Android)", "scopes": ["read", "write", "permit"]}
        )

        assert response.status_code == 201
        assert "permit" in response.json()["scopes"]


class TestRevocation:
    async def test_a_revoked_token_stops_working_immediately(self, signed_in: AsyncClient) -> None:
        created = await signed_in.post("/tokens", json={"name": "temp", "scopes": ["read"]})
        token_id = created.json()["id"]
        headers = {"Authorization": f"Bearer {created.json()['token']}"}

        assert (await signed_in.get("/me", headers=headers)).status_code == 200

        assert (await signed_in.delete(f"/tokens/{token_id}")).status_code == 200

        assert (await signed_in.get("/me", headers=headers)).status_code == 401

    async def test_revoked_tokens_disappear_from_the_list(self, signed_in: AsyncClient) -> None:
        created = await signed_in.post("/tokens", json={"name": "temp", "scopes": ["read"]})
        await signed_in.delete(f"/tokens/{created.json()['id']}")

        assert (await signed_in.get("/tokens")).json()["items"] == []

    async def test_revoking_an_unknown_token_is_a_clean_404(self, signed_in: AsyncClient) -> None:
        response = await signed_in.delete("/tokens/00000000-0000-7000-8000-000000000000")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


class TestRevokingYourself:
    """Any credential may give itself up; revoking another still needs admin."""

    async def test_a_token_without_admin_can_revoke_itself(self, signed_in: AsyncClient) -> None:
        created = await signed_in.post(
            "/tokens", json={"name": "phone", "scopes": ["read", "write"]}
        )
        headers = {"Authorization": f"Bearer {created.json()['token']}"}

        response = await signed_in.delete(f"/tokens/{created.json()['id']}", headers=headers)

        assert response.status_code == 200
        assert (await signed_in.get("/me", headers=headers)).status_code == 401
        assert (await signed_in.get("/tokens")).json()["items"] == []

    async def test_a_token_without_admin_cannot_revoke_another(
        self, signed_in: AsyncClient
    ) -> None:
        phone = await signed_in.post("/tokens", json={"name": "phone", "scopes": ["read", "write"]})
        other = await signed_in.post("/tokens", json={"name": "agent", "scopes": ["read"]})
        headers = {"Authorization": f"Bearer {phone.json()['token']}"}

        response = await signed_in.delete(f"/tokens/{other.json()['id']}", headers=headers)

        assert response.status_code == 403
        assert response.json()["error"]["details"]["missing_scopes"] == ["admin"]
        listed = [token["name"] for token in (await signed_in.get("/tokens")).json()["items"]]
        assert sorted(listed) == ["agent", "phone"]
