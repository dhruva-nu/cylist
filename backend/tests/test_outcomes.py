"""Outcomes: the sections the board's last column is divided into.

A board answers "where is this work" with a column and "when did it stop" with
a finishing time. How it stopped — shipped, dropped, sitting in production —
was a question it could only answer by growing another column, which made done
a place the board had several of. These are the three sections one column can
be divided into instead, and what a card, a template and a reordered board each
have to say about them.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

ATLAS = {"key": "ATL", "name": "Atlas Billing Migration"}
ADITI = {
    "name": "Aditi K",
    "kind": "team",
    "role": "Backend engineer",
    "responsibilities": "Payments and webhooks.",
}
OUTCOMES = ["Done", "Cancelled", "In prod"]


async def _setup(client: AsyncClient) -> str:
    await client.post("/projects", json=ATLAS)
    person = (await client.post("/people", json=ADITI)).json()["id"]
    await client.put("/projects/ATL/members", json={"person_ids": [person]})
    return str(person)


async def _create(client: AsyncClient, assignee: str, **overrides: Any) -> dict[str, Any]:
    response = await client.post(
        "/projects/ATL/tasks",
        json={
            "title": "Stripe webhook idempotency",
            "description": "Duplicate deliveries create double payments.",
            "type": "bug",
            "assignee_id": assignee,
            **overrides,
        },
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _columns(client: AsyncClient) -> list[dict[str, Any]]:
    return list((await client.get("/projects/ATL/columns")).json()["columns"])


async def _divide(client: AsyncClient, outcomes: list[str] = OUTCOMES) -> dict[str, Any]:
    """Divide the board's last column, and hand it back."""
    last = (await _columns(client))[-1]
    response = await client.patch(f"/columns/{last['id']}", json={"outcomes": outcomes})
    assert response.status_code == 200, response.text
    return dict(response.json())


async def _move(
    client: AsyncClient, task: dict[str, Any], column: dict[str, Any], **body: Any
) -> Any:
    return await client.post(
        f"/tasks/{task['id']}/move", json={"column_id": column["id"], "position": 0, **body}
    )


class TestDividingAColumn:
    async def test_the_last_column_can_be_divided(self, signed_in: AsyncClient) -> None:
        await _setup(signed_in)

        assert (await _divide(signed_in))["outcomes"] == OUTCOMES

    async def test_a_column_that_is_not_last_cannot(self, signed_in: AsyncClient) -> None:
        """Nothing ends in the middle of a board."""
        await _setup(signed_in)
        first = (await _columns(signed_in))[0]

        refused = await signed_in.patch(f"/columns/{first['id']}", json={"outcomes": OUTCOMES})

        assert refused.status_code == 422
        assert "last column" in refused.json()["error"]["message"]

    async def test_a_fourth_outcome_is_refused(self, signed_in: AsyncClient) -> None:
        await _setup(signed_in)
        last = (await _columns(signed_in))[-1]

        refused = await signed_in.patch(
            f"/columns/{last['id']}", json={"outcomes": [*OUTCOMES, "Superseded"]}
        )

        assert refused.status_code == 422

    async def test_two_sections_by_the_same_name_are_refused(self, signed_in: AsyncClient) -> None:
        await _setup(signed_in)
        last = (await _columns(signed_in))[-1]

        refused = await signed_in.patch(
            f"/columns/{last['id']}", json={"outcomes": ["Done", "done"]}
        )

        assert refused.status_code == 422

    async def test_a_new_column_to_the_right_takes_the_sections_away(
        self, signed_in: AsyncClient
    ) -> None:
        """Last is a position. The column that loses it loses the sections too."""
        await _setup(signed_in)
        was_last = (await _divide(signed_in))["id"]

        await signed_in.post(
            "/projects/ATL/columns",
            json={"name": "In staging", "description": "Deployed where it can be looked at."},
        )

        board = {column["id"]: column for column in await _columns(signed_in)}
        assert board[was_last]["outcomes"] == []

    async def test_reordering_the_board_takes_them_away_too(self, signed_in: AsyncClient) -> None:
        await _setup(signed_in)
        divided = (await _divide(signed_in))["id"]
        board = await _columns(signed_in)

        await signed_in.put(
            "/projects/ATL/columns/order",
            json={"column_ids": [divided, board[0]["id"]]},
        )

        after = {column["id"]: column for column in await _columns(signed_in)}
        assert after[divided]["outcomes"] == []


class TestLandingOnOne:
    async def test_a_card_lands_on_the_outcome_it_names(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        last = await _divide(signed_in)
        task = await _create(signed_in, person)

        moved = (await _move(signed_in, task, last, outcome="Cancelled")).json()

        assert moved["outcome"] == "Cancelled"
        assert moved["outcome_index"] == 1

    async def test_an_unnamed_outcome_lands_on_the_first(self, signed_in: AsyncClient) -> None:
        """A card dragged onto the last column has ended. Refusing the drop for
        want of a word the board can supply would make the ordinary gesture the
        awkward one."""
        person = await _setup(signed_in)
        last = await _divide(signed_in)
        task = await _create(signed_in, person)

        moved = (await _move(signed_in, task, last)).json()

        assert moved["outcome"] == "Done"

    async def test_the_name_is_matched_however_it_is_cased(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        last = await _divide(signed_in)
        task = await _create(signed_in, person)

        moved = (await _move(signed_in, task, last, outcome="in PROD")).json()

        assert moved["outcome"] == "In prod"

    async def test_an_outcome_the_column_does_not_have_is_refused(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _setup(signed_in)
        last = await _divide(signed_in)
        task = await _create(signed_in, person)

        refused = await _move(signed_in, task, last, outcome="Shipped")

        assert refused.status_code == 422
        assert refused.json()["error"]["details"]["outcomes"] == OUTCOMES

    async def test_an_undivided_column_leaves_a_card_in_no_section(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _setup(signed_in)
        last = (await _columns(signed_in))[-1]
        task = await _create(signed_in, person)

        moved = (await _move(signed_in, task, last, outcome="Done")).json()

        assert moved["outcome"] is None
        assert moved["outcome_index"] is None

    async def test_leaving_the_column_leaves_the_section(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        board = await _columns(signed_in)
        last = await _divide(signed_in)
        task = await _create(signed_in, person)
        await _move(signed_in, task, last, outcome="Cancelled")

        moved = (await _move(signed_in, task, board[0])).json()

        assert moved["outcome"] is None

    async def test_a_card_can_be_moved_between_sections(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        last = await _divide(signed_in)
        task = await _create(signed_in, person)
        await _move(signed_in, task, last, outcome="Done")

        moved = (await _move(signed_in, task, last, outcome="In prod")).json()

        assert moved["outcome"] == "In prod"

    async def test_landing_on_any_of_them_still_finishes_the_card(
        self, signed_in: AsyncClient
    ) -> None:
        """How the work ended and when it stopped are two questions. A card in
        Cancelled is in the last column, and the last column is done."""
        person = await _setup(signed_in)
        last = await _divide(signed_in)
        task = await _create(signed_in, person)

        moved = (await _move(signed_in, task, last, outcome="Cancelled")).json()

        assert moved["finished_at"] is not None


class TestShorteningTheList:
    async def test_a_card_past_the_end_falls_back_to_the_last_section(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _setup(signed_in)
        last = await _divide(signed_in)
        task = await _create(signed_in, person)
        await _move(signed_in, task, last, outcome="In prod")

        await _divide(signed_in, ["Done", "Cancelled"])

        card = (await signed_in.get(f"/tasks/{task['reference']}")).json()
        assert card["outcome"] == "Cancelled"

    async def test_emptying_the_list_takes_every_card_out_of_a_section(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _setup(signed_in)
        last = await _divide(signed_in)
        task = await _create(signed_in, person)
        await _move(signed_in, task, last, outcome="Cancelled")

        await _divide(signed_in, [])

        card = (await signed_in.get(f"/tasks/{task['reference']}")).json()
        assert card["outcome"] is None
        assert card["outcome_index"] is None

    async def test_a_card_within_the_shorter_list_stays_where_it_was(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _setup(signed_in)
        last = await _divide(signed_in)
        task = await _create(signed_in, person)
        await _move(signed_in, task, last, outcome="Done")

        await _divide(signed_in, ["Done", "Cancelled"])

        card = (await signed_in.get(f"/tasks/{task['reference']}")).json()
        assert card["outcome"] == "Done"


class TestGovernedByTheTemplate:
    async def _template(
        self, client: AsyncClient, column_id: str, allowed: list[str]
    ) -> dict[str, Any]:
        response = await client.post(
            "/projects/ATL/templates",
            json={
                "name": "Hotfix",
                "stages": [{"column_id": column_id, "allowed_outcomes": allowed}],
            },
        )
        assert response.status_code == 201, response.text
        return dict(response.json())

    async def test_a_stage_may_narrow_the_outcomes(self, signed_in: AsyncClient) -> None:
        await _setup(signed_in)
        last = await _divide(signed_in)

        template = await self._template(signed_in, last["id"], ["Done", "In prod"])

        assert template["stages"][0]["allowed_outcomes"] == ["Done", "In prod"]

    async def test_a_stage_naming_an_outcome_the_column_lacks_is_refused(
        self, signed_in: AsyncClient
    ) -> None:
        """A typo, or a rule written against a board that has since changed.
        Both are worth being told about while the author is looking."""
        await _setup(signed_in)
        last = await _divide(signed_in)

        refused = await signed_in.post(
            "/projects/ATL/templates",
            json={
                "name": "Hotfix",
                "stages": [{"column_id": last["id"], "allowed_outcomes": ["Shipped"]}],
            },
        )

        assert refused.status_code == 422
        assert refused.json()["error"]["details"]["unknown_outcomes"] == ["Shipped"]

    async def test_a_card_cannot_end_on_an_outcome_its_template_rules_out(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _setup(signed_in)
        board = await _columns(signed_in)
        last = await _divide(signed_in)
        template = await self._template(signed_in, last["id"], ["Done"])
        # The stage names only the last column, so that is the one column the
        # card may sit in — and where it has to be created.
        task = await _create(signed_in, person, template_id=template["id"])

        refused = await _move(signed_in, task, last, outcome="Cancelled")

        assert refused.status_code == 422
        assert refused.json()["error"]["details"]["allowed_outcomes"] == ["Done"]
        assert board  # the board was read to make the shape of this test plain

    async def test_an_unnamed_outcome_lands_on_the_first_the_template_allows(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _setup(signed_in)
        last = await _divide(signed_in)
        template = await self._template(signed_in, last["id"], ["In prod"])
        task = await _create(signed_in, person, template_id=template["id"])

        moved = (await _move(signed_in, task, last)).json()

        assert moved["outcome"] == "In prod"

    async def test_a_stage_that_names_none_allows_all_of_them(self, signed_in: AsyncClient) -> None:
        """Silence is not a ban, the way it is not for columns or sub-stages."""
        person = await _setup(signed_in)
        last = await _divide(signed_in)
        template = await self._template(signed_in, last["id"], [])
        task = await _create(signed_in, person, template_id=template["id"])

        moved = (await _move(signed_in, task, last, outcome="Cancelled")).json()

        assert moved["outcome"] == "Cancelled"


class TestWhatTheHistorySays:
    async def test_finishing_names_the_section(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        last = await _divide(signed_in)
        task = await _create(signed_in, person)

        await _move(signed_in, task, last, outcome="Cancelled")

        entry = (await signed_in.get(f"/tasks/{task['reference']}/history")).json()["entries"][0]
        assert entry["summary"] == "Moved from To do to Done. Finished as Cancelled."

    async def test_moving_between_sections_is_not_a_move(self, signed_in: AsyncClient) -> None:
        """Nothing moved by the board's reckoning; how the work ended did."""
        person = await _setup(signed_in)
        last = await _divide(signed_in)
        task = await _create(signed_in, person)
        await _move(signed_in, task, last, outcome="Done")

        await _move(signed_in, task, last, outcome="In prod")

        entry = (await signed_in.get(f"/tasks/{task['reference']}/history")).json()["entries"][0]
        assert entry["summary"] == "Ended as In prod."

    async def test_reordering_within_one_section_is_still_not_history(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _setup(signed_in)
        last = await _divide(signed_in)
        first = await _create(signed_in, person, title="First")
        second = await _create(signed_in, person, title="Second")
        await _move(signed_in, second, last, outcome="Done")
        await _move(signed_in, first, last, outcome="Done")

        await signed_in.post(
            f"/tasks/{first['id']}/move",
            json={"column_id": last["id"], "position": 1, "outcome": "Done"},
        )

        entries = (await signed_in.get(f"/tasks/{first['reference']}/history")).json()["entries"]
        assert [entry["verb"] for entry in entries] == ["task.moved", "task.created"]
