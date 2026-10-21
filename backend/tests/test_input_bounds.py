"""Input that used to get past validation and fail at the database (CYLIST-137).

Every one of these was a valid-looking request that ended as a 500 with its
transaction rolled back: a string longer than its column, or a reference whose
"number" Python or Postgres could not read. Each is now refused for what it is.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.services.references import goal_reference, task_reference
from tests.conftest import put_members

ATLAS = {"key": "ATL", "name": "Atlas Billing Migration"}
TEAM = {
    "name": "Aditi K",
    "kind": "team",
    "title": "Backend engineer",
    "responsibilities": "Payments and webhooks.",
}
TOO_LONG_LABEL = "x" * 61
"""One past ``SUB_STAGE_LABEL_MAX_LENGTH``."""


async def _project(client: AsyncClient) -> str:
    await client.post("/projects", json=ATLAS)
    person = (await client.post("/people", json=TEAM)).json()["id"]
    await put_members(client, "ATL", [person])
    return str(person)


def _card(person: str, **extra: object) -> dict[str, object]:
    return {
        "title": "Stripe webhook idempotency",
        "description": "Duplicate deliveries create double payments.",
        "type": "bug",
        "assignee_id": person,
        **extra,
    }


class TestReferences:
    @pytest.mark.parametrize(
        "reference",
        [
            "ATL-²",  # str.isdigit says yes; int() says no
            "ATL-1²",
            "ATL-٣",  # an Arabic-Indic three: int() reads it, nobody typed it
            "ATL-" + "9" * 5000,  # past Python's int-from-string limit
            "ATL-3000000000",  # past int32, which Postgres refuses to compare
            "ATL-1-3000000000",
            "ATL-1-²",
        ],
    )
    async def test_an_unreadable_task_reference_is_not_found(
        self, signed_in: AsyncClient, reference: str
    ) -> None:
        await _project(signed_in)

        response = await signed_in.get(f"/tasks/{reference}")

        assert response.status_code == 404, response.text

    @pytest.mark.parametrize("reference", ["ATL-G²", "ATL-G" + "9" * 5000, "ATL-G3000000000"])
    async def test_an_unreadable_goal_reference_is_not_found(
        self, signed_in: AsyncClient, reference: str
    ) -> None:
        await _project(signed_in)

        response = await signed_in.get(f"/goals/{reference}")

        assert response.status_code == 404, response.text

    async def test_a_reference_one_level_too_deep_says_why(self, signed_in: AsyncClient) -> None:
        await _project(signed_in)

        response = await signed_in.get("/tasks/ATL-1-2-3")

        assert response.status_code == 404
        assert "one level deep" in response.json()["error"]["message"]

    async def test_a_real_reference_still_resolves(self, signed_in: AsyncClient) -> None:
        person = await _project(signed_in)
        card = (await signed_in.post("/projects/ATL/tasks", json=_card(person))).json()

        response = await signed_in.get("/tasks/atl-1")

        assert response.status_code == 200
        assert response.json()["id"] == card["id"]

    def test_the_parser_reads_each_shape(self) -> None:
        assert task_reference("atl-41") == task_reference("ATL-41")
        parsed = task_reference("ATL-41-2")
        assert parsed is not None
        assert (parsed.key, parsed.number, parsed.sub_number) == ("ATL", 41, 2)
        assert task_reference("ATL-G1") is None
        assert task_reference("ATL") is None
        goal = goal_reference("atl-g7")
        assert goal is not None
        assert (goal.key, goal.number) == ("ATL", 7)
        assert goal_reference("ATL-7") is None


class TestLabels:
    async def test_a_long_sub_status_is_refused_on_a_new_card(self, signed_in: AsyncClient) -> None:
        person = await _project(signed_in)

        response = await signed_in.post(
            "/projects/ATL/tasks", json=_card(person, sub_statuses=["Draft", TOO_LONG_LABEL])
        )

        assert response.status_code == 422, response.text

    async def test_a_long_sub_status_is_refused_on_an_edit(self, signed_in: AsyncClient) -> None:
        person = await _project(signed_in)
        card = (await signed_in.post("/projects/ATL/tasks", json=_card(person))).json()

        response = await signed_in.patch(
            f"/tasks/{card['id']}", json={"sub_statuses": [TOO_LONG_LABEL]}
        )

        assert response.status_code == 422, response.text

    async def test_a_label_at_the_limit_is_kept(self, signed_in: AsyncClient) -> None:
        person = await _project(signed_in)
        label = "x" * 60

        response = await signed_in.post(
            "/projects/ATL/tasks", json=_card(person, sub_statuses=[f"  {label}  "])
        )

        assert response.status_code == 201, response.text
        assert response.json()["sub_statuses"] == [label]

    async def test_a_long_template_sub_stage_is_refused(self, signed_in: AsyncClient) -> None:
        await _project(signed_in)
        column = (await signed_in.get("/projects/ATL/columns")).json()["columns"][0]["id"]

        response = await signed_in.post(
            "/projects/ATL/templates",
            json={
                "name": "Hotfix",
                "stages": [{"column_id": column, "sub_stage_labels": [TOO_LONG_LABEL]}],
            },
        )

        assert response.status_code == 422, response.text


class TestUploads:
    async def _root(self, client: AsyncClient) -> str:
        await client.post("/projects", json=ATLAS)
        root: str = (await client.get("/projects/ATL/tree")).json()["id"]
        return root

    async def test_a_filename_longer_than_its_column_is_refused(
        self, signed_in: AsyncClient
    ) -> None:
        root = await self._root(signed_in)

        response = await signed_in.post(
            f"/folders/{root}/upload",
            files={"file": ("a" * 252 + ".pdf", b"cylist bounds: long name\n", "application/pdf")},
        )

        assert response.status_code == 422, response.text

    async def test_an_absurd_content_type_is_stored_as_generic(
        self, signed_in: AsyncClient
    ) -> None:
        root = await self._root(signed_in)

        response = await signed_in.post(
            f"/folders/{root}/upload",
            files={"file": ("brief.pdf", b"cylist bounds: long mime\n", "x/" + "y" * 300)},
        )

        assert response.status_code == 201, response.text
        assert response.json()["mime"] == "application/octet-stream"
