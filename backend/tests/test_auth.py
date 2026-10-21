"""Password login, sessions and the identity endpoint."""

from __future__ import annotations

import asyncio

import pytest
from httpx import AsyncClient

from app.auth.dependencies import SESSION_COOKIE
from app.auth.passwords import (
    dummy_hash,
    hash_password,
    hash_password_sync,
    needs_rehash,
    verify_password,
    verify_password_sync,
)
from app.auth.throttle import ACCOUNT_BURST, SignInThrottle
from app.config import Settings
from app.core.ratelimit import TokenBuckets
from app.db import Database
from app.services import people, tokens
from tests.conftest import (
    INVITEE_PASSWORD,
    OWNER_EMAIL,
    OWNER_NAME,
    OWNER_PASSWORD,
    client_for,
    client_on,
    open_account,
    sign_in,
)

ADITI = {
    "name": "Aditi K",
    "kind": "team",
    "title": "Backend engineer",
    "responsibilities": "Payments and webhooks.",
}


def _bearer(client: AsyncClient) -> dict[str, str]:
    """The session cookie this client holds, as a header another request can
    carry — so a test can keep using a session after the jar has replaced it."""
    return {"cookie": f"{SESSION_COOKIE}={client.cookies[SESSION_COOKIE]}"}


def _no_throttle(client: AsyncClient) -> None:
    """Let a test sign in as often as it likes."""
    client.app.state.sign_in_throttle = SignInThrottle(  # type: ignore[attr-defined]
        address_burst=10_000, account_burst=10_000
    )


class TestPasswordHashing:
    def test_verifies_the_right_password(self) -> None:
        assert verify_password_sync("hunter2", hash_password_sync("hunter2"))

    def test_rejects_the_wrong_password(self) -> None:
        assert not verify_password_sync("hunter3", hash_password_sync("hunter2"))

    def test_hashes_are_salted(self) -> None:
        assert hash_password_sync("hunter2") != hash_password_sync("hunter2")

    def test_rejects_everything_when_no_hash_is_configured(self) -> None:
        """A deployment that forgot to set the hash must not accept any login."""
        assert not verify_password_sync("hunter2", "")
        assert not verify_password_sync("", "")

    def test_rejects_a_malformed_hash_instead_of_raising(self) -> None:
        assert not verify_password_sync("hunter2", "not-a-hash")
        assert not needs_rehash("not-a-hash")

    async def test_the_async_pair_agree_with_the_sync_pair(self) -> None:
        """The request path hashes in a thread (CYLIST-121); same answers."""
        hashed = await hash_password("hunter2")
        assert verify_password_sync("hunter2", hashed)
        assert await verify_password("hunter2", hash_password_sync("hunter2"))
        assert not await verify_password("hunter3", hashed)
        assert not await verify_password("hunter2", None)

    def test_the_dummy_hash_is_a_real_hash_nothing_matches(self) -> None:
        assert dummy_hash() == dummy_hash()
        assert dummy_hash().startswith("$argon2id$")
        assert not verify_password_sync("", dummy_hash())
        assert not verify_password_sync("hunter2", dummy_hash())


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
        assert set(body["scopes"]) == {
            "read",
            "write",
            "vault:read",
            "vault:reveal",
            "admin",
            "permit",
        }

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


class TestLoginIsBounded:
    """CYLIST-121: what a stranger can make the login endpoint spend."""

    async def test_a_password_longer_than_the_ceiling_is_refused_unhashed(
        self, client: AsyncClient
    ) -> None:
        response = await client.post("/auth/login", json={"password": "x" * 257})

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_failed"

    async def test_a_password_at_the_ceiling_is_merely_wrong(self, client: AsyncClient) -> None:
        response = await client.post("/auth/login", json={"password": "x" * 256})

        assert response.status_code == 401

    async def test_an_unknown_email_is_checked_against_a_real_hash(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """So that a wrong address costs what a wrong password costs."""
        checked: list[str | None] = []
        real = people.verify_password

        async def recording(plaintext: str, expected: str | None) -> bool:
            checked.append(expected)
            return await real(plaintext, expected)

        monkeypatch.setattr(people, "verify_password", recording)
        await sign_in(client)  # so the directory has accounts and the email path runs
        checked.clear()

        response = await client.post(
            "/auth/login", json={"email": "nobody@cylist.dev", "password": OWNER_PASSWORD}
        )

        assert response.status_code == 401
        assert checked == [dummy_hash()]


class TestTokenBuckets:
    def test_a_burst_then_a_wait_then_one_more(self) -> None:
        clock = [0.0]
        buckets = TokenBuckets(burst=3, per_minute=60, clock=lambda: clock[0])

        assert [buckets.take("k") for _ in range(3)] == [None, None, None]
        assert buckets.take("k") == 1.0  # one token a second: a whole second away
        clock[0] += 1.0
        assert buckets.take("k") is None
        assert buckets.take("k") == 1.0

    def test_keys_are_independent(self) -> None:
        buckets = TokenBuckets(burst=1, per_minute=1)

        assert buckets.take("a") is None
        assert buckets.take("a") is not None
        assert buckets.take("b") is None

    def test_the_wait_is_whole_seconds_and_never_zero(self) -> None:
        buckets = TokenBuckets(burst=1, per_minute=600)  # ten a second

        buckets.take("k")
        assert buckets.take("k") == 1.0

    def test_full_buckets_are_swept_rather_than_kept_forever(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.core import ratelimit

        monkeypatch.setattr(ratelimit, "MAX_KEYS", 3)
        clock = [0.0]
        buckets = TokenBuckets(burst=1, per_minute=60, clock=lambda: clock[0])
        for key in ("a", "b", "c"):
            buckets.take(key)
        clock[0] += 5.0  # all three refilled

        buckets.take("d")

        assert set(buckets._buckets) == {"d"}


class TestSignInIsThrottled:
    """Repeated attempts get a 429 with ``Retry-After``, before any hashing."""

    async def test_an_account_can_be_tried_only_so_often(self, client: AsyncClient) -> None:
        for _ in range(ACCOUNT_BURST):
            refused = await client.post(
                "/auth/login", json={"email": OWNER_EMAIL, "password": "wrong-every-time"}
            )
            assert refused.status_code == 401

        response = await client.post(
            "/auth/login", json={"email": OWNER_EMAIL, "password": "wrong-every-time"}
        )

        assert response.status_code == 429
        assert response.json()["error"]["code"] == "rate_limited"
        assert response.headers["retry-after"].isdigit()
        assert int(response.headers["retry-after"]) >= 1
        assert response.json()["error"]["details"]["retry_after"] == int(
            response.headers["retry-after"]
        )

    async def test_the_right_password_is_refused_too_while_throttled(
        self, client: AsyncClient
    ) -> None:
        """A limiter the right password walks through is one an attacker's
        guesses walk through as well — the check must come first."""
        await sign_in(client)
        for _ in range(ACCOUNT_BURST):
            await client.post("/auth/login", json={"email": OWNER_EMAIL, "password": "wrong"})

        response = await client.post(
            "/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
        )

        assert response.status_code == 429

    async def test_the_bootstrap_login_is_an_account_like_any_other(
        self, client: AsyncClient
    ) -> None:
        for _ in range(ACCOUNT_BURST):
            await client.post("/auth/login", json={"password": "wrong"})

        assert (await client.post("/auth/login", json={"password": "wrong"})).status_code == 429

    async def test_one_account_being_hammered_does_not_lock_another(
        self, client: AsyncClient
    ) -> None:
        for _ in range(ACCOUNT_BURST + 1):
            await client.post("/auth/login", json={"email": "a@cylist.dev", "password": "wrong"})

        response = await client.post(
            "/auth/login", json={"email": "b@cylist.dev", "password": "wrong"}
        )

        assert response.status_code == 401

    async def test_an_address_can_knock_only_so_often_whoever_it_names(
        self, client: AsyncClient
    ) -> None:
        client.app.state.sign_in_throttle = SignInThrottle(  # type: ignore[attr-defined]
            address_burst=2, address_per_minute=1
        )
        for n in range(2):
            await client.post("/auth/login", json={"email": f"{n}@cylist.dev", "password": "wrong"})

        response = await client.post(
            "/auth/login", json={"email": "9@cylist.dev", "password": "wrong"}
        )

        assert response.status_code == 429
        assert "address" in response.json()["error"]["message"]
        # The address refused it, so the account was not charged.
        assert int(response.headers["retry-after"]) >= 1

    async def test_accepting_invitations_is_throttled_by_address(self, client: AsyncClient) -> None:
        client.app.state.sign_in_throttle = SignInThrottle(  # type: ignore[attr-defined]
            address_burst=1, address_per_minute=1
        )
        body = {"token": "cylinv_made-up", "password": INVITEE_PASSWORD}
        assert (await client.post("/auth/accept-invite", json=body)).status_code == 422

        assert (await client.post("/auth/accept-invite", json=body)).status_code == 429


class TestOwnSessions:
    """CYLIST-143: a person can see and end their own sessions."""

    async def test_lists_live_sessions_newest_first_marking_this_one(
        self, signed_in: AsyncClient
    ) -> None:
        first = _bearer(signed_in)
        await signed_in.get("/me", headers=first)  # so it has been used
        async with client_on(signed_in.app) as second:  # type: ignore[attr-defined]
            await sign_in(second)
            listed = await second.get("/me/sessions")

        assert listed.status_code == 200
        sessions = listed.json()
        assert [s["current"] for s in sessions] == [True, False]
        assert all(s["channel"] == "web" for s in sessions)
        assert sessions[1]["last_used_at"] is not None
        assert "token" not in sessions[0] and "token_hash" not in sessions[0]

    async def test_lists_only_sessions_not_api_tokens(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/tokens", json={"name": "agent", "scopes": ["read"]})

        assert len((await signed_in.get("/me/sessions")).json()) == 1

    async def test_ending_a_session_signs_that_browser_out(self, signed_in: AsyncClient) -> None:
        first = _bearer(signed_in)
        async with client_on(signed_in.app) as second:  # type: ignore[attr-defined]
            await sign_in(second)
            other = next(s for s in (await second.get("/me/sessions")).json() if not s["current"])

            ended = await second.delete(f"/me/sessions/{other['id']}")

            assert ended.status_code == 200
            assert (await signed_in.get("/me", headers=first)).status_code == 401
            assert (await second.get("/me")).status_code == 200
            assert len((await second.get("/me/sessions")).json()) == 1

    async def test_ending_this_session_is_a_logout(self, signed_in: AsyncClient) -> None:
        mine = (await signed_in.get("/me/sessions")).json()[0]

        assert (await signed_in.delete(f"/me/sessions/{mine['id']}")).status_code == 200
        assert (await signed_in.get("/me")).status_code == 401

    async def test_somebody_elses_session_is_not_yours_to_end(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        _, token = await open_account(signed_in, ADITI, "aditi@cylist.dev")
        accepted = await other_client.post(
            "/auth/accept-invite", json={"token": token, "password": INVITEE_PASSWORD}
        )
        assert accepted.status_code == 200, accepted.text
        theirs = (await other_client.get("/me/sessions")).json()[0]["id"]

        response = await signed_in.delete(f"/me/sessions/{theirs}")

        assert response.status_code == 404
        assert (await other_client.get("/me")).status_code == 200

    async def test_the_bootstrap_session_has_none(self, bootstrapped: AsyncClient) -> None:
        assert (await bootstrapped.get("/me/sessions")).status_code == 403
        assert (
            await bootstrapped.delete("/me/sessions/00000000-0000-7000-8000-000000000000")
        ).status_code == 403

    async def test_an_api_token_sees_its_owners_sessions(self, signed_in: AsyncClient) -> None:
        """The phone app lists sessions with the token it minted from one."""
        created = await signed_in.post("/tokens", json={"name": "Pixel", "scopes": ["read"]})
        headers = {"Authorization": f"Bearer {created.json()['token']}"}

        listed = await signed_in.get("/me/sessions", headers=headers)

        assert listed.status_code == 200
        assert [s["current"] for s in listed.json()] == [False]

    async def test_a_login_retires_the_session_the_browser_held(
        self, signed_in: AsyncClient
    ) -> None:
        first = _bearer(signed_in)

        again = await signed_in.post(
            "/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
        )

        assert again.status_code == 200
        assert (await signed_in.get("/me", headers=first)).status_code == 401
        assert (await signed_in.get("/me")).status_code == 200
        assert len((await signed_in.get("/me/sessions")).json()) == 1

    async def test_a_login_leaves_an_api_token_in_the_cookie_alone(
        self, signed_in: AsyncClient
    ) -> None:
        created = await signed_in.post("/tokens", json={"name": "Pixel", "scopes": ["read"]})
        plaintext = created.json()["token"]

        again = await signed_in.post(
            "/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
            headers={"cookie": f"{SESSION_COOKIE}={plaintext}"},
        )

        assert again.status_code == 200
        bearer = {"Authorization": f"Bearer {plaintext}"}
        assert (await signed_in.get("/me", headers=bearer)).status_code == 200

    async def test_live_sessions_are_capped_oldest_first(self, client: AsyncClient) -> None:
        _no_throttle(client)
        cookies: list[dict[str, str]] = []
        for _ in range(tokens.MAX_SESSIONS + 2):
            async with client_on(client.app) as browser:  # type: ignore[attr-defined]
                await sign_in(browser)
                cookies.append(_bearer(browser))

        listed = await client.get("/me/sessions", headers=cookies[-1])
        assert len(listed.json()) == tokens.MAX_SESSIONS
        statuses = await asyncio.gather(*(client.get("/me", headers=cookie) for cookie in cookies))
        assert [r.status_code for r in statuses] == [401, 401] + [200] * tokens.MAX_SESSIONS
