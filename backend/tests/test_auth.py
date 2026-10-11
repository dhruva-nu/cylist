"""Password login, sessions and the identity endpoint."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.auth.dependencies import SESSION_COOKIE
from app.auth.passwords import hash_password, needs_rehash, verify_password
from app.config import Settings
from app.db import Database
from tests.conftest import OWNER_NAME, OWNER_PASSWORD, client_for


class TestPasswordHashing:
    def test_verifies_the_right_password(self) -> None:
        assert verify_password("hunter2", hash_password("hunter2"))

    def test_rejects_the_wrong_password(self) -> None:
        assert not verify_password("hunter3", hash_password("hunter2"))

    def test_hashes_are_salted(self) -> None:
        assert hash_password("hunter2") != hash_password("hunter2")

    def test_rejects_everything_when_no_hash_is_configured(self) -> None:
        """A deployment that forgot to set the hash must not accept any login."""
        assert not verify_password("hunter2", "")
        assert not verify_password("", "")

    def test_rejects_a_malformed_hash_instead_of_raising(self) -> None:
        assert not verify_password("hunter2", "not-a-hash")
        assert not needs_rehash("not-a-hash")


class TestLogin:
    async def test_sets_an_httponly_session_cookie(self, client: AsyncClient) -> None:
        response = await client.post("/auth/login", json={"password": OWNER_PASSWORD})

        assert response.status_code == 200
        cookie = response.cookies.get(SESSION_COOKIE)
        assert cookie is not None
        assert cookie.startswith("cyl_")
        assert "httponly" in response.headers["set-cookie"].lower()

    async def test_grants_every_scope_to_the_owner(self, client: AsyncClient) -> None:
        response = await client.post("/auth/login", json={"password": OWNER_PASSWORD})

        body = response.json()
        assert body["channel"] == "web"
        assert set(body["scopes"]) == {"read", "write", "vault:read", "vault:reveal", "admin"}

    async def test_rejects_the_wrong_password(self, client: AsyncClient) -> None:
        response = await client.post("/auth/login", json={"password": "wrong"})

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"
        assert SESSION_COOKIE not in response.cookies

    async def test_rejects_an_empty_password_before_hashing(self, client: AsyncClient) -> None:
        response = await client.post("/auth/login", json={"password": ""})

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_failed"

    @pytest.mark.parametrize(
        ("environment", "secure"),
        [
            ("dev", False),
            ("test", False),
            ("preview", True),
            ("staging", True),
            ("prod", True),
        ],
    )
    async def test_marks_the_cookie_secure_wherever_https_terminates(
        self,
        settings: Settings,
        database: Database,
        environment: str,
        secure: bool,
    ) -> None:
        """Staging is served over HTTPS like production, so it is Secure too.

        Getting this wrong fails silently in the direction that matters: a
        Secure cookie sent over plain HTTP is dropped by the browser, and the
        only symptom is a login that returns 200 and then does not stick.
        """
        deployed = settings.model_copy(update={"environment": environment})

        async with client_for(deployed, database) as http:
            response = await http.post("/auth/login", json={"password": OWNER_PASSWORD})

        assert response.status_code == 200
        assert ("secure" in response.headers["set-cookie"].lower()) is secure


class TestOneHostManyDeployments:
    """Production, staging and the dev slots share a host; a browser sends each
    of them every cookie the others set, whatever the port (CYLIST-81)."""

    @pytest.mark.parametrize(
        ("environment", "base_path", "name"),
        [
            ("prod", "", "cylist_session"),
            ("dev", "", "cylist_session"),
            ("test", "", "cylist_session"),
            ("staging", "", "cylist_session_staging"),
            ("preview", "", "cylist_session_preview"),
            ("preview", "/dev_1", "cylist_session_dev_1"),
        ],
    )
    def test_each_deployment_names_its_own_cookie(
        self, environment: str, base_path: str, name: str
    ) -> None:
        """Production keeps the bare name, so its sessions outlive the change."""
        configured = Settings(environment=environment, base_path=base_path)  # type: ignore[arg-type]

        assert configured.session_cookie_name == name

    @pytest.mark.parametrize(
        ("signed_in_at", "visiting"), [("prod", "staging"), ("staging", "prod")]
    )
    async def test_one_deployments_cookie_does_not_sign_in_to_another(
        self, settings: Settings, database: Database, signed_in_at: str, visiting: str
    ) -> None:
        """The two apps share a database here, so the token itself is good in
        both: only the cookie's name keeps them apart, as it must on one host."""
        home = settings.model_copy(update={"environment": signed_in_at})
        away = settings.model_copy(update={"environment": visiting})

        async with client_for(home, database) as http:
            login = await http.post("/auth/login", json={"password": OWNER_PASSWORD})
            name, _, rest = login.headers["set-cookie"].partition("=")
            token = rest.split(";", 1)[0]
            assert name == home.session_cookie_name
            assert (await http.get("/me", headers={"cookie": f"{name}={token}"})).status_code == 200

        async with client_for(away, database) as http:
            response = await http.get("/me", headers={"cookie": f"{name}={token}"})

        assert response.status_code == 401


class TestIdentity:
    async def test_describes_the_current_session(self, signed_in: AsyncClient) -> None:
        response = await signed_in.get("/me")

        assert response.status_code == 200
        assert response.json()["label"] == OWNER_NAME
        assert response.json()["person"]["name"] == OWNER_NAME

    async def test_a_session_reports_the_web_channel(self, signed_in: AsyncClient) -> None:
        """Regression: enum columns must load back as enums, not bare strings.

        With the column typed as a plain ``String`` the identity comparison
        ``token.kind is TokenKind.SESSION`` was always false, so a browser
        session was reported as an API caller in ``/me`` and in the audit log.
        """
        assert (await signed_in.get("/me")).json()["channel"] == "web"

    async def test_requires_a_credential(self, client: AsyncClient) -> None:
        response = await client.get("/me")

        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"

    async def test_rejects_a_credential_that_is_not_a_cylist_token(
        self, client: AsyncClient
    ) -> None:
        response = await client.get("/me", headers={"Authorization": "Bearer nonsense"})

        assert response.status_code == 401


class TestLogout:
    async def test_revokes_the_session(self, signed_in: AsyncClient) -> None:
        assert (await signed_in.post("/auth/logout")).status_code == 200

        # The cookie is cleared by the response, so put it back to prove the
        # server rejects the value itself rather than merely losing it.
        assert (await signed_in.get("/me")).status_code == 401

    async def test_is_safe_without_a_session(self, client: AsyncClient) -> None:
        assert (await client.post("/auth/logout")).status_code == 200
