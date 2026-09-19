"""The people directory."""

from __future__ import annotations

from httpx import AsyncClient

from app.config import Settings
from app.core.palette import PALETTE, colour_for
from app.db import Database
from app.models.person import Person
from tests.conftest import INVITEE_PASSWORD, client_for, open_account

ADITI = {
    "name": "Aditi K",
    "kind": "team",
    "role": "Backend engineer",
    "responsibilities": "Payments, Stripe integration and webhook reliability.",
    "email": "aditi@think41.com",
}
SANJAY = {
    "name": "Sanjay F",
    "kind": "client",
    "role": "Finance controller, Atlas",
    "responsibilities": "Approves anything touching tax or vendor accounts.",
}


class TestPalette:
    def test_the_same_name_always_gets_the_same_colour(self) -> None:
        """Colours must survive a restart, so they cannot use salted hash()."""
        assert colour_for("Aditi K") == colour_for("Aditi K")

    def test_colour_ignores_case_and_surrounding_space(self) -> None:
        assert colour_for("  aditi k ") == colour_for("Aditi K")

    def test_every_colour_comes_from_the_palette(self) -> None:
        names = [f"Person {index}" for index in range(200)]
        assert {colour_for(name) for name in names} <= set(PALETTE)


class TestCreating:
    async def test_adds_someone_to_the_directory(self, signed_in: AsyncClient) -> None:
        response = await signed_in.post("/people", json=ADITI)

        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "Aditi K"
        assert body["kind"] == "team"
        assert body["archived_at"] is None

    async def test_assigns_a_palette_colour_when_none_is_given(
        self, signed_in: AsyncClient
    ) -> None:
        body = (await signed_in.post("/people", json=ADITI)).json()

        assert body["colour"] == colour_for("Aditi K")

    async def test_accepts_an_explicit_colour(self, signed_in: AsyncClient) -> None:
        body = (await signed_in.post("/people", json={**ADITI, "colour": "#123ABC"})).json()

        assert body["colour"] == "#123ABC"

    async def test_rejects_a_colour_that_is_not_six_digit_hex(self, signed_in: AsyncClient) -> None:
        response = await signed_in.post("/people", json={**ADITI, "colour": "red"})

        assert response.status_code == 422

    async def test_rejects_a_blank_name(self, signed_in: AsyncClient) -> None:
        """Also a regression test for the error envelope.

        For a custom field validator Pydantic puts the original exception
        object into the error's ``ctx``. Rendering that straight into a
        JSONResponse raised a serialisation error, so a blank name produced a
        crash instead of a 422.
        """
        response = await signed_in.post("/people", json={**ADITI, "name": "   "})

        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "validation_failed"
        assert body["error"]["details"]["fields"][0]["loc"] == ["body", "name"]

    async def test_trims_surrounding_whitespace(self, signed_in: AsyncClient) -> None:
        body = (await signed_in.post("/people", json={**ADITI, "name": "  Aditi K  "})).json()

        assert body["name"] == "Aditi K"

    async def test_rejects_a_malformed_email(self, signed_in: AsyncClient) -> None:
        response = await signed_in.post("/people", json={**ADITI, "email": "not-an-email"})

        assert response.status_code == 422

    async def test_email_is_optional(self, signed_in: AsyncClient) -> None:
        assert (await signed_in.post("/people", json=SANJAY)).status_code == 201


class TestListing:
    async def test_orders_team_before_clients(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/people", json=SANJAY)
        await signed_in.post("/people", json=ADITI)

        listed = (await signed_in.get("/people")).json()

        # Two team members — Aditi and whoever the session belongs to — then
        # the client. Ordering is the assertion; the count is incidental.
        assert [person["kind"] for person in listed] == ["team", "team", "client"]

    async def test_can_be_filtered_to_one_kind(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/people", json=ADITI)
        await signed_in.post("/people", json=SANJAY)

        listed = (await signed_in.get("/people", params={"kind": "client"})).json()

        assert [person["name"] for person in listed] == ["Sanjay F"]


class TestUpdating:
    async def test_changes_only_the_fields_given(self, signed_in: AsyncClient) -> None:
        person = (await signed_in.post("/people", json=ADITI)).json()

        updated = (
            await signed_in.patch(f"/people/{person['id']}", json={"role": "Tech lead"})
        ).json()

        assert updated["role"] == "Tech lead"
        assert updated["name"] == "Aditi K"
        assert updated["responsibilities"] == ADITI["responsibilities"]

    async def test_unknown_person_is_a_clean_404(self, signed_in: AsyncClient) -> None:
        response = await signed_in.patch(
            "/people/00000000-0000-7000-8000-000000000000", json={"role": "x"}
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


class TestArchiving:
    async def test_archived_people_drop_out_of_the_default_list(
        self, signed_in: AsyncClient
    ) -> None:
        person = (await signed_in.post("/people", json=ADITI)).json()

        assert (await signed_in.delete(f"/people/{person['id']}")).status_code == 200

        listed = [entry["name"] for entry in (await signed_in.get("/people")).json()]
        with_archived = (await signed_in.get("/people", params={"include_archived": True})).json()

        assert ADITI["name"] not in listed
        assert ADITI["name"] in [entry["name"] for entry in with_archived]

    async def test_an_archived_person_can_be_brought_back(self, signed_in: AsyncClient) -> None:
        person = (await signed_in.post("/people", json=ADITI)).json()
        await signed_in.delete(f"/people/{person['id']}")

        restored = (
            await signed_in.patch(f"/people/{person['id']}", json={"archived": False})
        ).json()

        assert restored["archived_at"] is None
        assert ADITI["name"] in [entry["name"] for entry in (await signed_in.get("/people")).json()]

    async def test_archiving_twice_keeps_the_original_timestamp(
        self, signed_in: AsyncClient
    ) -> None:
        person = (await signed_in.post("/people", json=ADITI)).json()
        await signed_in.delete(f"/people/{person['id']}")
        first = (await signed_in.get(f"/people/{person['id']}")).json()["archived_at"]

        await signed_in.delete(f"/people/{person['id']}")
        second = (await signed_in.get(f"/people/{person['id']}")).json()["archived_at"]

        assert first == second


class TestScopes:
    async def test_reading_needs_only_read(self, signed_in: AsyncClient) -> None:
        created = await signed_in.post("/tokens", json={"name": "r", "scopes": ["read"]})
        reader = {"Authorization": f"Bearer {created.json()['token']}"}

        assert (await signed_in.get("/people", headers=reader)).status_code == 200

    async def test_writing_is_refused_without_write(self, signed_in: AsyncClient) -> None:
        created = await signed_in.post("/tokens", json={"name": "r", "scopes": ["read"]})
        reader = {"Authorization": f"Bearer {created.json()['token']}"}

        response = await signed_in.post("/people", json=ADITI, headers=reader)

        assert response.status_code == 403
        assert response.json()["error"]["details"]["missing_scopes"] == ["write"]


class TestWhoIsAsking:
    """Who "you" is, now that it is a property of the session rather than a row.

    There used to be a ``person.is_me`` column and a partial unique index
    keeping there to only ever be one of them. Both are gone: with several
    people able to sign in, the directory cannot hold the answer, because the
    answer is different for each of them.
    """

    async def test_me_is_whoever_signed_in(self, signed_in: AsyncClient, owner: Person) -> None:
        body = (await signed_in.get("/me")).json()

        assert body["person"]["id"] == str(owner.id)
        assert body["person"]["name"] == owner.name

    async def test_two_sessions_each_get_their_own_answer(
        self, signed_in: AsyncClient, settings: Settings, database: Database
    ) -> None:
        """The point of the whole change, in one test.

        Two people signed in against the same deployment, each asking who
        they are, and getting different answers — which the old column could
        not have produced however it was read.
        """
        aditi, token = await open_account(signed_in, ADITI, "aditi@cylist.dev")

        async with client_for(settings, database) as other:
            accepted = await other.post(
                "/auth/accept-invite", json={"token": token, "password": INVITEE_PASSWORD}
            )
            assert accepted.status_code == 200, accepted.text

            mine = (await signed_in.get("/me")).json()["person"]
            theirs = (await other.get("/me")).json()["person"]

        assert theirs["id"] == aditi["id"]
        assert mine["id"] != theirs["id"]

    async def test_a_person_is_the_same_person_to_everybody(
        self, signed_in: AsyncClient, owner: Person
    ) -> None:
        """No ``is_me`` on the wire, so one directory entry is one payload.

        Asserted rather than assumed: a field that means something different
        depending on who asked is the thing this design is avoiding, and it
        would be easy to add back by accident.
        """
        listed = (await signed_in.get("/people")).json()

        assert all("is_me" not in entry for entry in listed)

    async def test_the_directory_says_who_can_sign_in(self, signed_in: AsyncClient) -> None:
        added = (await signed_in.post("/people", json=ADITI)).json()

        assert added["has_account"] is False
        assert added["invite_is_pending"] is False

        me = (await signed_in.get("/me")).json()["person"]
        assert me["has_account"] is True
