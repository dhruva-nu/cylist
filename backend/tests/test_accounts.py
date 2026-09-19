"""Accounts: who can sign in, how they come to, and how they stop.

The old model was one password in the environment and one ``person.is_me``
row. These are the tests for what replaced it — an account is a directory
entry with a password, opened by invitation and closed by archiving — and for
the seam between the two, which is the bootstrap login that works only while
nobody has an account yet.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import SESSION_COOKIE
from app.auth.invites import INVITE_PREFIX
from app.config import Settings
from app.core.clock import now
from app.db import Database
from app.models.activity import Activity
from app.models.api_token import ApiToken
from app.models.person import Person
from tests.conftest import (
    INVITEE_PASSWORD,
    OWNER_EMAIL,
    OWNER_NAME,
    OWNER_PASSWORD,
    client_for,
    open_account,
)

ADITI = {
    "name": "Aditi K",
    "kind": "team",
    "title": "Backend engineer",
    "responsibilities": "Payments and webhooks.",
}
SANJAY = {
    "name": "Sanjay F",
    "kind": "client",
    "title": "Finance controller, Atlas",
    "responsibilities": "Approves tax and vendor accounts.",
}
ADITI_EMAIL = "aditi@cylist.dev"


async def _accept(client: AsyncClient, token: str, password: str = INVITEE_PASSWORD) -> AsyncClient:
    response = await client.post("/auth/accept-invite", json={"token": token, "password": password})
    assert response.status_code == 200, response.text
    return client


class TestInviting:
    async def test_mints_a_one_time_token(self, signed_in: AsyncClient) -> None:
        person, token = await open_account(signed_in, ADITI, ADITI_EMAIL)

        assert token.startswith(INVITE_PREFIX)
        fetched = (await signed_in.get(f"/people/{person['id']}")).json()
        assert fetched["invite_is_pending"] is True
        assert fetched["has_account"] is False

    async def test_the_link_is_built_from_the_request(self, signed_in: AsyncClient) -> None:
        person = (await signed_in.post("/people", json={**ADITI, "email": ADITI_EMAIL})).json()

        issued = (await signed_in.post(f"/people/{person['id']}/invite")).json()

        assert issued["url"].endswith(f"/invite/{issued['token']}")

    async def test_the_token_is_never_stored_in_the_clear(
        self, signed_in: AsyncClient, session: AsyncSession
    ) -> None:
        """It is a credential, so the database gets the digest and nothing else."""
        person, token = await open_account(signed_in, ADITI, ADITI_EMAIL)

        stored = await session.get(Person, person["id"])
        assert stored is not None
        assert stored.invite_token_hash is not None
        assert token not in (stored.invite_token_hash or "")

    async def test_the_token_is_not_in_the_audit_trail(self, signed_in: AsyncClient) -> None:
        """The trail is readable with `read` alone, so it must hold no secrets."""
        _, token = await open_account(signed_in, ADITI, ADITI_EMAIL)

        feed = (await signed_in.get("/activity")).text

        assert token not in feed

    async def test_re_inviting_replaces_the_outstanding_link(
        self, signed_in: AsyncClient, client: AsyncClient
    ) -> None:
        person, first = await open_account(signed_in, ADITI, ADITI_EMAIL)

        second = (await signed_in.post(f"/people/{person['id']}/invite")).json()["token"]

        assert second != first
        stale = await client.post(
            "/auth/accept-invite", json={"token": first, "password": INVITEE_PASSWORD}
        )
        assert stale.status_code == 422

    async def test_withdrawing_it_makes_the_link_dead(
        self, signed_in: AsyncClient, client: AsyncClient
    ) -> None:
        person, token = await open_account(signed_in, ADITI, ADITI_EMAIL)

        assert (await signed_in.delete(f"/people/{person['id']}/invite")).status_code == 200

        refused = await client.post(
            "/auth/accept-invite", json={"token": token, "password": INVITEE_PASSWORD}
        )
        assert refused.status_code == 422
        assert (await signed_in.get(f"/people/{person['id']}")).json()["invite_is_pending"] is False

    async def test_a_client_cannot_be_given_an_account(self, signed_in: AsyncClient) -> None:
        """Clients are named on the work, not signed in to it."""
        person = (await signed_in.post("/people", json={**SANJAY, "email": "s@cylist.dev"})).json()

        response = await signed_in.post(f"/people/{person['id']}/invite")

        assert response.status_code == 422

    async def test_somebody_with_no_email_cannot_be_invited(self, signed_in: AsyncClient) -> None:
        """The address is the username, so there is nothing to invite them as."""
        person = (await signed_in.post("/people", json=ADITI)).json()

        response = await signed_in.post(f"/people/{person['id']}/invite")

        assert response.status_code == 422

    async def test_an_archived_person_cannot_be_invited(self, signed_in: AsyncClient) -> None:
        person = (await signed_in.post("/people", json={**ADITI, "email": ADITI_EMAIL})).json()
        await signed_in.delete(f"/people/{person['id']}")

        response = await signed_in.post(f"/people/{person['id']}/invite")

        assert response.status_code == 422

    async def test_somebody_who_already_has_an_account_is_a_conflict(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        person, token = await open_account(signed_in, ADITI, ADITI_EMAIL)
        await _accept(other_client, token)

        response = await signed_in.post(f"/people/{person['id']}/invite")

        assert response.status_code == 409

    async def test_two_accounts_cannot_share_an_address(self, signed_in: AsyncClient) -> None:
        """The address is what login looks somebody up by, so it has to be theirs."""
        await open_account(signed_in, ADITI, ADITI_EMAIL)
        other = (await signed_in.post("/people", json={**ADITI, "name": "Aditi B"})).json()
        await signed_in.patch(f"/people/{other['id']}", json={"email": ADITI_EMAIL.upper()})

        response = await signed_in.post(f"/people/{other['id']}/invite")

        assert response.status_code == 409
        assert ADITI_EMAIL in response.json()["error"]["message"].lower()

    async def test_two_people_who_cannot_sign_in_may_share_one(
        self, signed_in: AsyncClient
    ) -> None:
        """A shared ``support@`` against two client contacts is a real thing."""
        shared = {"email": "support@cylist.dev"}

        first = await signed_in.post("/people", json={**SANJAY, **shared})
        second = await signed_in.post("/people", json={**SANJAY, "name": "Ravi M", **shared})

        assert first.status_code == 201
        assert second.status_code == 201

    async def test_inviting_needs_admin_not_write(
        self, signed_in: AsyncClient, client: AsyncClient
    ) -> None:
        """`write` is what a board agent is given; handing out accounts is not it."""
        person = (await signed_in.post("/people", json={**ADITI, "email": ADITI_EMAIL})).json()
        issued = (
            await signed_in.post("/tokens", json={"name": "board agent", "scopes": ["write"]})
        ).json()

        response = await client.post(
            f"/people/{person['id']}/invite",
            headers={"Authorization": f"Bearer {issued['token']}"},
        )

        assert response.status_code == 403


class TestAcceptingAnInvitation:
    async def test_sets_a_password_and_signs_them_in(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        person, token = await open_account(signed_in, ADITI, ADITI_EMAIL)

        response = await other_client.post(
            "/auth/accept-invite", json={"token": token, "password": INVITEE_PASSWORD}
        )

        assert response.status_code == 200
        assert response.cookies.get(SESSION_COOKIE) is not None
        assert response.json()["person"]["id"] == person["id"]
        assert (await other_client.get("/me")).json()["person"]["name"] == "Aditi K"
        # And the admin who sent the invitation is still themselves.
        assert (await signed_in.get("/me")).json()["person"]["name"] == OWNER_NAME

    async def test_the_link_works_exactly_once(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        _, token = await open_account(signed_in, ADITI, ADITI_EMAIL)
        await _accept(other_client, token)

        again = await other_client.post(
            "/auth/accept-invite", json={"token": token, "password": "a-different-password"}
        )

        assert again.status_code == 422

    async def test_an_expired_invitation_is_refused(
        self, signed_in: AsyncClient, client: AsyncClient, session: AsyncSession
    ) -> None:
        person, token = await open_account(signed_in, ADITI, ADITI_EMAIL)
        stored = await session.get(Person, person["id"])
        assert stored is not None
        stored.invite_expires_at = now() - timedelta(seconds=1)
        await session.commit()

        response = await client.post(
            "/auth/accept-invite", json={"token": token, "password": INVITEE_PASSWORD}
        )

        assert response.status_code == 422

    async def test_an_unknown_token_is_refused(self, client: AsyncClient) -> None:
        response = await client.post(
            "/auth/accept-invite",
            json={"token": f"{INVITE_PREFIX}made-up", "password": INVITEE_PASSWORD},
        )

        assert response.status_code == 422

    async def test_a_short_password_is_refused_before_anything_is_written(
        self, signed_in: AsyncClient, client: AsyncClient
    ) -> None:
        person, token = await open_account(signed_in, ADITI, ADITI_EMAIL)

        response = await client.post(
            "/auth/accept-invite", json={"token": token, "password": "short"}
        )

        assert response.status_code == 422
        assert (await signed_in.get(f"/people/{person['id']}")).json()["invite_is_pending"] is True


class TestSigningIn:
    async def test_an_account_signs_in_with_its_email(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        _, token = await open_account(signed_in, ADITI, ADITI_EMAIL)
        await _accept(other_client, token)
        await other_client.post("/auth/logout")

        response = await other_client.post(
            "/auth/login", json={"email": ADITI_EMAIL, "password": INVITEE_PASSWORD}
        )

        assert response.status_code == 200
        assert response.json()["person"]["name"] == "Aditi K"

    async def test_the_address_is_matched_without_case(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        """Nobody who typed their address with a capital thinks it is a different one.

        ``signed_in`` is what puts the account in the directory; the login
        under test is the one made from the other browser.
        """
        response = await other_client.post(
            "/auth/login", json={"email": OWNER_EMAIL.upper(), "password": OWNER_PASSWORD}
        )

        assert response.status_code == 200
        assert response.json()["person"]["name"] == OWNER_NAME

    async def test_surrounding_space_is_not_a_different_address(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        response = await other_client.post(
            "/auth/login", json={"email": f"  {OWNER_EMAIL}  ", "password": OWNER_PASSWORD}
        )

        assert response.status_code == 200

    @pytest.mark.parametrize(
        ("email", "password"),
        [
            (OWNER_EMAIL, "not-the-password"),
            ("nobody@cylist.dev", OWNER_PASSWORD),
        ],
    )
    async def test_a_wrong_address_and_a_wrong_password_read_the_same(
        self, signed_in: AsyncClient, client: AsyncClient, email: str, password: str
    ) -> None:
        """Otherwise the error is a way to find out who has an account here."""
        response = await client.post("/auth/login", json={"email": email, "password": password})

        assert response.status_code == 401
        assert response.json()["error"]["message"] == (
            "That email and password do not match an account."
        )

    async def test_an_archived_person_cannot_sign_in(
        self, signed_in: AsyncClient, client: AsyncClient, owner: Person
    ) -> None:
        await signed_in.delete(f"/people/{owner.id}")

        response = await client.post(
            "/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
        )

        assert response.status_code == 401

    async def test_somebody_with_no_password_cannot_sign_in(
        self, signed_in: AsyncClient, client: AsyncClient
    ) -> None:
        """Every client, and every teammate who has not accepted their invite."""
        await signed_in.post("/people", json={**ADITI, "email": ADITI_EMAIL})

        response = await client.post(
            "/auth/login", json={"email": ADITI_EMAIL, "password": INVITEE_PASSWORD}
        )

        assert response.status_code == 401


class TestTheBootstrapLogin:
    async def test_opens_a_deployment_that_has_no_accounts(self, bootstrapped: AsyncClient) -> None:
        body = (await bootstrapped.get("/me")).json()

        assert body["person"] is None
        assert set(body["scopes"]) == {"read", "write", "vault:read", "vault:reveal", "admin"}

    async def test_setup_says_whether_it_is_still_available(
        self, client: AsyncClient, signed_in: AsyncClient
    ) -> None:
        """What the sign-in screen reads to know which form to draw."""
        assert (await signed_in.get("/setup")).json()["has_accounts"] is True

    async def test_setup_says_so_before_anybody_has_one(self, client: AsyncClient) -> None:
        assert (await client.get("/setup")).json()["has_accounts"] is False

    async def test_shuts_for_good_once_somebody_has_an_account(
        self, bootstrapped: AsyncClient, client: AsyncClient
    ) -> None:
        person = (await bootstrapped.post("/people", json={**ADITI, "email": ADITI_EMAIL})).json()
        token = (await bootstrapped.post(f"/people/{person['id']}/invite")).json()["token"]
        await _accept(client, token)

        response = await client.post("/auth/login", json={"password": OWNER_PASSWORD})

        assert response.status_code == 401

    async def test_it_is_still_the_configured_password(self, client: AsyncClient) -> None:
        response = await client.post("/auth/login", json={"password": "not-it"})

        assert response.status_code == 401

    async def test_its_trail_says_it_belongs_to_nobody(
        self, bootstrapped: AsyncClient, session: AsyncSession
    ) -> None:
        entry = await session.scalar(select(Activity).where(Activity.verb == "session.started"))

        assert entry is not None
        assert entry.actor_person_id is None
        assert entry.actor_label == "Bootstrap session"

    async def test_it_cannot_change_a_password_it_does_not_have(
        self, bootstrapped: AsyncClient
    ) -> None:
        response = await bootstrapped.post(
            "/auth/password",
            json={"current_password": OWNER_PASSWORD, "new_password": INVITEE_PASSWORD},
        )

        assert response.status_code == 422


class TestArchivingWithdrawsAccess:
    async def test_their_session_stops_working_at_once(
        self, signed_in: AsyncClient, settings: Settings, database: Database
    ) -> None:
        """A cookie lasts a month by default. Leaving has to beat it."""
        person, token = await open_account(signed_in, ADITI, ADITI_EMAIL)

        async with client_for(settings, database) as theirs:
            await _accept(theirs, token)
            assert (await theirs.get("/me")).status_code == 200

            await signed_in.delete(f"/people/{person['id']}")

            assert (await theirs.get("/me")).status_code == 401

    async def test_their_agents_token_stops_working_too(
        self, signed_in: AsyncClient, client: AsyncClient, settings: Settings, database: Database
    ) -> None:
        person, invite = await open_account(signed_in, ADITI, ADITI_EMAIL)

        async with client_for(settings, database) as theirs:
            await _accept(theirs, invite)
            agent = (
                await theirs.post("/tokens", json={"name": "their agent", "scopes": ["read"]})
            ).json()["token"]

        assert (
            await client.get("/projects", headers={"Authorization": f"Bearer {agent}"})
        ).status_code == 200

        await signed_in.delete(f"/people/{person['id']}")

        assert (
            await client.get("/projects", headers={"Authorization": f"Bearer {agent}"})
        ).status_code == 401

    async def test_bringing_them_back_does_not_need_a_new_invitation(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        """The password survives; only the credentials made from it are revoked."""
        person, token = await open_account(signed_in, ADITI, ADITI_EMAIL)
        await _accept(other_client, token)
        await signed_in.delete(f"/people/{person['id']}")

        await signed_in.patch(f"/people/{person['id']}", json={"archived": False})

        response = await other_client.post(
            "/auth/login", json={"email": ADITI_EMAIL, "password": INVITEE_PASSWORD}
        )
        assert response.status_code == 200


class TestChangingAPassword:
    async def test_replaces_it_and_keeps_this_browser_signed_in(
        self, signed_in: AsyncClient
    ) -> None:
        response = await signed_in.post(
            "/auth/password",
            json={"current_password": OWNER_PASSWORD, "new_password": INVITEE_PASSWORD},
        )

        assert response.status_code == 200
        assert (await signed_in.get("/me")).status_code == 200

    async def test_every_other_session_is_revoked(
        self, signed_in: AsyncClient, settings: Settings, database: Database
    ) -> None:
        """The point of changing a password under suspicion."""
        async with client_for(settings, database) as elsewhere:
            await elsewhere.post(
                "/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
            )
            assert (await elsewhere.get("/me")).status_code == 200

            await signed_in.post(
                "/auth/password",
                json={"current_password": OWNER_PASSWORD, "new_password": INVITEE_PASSWORD},
            )

            assert (await elsewhere.get("/me")).status_code == 401

    async def test_the_new_password_is_what_works_afterwards(
        self, signed_in: AsyncClient, client: AsyncClient
    ) -> None:
        await signed_in.post(
            "/auth/password",
            json={"current_password": OWNER_PASSWORD, "new_password": INVITEE_PASSWORD},
        )

        stale = await client.post(
            "/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
        )
        fresh = await client.post(
            "/auth/login", json={"email": OWNER_EMAIL, "password": INVITEE_PASSWORD}
        )

        assert stale.status_code == 401
        assert fresh.status_code == 200

    async def test_the_wrong_current_password_is_refused(self, signed_in: AsyncClient) -> None:
        response = await signed_in.post(
            "/auth/password",
            json={"current_password": "not-it", "new_password": INVITEE_PASSWORD},
        )

        assert response.status_code == 401

    async def test_a_short_new_password_is_refused(self, signed_in: AsyncClient) -> None:
        response = await signed_in.post(
            "/auth/password", json={"current_password": OWNER_PASSWORD, "new_password": "short"}
        )

        assert response.status_code == 422


class TestAttribution:
    async def test_an_agents_token_acts_as_whoever_minted_it(
        self, signed_in: AsyncClient, client: AsyncClient, owner: Person, session: AsyncSession
    ) -> None:
        """ "Who moved this card?" has to name a person once there are several."""
        agent = (
            await signed_in.post("/tokens", json={"name": "board agent", "scopes": ["read"]})
        ).json()

        stored = await session.get(ApiToken, agent["id"])
        assert stored is not None
        assert stored.person_id == owner.id
        assert agent["person_id"] == str(owner.id)

    async def test_the_trail_records_the_person_behind_the_credential(
        self, signed_in: AsyncClient, owner: Person, session: AsyncSession
    ) -> None:
        await signed_in.post("/projects", json={"key": "ATL", "name": "Atlas"})

        entry = await session.scalar(select(Activity).where(Activity.verb == "project.created"))

        assert entry is not None
        assert entry.actor_person_id == owner.id
        assert entry.actor_label == OWNER_NAME

    async def test_two_people_are_told_apart_in_one_feed(
        self, signed_in: AsyncClient, settings: Settings, database: Database
    ) -> None:
        _, token = await open_account(signed_in, ADITI, ADITI_EMAIL)
        await signed_in.post("/projects", json={"key": "ATL", "name": "Atlas"})

        async with client_for(settings, database) as theirs:
            await _accept(theirs, token)
            await theirs.post("/projects", json={"key": "HRM", "name": "Hermes"})

        feed = (await signed_in.get("/activity")).json()
        started = {
            entry["payload"].get("name"): entry["actor_label"]
            for entry in feed
            if entry["verb"] == "project.created"
        }

        assert started == {"Atlas": OWNER_NAME, "Hermes": "Aditi K"}


class TestTheDirectoryAndTheAccount:
    async def test_adding_somebody_does_not_give_them_one(self, signed_in: AsyncClient) -> None:
        """Being in the directory and being able to sign in are separate facts."""
        person = (await signed_in.post("/people", json={**ADITI, "email": ADITI_EMAIL})).json()

        assert person["has_account"] is False

    async def test_a_password_is_never_returned(self, signed_in: AsyncClient) -> None:
        listed = (await signed_in.get("/people")).text

        assert "password_hash" not in listed
        assert "invite_token_hash" not in listed

    async def test_one_person_holds_one_account(
        self, signed_in: AsyncClient, session: AsyncSession
    ) -> None:
        """There is no separate user table to drift out of step with this one."""
        count = await session.scalar(
            select(func.count()).select_from(Person).where(Person.password_hash.is_not(None))
        )

        assert count == 1
