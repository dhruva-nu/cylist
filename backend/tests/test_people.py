"""The people directory."""

from __future__ import annotations

from httpx import AsyncClient

from app.core.palette import PALETTE, colour_for

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

        assert [person["kind"] for person in listed] == ["team", "client"]

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

        assert (await signed_in.get("/people")).json() == []
        assert len((await signed_in.get("/people", params={"include_archived": True})).json()) == 1

    async def test_an_archived_person_can_be_brought_back(self, signed_in: AsyncClient) -> None:
        person = (await signed_in.post("/people", json=ADITI)).json()
        await signed_in.delete(f"/people/{person['id']}")

        restored = (
            await signed_in.patch(f"/people/{person['id']}", json={"archived": False})
        ).json()

        assert restored["archived_at"] is None
        assert len((await signed_in.get("/people")).json()) == 1

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


class TestWhoIsMe:
    """One directory entry stands for the owner, and joins every new project."""

    async def test_nobody_is_me_to_begin_with(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/people", json=ADITI)

        assert (await signed_in.get("/me")).json()["person"] is None

    async def test_a_person_can_be_created_as_me(self, signed_in: AsyncClient) -> None:
        body = (await signed_in.post("/people", json={**ADITI, "is_me": True})).json()

        assert body["is_me"] is True
        assert (await signed_in.get("/me")).json()["person"]["id"] == body["id"]

    async def test_a_person_can_be_marked_as_me_afterwards(self, signed_in: AsyncClient) -> None:
        person = (await signed_in.post("/people", json=ADITI)).json()

        updated = (await signed_in.patch(f"/people/{person['id']}", json={"is_me": True})).json()

        assert updated["is_me"] is True

    async def test_marking_someone_takes_it_off_whoever_had_it(
        self, signed_in: AsyncClient
    ) -> None:
        first = (await signed_in.post("/people", json={**ADITI, "is_me": True})).json()
        second = (await signed_in.post("/people", json={**SANJAY, "is_me": True})).json()

        directory = {
            entry["id"]: entry["is_me"] for entry in (await signed_in.get("/people")).json()
        }

        assert directory[second["id"]] is True
        assert directory[first["id"]] is False

    async def test_it_can_be_given_up_without_naming_a_successor(
        self, signed_in: AsyncClient
    ) -> None:
        person = (await signed_in.post("/people", json={**ADITI, "is_me": True})).json()

        await signed_in.patch(f"/people/{person['id']}", json={"is_me": False})

        assert (await signed_in.get("/me")).json()["person"] is None

    async def test_archiving_me_gives_the_flag_up(self, signed_in: AsyncClient) -> None:
        """An archived person cannot be a member, so they cannot be the owner."""
        person = (await signed_in.post("/people", json={**ADITI, "is_me": True})).json()

        await signed_in.delete(f"/people/{person['id']}")

        assert (await signed_in.get("/me")).json()["person"] is None
