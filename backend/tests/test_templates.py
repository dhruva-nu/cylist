"""Task templates, and the two rules their stages enforce.

A stage says two things about one column: that a template's cards may sit
there, and the sub-stages — the card's own click-through progress bar — it
should be on while it is. Both are checked here: which columns a card may go
to, and whether it may leave the one it is in.
"""

from __future__ import annotations

from httpx import AsyncClient

ATLAS = {"key": "ATL", "name": "Atlas Billing Migration"}
TEAM = {
    "name": "Aditi K",
    "kind": "team",
    "role": "Backend engineer",
    "responsibilities": "Payments and webhooks.",
}
UNKNOWN_ID = "00000000-0000-7000-8000-000000000000"


async def _project(client: AsyncClient) -> str:
    """A project with a member, ready to hold cards."""
    await client.post("/projects", json=ATLAS)
    person = (await client.post("/people", json=TEAM)).json()["id"]
    await client.put("/projects/ATL/members", json={"person_ids": [person]})
    return str(person)


async def _columns(client: AsyncClient) -> dict[str, str]:
    """The board's columns by name."""
    body = (await client.get("/projects/ATL/columns")).json()
    return {column["name"]: column["id"] for column in body["columns"]}


async def _add_column(client: AsyncClient, name: str) -> str:
    response = await client.post(
        "/projects/ATL/columns", json={"name": name, "description": f"Work that is {name}."}
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def _add_middle_column(client: AsyncClient, name: str) -> str:
    """Add a column between the board's first and last, so it is neither."""
    column_id = await _add_column(client, name)
    columns = await _columns(client)
    ordered = [columns["To do"], column_id, columns["Done"]]
    response = await client.put("/projects/ATL/columns/order", json={"column_ids": ordered})
    assert response.status_code == 200, response.text
    return column_id


async def _template(
    client: AsyncClient,
    name: str = "Hotfix",
    stages: list[dict[str, object]] | None = None,
) -> str:
    response = await client.post(
        "/projects/ATL/templates",
        json={"name": name, "description": f"A {name.lower()} card.", "stages": stages or []},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def _task(client: AsyncClient, person: str, **extra: object) -> dict[str, object]:
    response = await client.post(
        "/projects/ATL/tasks",
        json={
            "title": "Stripe webhook idempotency",
            "description": "Duplicate deliveries create double payments.",
            "type": "bug",
            "assignee_id": person,
            **extra,
        },
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


class TestTemplates:
    async def test_a_new_project_has_none(self, signed_in: AsyncClient) -> None:
        """Templates are opt-in: a board works without ever naming one."""
        await _project(signed_in)

        assert (await signed_in.get("/projects/ATL/templates")).json() == []

    async def test_a_template_is_created_with_its_description(self, signed_in: AsyncClient) -> None:
        await _project(signed_in)

        response = await signed_in.post(
            "/projects/ATL/templates",
            json={"name": "Hotfix", "description": "Something is on fire in production."},
        )

        assert response.status_code == 201
        assert response.json()["name"] == "Hotfix"
        assert response.json()["description"] == "Something is on fire in production."

    async def test_a_name_is_enough(self, signed_in: AsyncClient) -> None:
        """A template is a word written on a card. "Hotfix" explains itself, and
        a second required box between wanting one and having one is a toll."""
        await _project(signed_in)

        response = await signed_in.post("/projects/ATL/templates", json={"name": "Hotfix"})

        assert response.status_code == 201
        assert response.json()["description"] == ""

    async def test_a_template_still_needs_a_name(self, signed_in: AsyncClient) -> None:
        await _project(signed_in)

        response = await signed_in.post("/projects/ATL/templates", json={"name": "   "})

        assert response.status_code == 422

    async def test_a_new_template_is_unrestricted(self, signed_in: AsyncClient) -> None:
        """Silence is not a ban: nothing governs it until it gains a stage."""
        await _project(signed_in)
        await _template(signed_in)

        listed = (await signed_in.get("/projects/ATL/templates")).json()

        assert listed[0]["allowed_column_ids"] == []
        assert listed[0]["stages"] == []

    async def test_two_templates_cannot_share_a_name(self, signed_in: AsyncClient) -> None:
        await _project(signed_in)
        await _template(signed_in, "Hotfix")

        response = await signed_in.post(
            "/projects/ATL/templates", json={"name": "hotfix", "description": "Again."}
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"

    async def test_a_template_is_renamed(self, signed_in: AsyncClient) -> None:
        await _project(signed_in)
        template = await _template(signed_in, "Hotfix")

        response = await signed_in.patch(f"/templates/{template}", json={"name": "Prod incident"})

        assert response.status_code == 200
        assert response.json()["name"] == "Prod incident"

    async def test_renaming_to_its_own_name_is_not_a_clash(self, signed_in: AsyncClient) -> None:
        await _project(signed_in)
        template = await _template(signed_in, "Hotfix")

        response = await signed_in.patch(
            f"/templates/{template}", json={"name": "Hotfix", "description": "Reworded."}
        )

        assert response.status_code == 200

    async def test_an_unused_template_is_deleted(self, signed_in: AsyncClient) -> None:
        await _project(signed_in)
        template = await _template(signed_in)

        assert (await signed_in.delete(f"/templates/{template}")).status_code == 200
        assert (await signed_in.get("/projects/ATL/templates")).json() == []

    async def test_a_template_a_card_uses_is_not_deleted(self, signed_in: AsyncClient) -> None:
        """Deleting it would quietly free the card and drop what it still owes."""
        person = await _project(signed_in)
        template = await _template(signed_in)
        await _task(signed_in, person, template_id=template)

        response = await signed_in.delete(f"/templates/{template}")

        assert response.status_code == 409
        assert response.json()["error"]["details"]["task_count"] == 1

    async def test_a_template_counts_its_cards(self, signed_in: AsyncClient) -> None:
        person = await _project(signed_in)
        template = await _template(signed_in)
        await _task(signed_in, person, template_id=template)

        listed = (await signed_in.get("/projects/ATL/templates")).json()

        assert listed[0]["task_count"] == 1

    async def test_a_missing_template_is_a_404(self, signed_in: AsyncClient) -> None:
        await _project(signed_in)

        assert (await signed_in.patch(f"/templates/{UNKNOWN_ID}", json={})).status_code == 404


class TestStages:
    async def test_a_template_is_a_list_of_stages(self, signed_in: AsyncClient) -> None:
        await _project(signed_in)
        columns = await _columns(signed_in)

        template = await _template(
            signed_in,
            "Hotfix",
            [
                {"column_id": columns["To do"], "sub_stage_labels": ["Confirmed", "Triaged"]},
                {"column_id": columns["Done"], "sub_stage_labels": ["Announced"]},
            ],
        )

        listed = (await signed_in.get("/projects/ATL/templates")).json()[0]
        assert listed["id"] == template
        assert listed["allowed_column_ids"] == [columns["To do"], columns["Done"]]
        by_column = {stage["column_id"]: stage["sub_stage_labels"] for stage in listed["stages"]}
        assert by_column[columns["To do"]] == ["Confirmed", "Triaged"]
        assert by_column[columns["Done"]] == ["Announced"]

    async def test_stages_read_in_the_boards_own_order(self, signed_in: AsyncClient) -> None:
        """However they were written: a template reads along the board."""
        await _project(signed_in)
        columns = await _columns(signed_in)

        await _template(
            signed_in,
            "Hotfix",
            [
                {"column_id": columns["Done"], "sub_stage_labels": []},
                {"column_id": columns["To do"], "sub_stage_labels": []},
            ],
        )

        listed = (await signed_in.get("/projects/ATL/templates")).json()[0]
        assert listed["allowed_column_ids"] == [columns["To do"], columns["Done"]]

    async def test_a_template_may_start_with_no_stages(self, signed_in: AsyncClient) -> None:
        await _project(signed_in)

        template = await _template(signed_in, "Hotfix", [])

        listed = (await signed_in.get("/projects/ATL/templates")).json()
        assert listed[0]["id"] == template
        assert listed[0]["stages"] == []

    async def test_a_column_cannot_be_named_twice_in_one_template(
        self, signed_in: AsyncClient
    ) -> None:
        await _project(signed_in)
        columns = await _columns(signed_in)

        response = await signed_in.post(
            "/projects/ATL/templates",
            json={
                "name": "Hotfix",
                "stages": [
                    {"column_id": columns["To do"], "sub_stage_labels": []},
                    {"column_id": columns["To do"], "sub_stage_labels": ["Something else"]},
                ],
            },
        )

        assert response.status_code == 422

    async def test_a_stage_cannot_name_a_column_off_this_board(
        self, signed_in: AsyncClient
    ) -> None:
        await _project(signed_in)

        response = await signed_in.post(
            "/projects/ATL/templates",
            json={"name": "Hotfix", "stages": [{"column_id": UNKNOWN_ID, "sub_stage_labels": []}]},
        )

        assert response.status_code == 422

    async def test_a_sub_stage_label_cannot_be_blank(self, signed_in: AsyncClient) -> None:
        await _project(signed_in)
        columns = await _columns(signed_in)

        response = await signed_in.post(
            "/projects/ATL/templates",
            json={
                "name": "Hotfix",
                "stages": [{"column_id": columns["To do"], "sub_stage_labels": ["  "]}],
            },
        )

        assert response.status_code == 422

    async def test_a_stage_cannot_hold_more_than_four_sub_stages(
        self, signed_in: AsyncClient
    ) -> None:
        """Matches ``Task.sub_statuses``: a stage cannot promise more than the
        card's own progress bar can hold."""
        await _project(signed_in)
        columns = await _columns(signed_in)

        response = await signed_in.post(
            "/projects/ATL/templates",
            json={
                "name": "Hotfix",
                "stages": [
                    {"column_id": columns["To do"], "sub_stage_labels": ["A", "B", "C", "D", "E"]}
                ],
            },
        )

        assert response.status_code == 422

    async def test_editing_replaces_the_whole_set_of_stages(self, signed_in: AsyncClient) -> None:
        await _project(signed_in)
        columns = await _columns(signed_in)
        template = await _template(
            signed_in,
            "Hotfix",
            [
                {"column_id": columns["To do"], "sub_stage_labels": ["A"]},
                {"column_id": columns["Done"], "sub_stage_labels": ["B"]},
            ],
        )

        response = await signed_in.patch(
            f"/templates/{template}",
            json={"stages": [{"column_id": columns["Done"], "sub_stage_labels": ["C"]}]},
        )

        assert response.status_code == 200
        assert response.json()["allowed_column_ids"] == [columns["Done"]]

    async def test_a_rename_leaves_the_stages_alone(self, signed_in: AsyncClient) -> None:
        """`stages` is the only field of a PATCH that is all-or-nothing."""
        await _project(signed_in)
        columns = await _columns(signed_in)
        template = await _template(
            signed_in, "Hotfix", [{"column_id": columns["To do"], "sub_stage_labels": ["A"]}]
        )

        response = await signed_in.patch(f"/templates/{template}", json={"name": "Prod incident"})

        assert response.json()["name"] == "Prod incident"
        assert len(response.json()["stages"]) == 1

    async def test_a_bad_stage_leaves_the_template_as_it_was(self, signed_in: AsyncClient) -> None:
        """Everything is checked before anything is written."""
        await _project(signed_in)
        columns = await _columns(signed_in)
        template = await _template(
            signed_in, "Hotfix", [{"column_id": columns["To do"], "sub_stage_labels": ["A"]}]
        )

        await signed_in.patch(
            f"/templates/{template}",
            json={"stages": [{"column_id": UNKNOWN_ID, "sub_stage_labels": []}]},
        )

        kept = (await signed_in.get("/projects/ATL/templates")).json()[0]
        assert kept["allowed_column_ids"] == [columns["To do"]]

    async def test_deleting_a_column_takes_it_out_of_every_template(
        self, signed_in: AsyncClient
    ) -> None:
        """A board no longer has that column, so no template can say anything
        about it."""
        await _project(signed_in)
        columns = await _columns(signed_in)
        review = await _add_column(signed_in, "Review")
        await _template(
            signed_in,
            "Hotfix",
            [
                {"column_id": columns["To do"], "sub_stage_labels": []},
                {"column_id": review, "sub_stage_labels": ["Get sign-off"]},
            ],
        )

        assert (await signed_in.delete(f"/columns/{review}")).status_code == 200

        listed = (await signed_in.get("/projects/ATL/templates")).json()
        assert listed[0]["allowed_column_ids"] == [columns["To do"]]


class TestCardsFollowTheirTemplate:
    async def test_a_card_without_a_template_goes_anywhere(self, signed_in: AsyncClient) -> None:
        person = await _project(signed_in)
        columns = await _columns(signed_in)
        task = await _task(signed_in, person)

        response = await signed_in.post(
            f"/tasks/{task['id']}/move", json={"column_id": columns["Done"], "position": 0}
        )

        assert response.status_code == 200

    async def test_a_card_of_a_stageless_template_goes_anywhere(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _project(signed_in)
        columns = await _columns(signed_in)
        task = await _task(signed_in, person, template_id=await _template(signed_in))

        response = await signed_in.post(
            f"/tasks/{task['id']}/move", json={"column_id": columns["Done"], "position": 0}
        )

        assert response.status_code == 200

    async def test_a_card_goes_where_its_template_allows(self, signed_in: AsyncClient) -> None:
        person = await _project(signed_in)
        columns = await _columns(signed_in)
        template = await _template(
            signed_in,
            "Hotfix",
            [
                {"column_id": columns["To do"], "sub_stage_labels": []},
                {"column_id": columns["Done"], "sub_stage_labels": []},
            ],
        )
        task = await _task(signed_in, person, template_id=template)

        response = await signed_in.post(
            f"/tasks/{task['id']}/move", json={"column_id": columns["Done"], "position": 0}
        )

        assert response.status_code == 200

    async def test_a_card_is_refused_a_column_its_template_rules_out(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _project(signed_in)
        columns = await _columns(signed_in)
        review = await _add_column(signed_in, "Review")
        template = await _template(
            signed_in,
            "Hotfix",
            [
                {"column_id": columns["To do"], "sub_stage_labels": []},
                {"column_id": columns["Done"], "sub_stage_labels": []},
            ],
        )
        task = await _task(signed_in, person, template_id=template)

        response = await signed_in.post(
            f"/tasks/{task['id']}/move", json={"column_id": review, "position": 0}
        )

        assert response.status_code == 422
        assert response.json()["error"]["details"]["allowed_columns"] == ["To do", "Done"]

    async def test_the_refusal_names_the_template_and_the_columns(
        self, signed_in: AsyncClient
    ) -> None:
        """A message that only says no leaves the reader to guess."""
        person = await _project(signed_in)
        columns = await _columns(signed_in)
        review = await _add_column(signed_in, "Review")
        template = await _template(
            signed_in, "Hotfix", [{"column_id": columns["To do"], "sub_stage_labels": []}]
        )
        task = await _task(signed_in, person, template_id=template)

        message = (
            await signed_in.post(
                f"/tasks/{task['id']}/move", json={"column_id": review, "position": 0}
            )
        ).json()["error"]["message"]

        assert "Hotfix" in message and "Review" in message and "To do" in message

    async def test_a_new_card_lands_in_the_first_column(self, signed_in: AsyncClient) -> None:
        person = await _project(signed_in)
        columns = await _columns(signed_in)
        template = await _template(
            signed_in,
            "Hotfix",
            [
                {"column_id": columns["To do"], "sub_stage_labels": []},
                {"column_id": columns["Done"], "sub_stage_labels": []},
            ],
        )

        task = await _task(signed_in, person, template_id=template)

        assert task["column_id"] == columns["To do"]

    async def test_a_card_barred_from_the_first_column_lands_further_along(
        self, signed_in: AsyncClient
    ) -> None:
        """A card has to be born somewhere its own template permits."""
        person = await _project(signed_in)
        review = await _add_column(signed_in, "Review")
        template = await _template(
            signed_in, "Hotfix", [{"column_id": review, "sub_stage_labels": []}]
        )

        task = await _task(signed_in, person, template_id=template)

        assert task["column_id"] == review

    async def test_a_card_carries_its_templates_name(self, signed_in: AsyncClient) -> None:
        person = await _project(signed_in)
        template = await _template(signed_in, "Hotfix")

        task = await _task(signed_in, person, template_id=template)

        assert task["template_id"] == template
        assert task["template_name"] == "Hotfix"

    async def test_a_card_cannot_take_another_projects_template(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _project(signed_in)

        response = await signed_in.post(
            "/projects/ATL/tasks",
            json={
                "title": "Stripe webhook idempotency",
                "description": "Duplicate deliveries create double payments.",
                "type": "bug",
                "assignee_id": person,
                "template_id": UNKNOWN_ID,
            },
        )

        assert response.status_code == 422


class TestStageSubStages:
    """A stage's labels are loaded onto the card's own ``sub_statuses`` — the
    same click-through progress bar every card carries — the moment the card
    lands in that column, and the card cannot leave until it is on the last
    one."""

    async def test_a_new_card_lands_on_its_columns_sub_stages(self, signed_in: AsyncClient) -> None:
        person = await _project(signed_in)
        columns = await _columns(signed_in)
        template = await _template(
            signed_in,
            "Hotfix",
            [
                {"column_id": columns["To do"], "sub_stage_labels": ["Confirmed", "Triaged"]},
                {"column_id": columns["Done"], "sub_stage_labels": ["Announced"]},
            ],
        )

        task = await _task(signed_in, person, template_id=template)

        assert task["sub_statuses"] == ["Confirmed", "Triaged"]
        assert task["sub_status_index"] == 0

    async def test_a_card_with_no_template_gets_no_sub_stages(self, signed_in: AsyncClient) -> None:
        person = await _project(signed_in)

        task = await _task(signed_in, person)

        assert task["sub_statuses"] == []
        assert task["sub_status_index"] is None

    async def test_a_column_the_template_says_nothing_about_keeps_the_cards_own_choice(
        self, signed_in: AsyncClient
    ) -> None:
        """An unstaged column asks nothing of the cards that pass through it —
        the caller's own ``sub_statuses`` stand rather than being cleared."""
        person = await _project(signed_in)
        template = await _template(signed_in, "Hotfix")

        task = await _task(
            signed_in, person, template_id=template, sub_statuses=["Drafted", "Reviewed"]
        )

        assert task["sub_statuses"] == ["Drafted", "Reviewed"]

    async def test_a_card_cannot_leave_a_column_before_the_last_sub_stage(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _project(signed_in)
        columns = await _columns(signed_in)
        template = await _template(
            signed_in,
            "Hotfix",
            [
                {"column_id": columns["To do"], "sub_stage_labels": ["Confirmed", "Triaged"]},
                {"column_id": columns["Done"], "sub_stage_labels": []},
            ],
        )
        task = await _task(signed_in, person, template_id=template)

        response = await signed_in.post(
            f"/tasks/{task['id']}/move", json={"column_id": columns["Done"], "position": 0}
        )

        assert response.status_code == 422
        assert "Triaged" in response.json()["error"]["message"]

    async def test_reaching_the_last_sub_stage_lets_the_card_move(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _project(signed_in)
        columns = await _columns(signed_in)
        template = await _template(
            signed_in,
            "Hotfix",
            [
                {"column_id": columns["To do"], "sub_stage_labels": ["Confirmed", "Triaged"]},
                {"column_id": columns["Done"], "sub_stage_labels": []},
            ],
        )
        task = await _task(signed_in, person, template_id=template)
        await signed_in.post(f"/tasks/{task['id']}/sub-status", json={"index": 1})

        response = await signed_in.post(
            f"/tasks/{task['id']}/move", json={"column_id": columns["Done"], "position": 0}
        )

        assert response.status_code == 200

    async def test_a_single_label_stage_never_blocks(self, signed_in: AsyncClient) -> None:
        """A new card lands on the first label, which is also the last one
        when a stage names only one — so there is nothing to advance through."""
        person = await _project(signed_in)
        columns = await _columns(signed_in)
        template = await _template(
            signed_in,
            "Hotfix",
            [
                {"column_id": columns["To do"], "sub_stage_labels": ["Confirmed"]},
                {"column_id": columns["Done"], "sub_stage_labels": []},
            ],
        )
        task = await _task(signed_in, person, template_id=template)

        response = await signed_in.post(
            f"/tasks/{task['id']}/move", json={"column_id": columns["Done"], "position": 0}
        )

        assert response.status_code == 200

    async def test_a_card_picks_up_the_next_columns_sub_stages_on_arrival(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _project(signed_in)
        columns = await _columns(signed_in)
        template = await _template(
            signed_in,
            "Hotfix",
            [
                {"column_id": columns["To do"], "sub_stage_labels": ["Confirmed"]},
                {"column_id": columns["Done"], "sub_stage_labels": ["Announced", "Closed"]},
            ],
        )
        task = await _task(signed_in, person, template_id=template)

        moved = await signed_in.post(
            f"/tasks/{task['id']}/move", json={"column_id": columns["Done"], "position": 0}
        )

        assert moved.status_code == 200
        assert moved.json()["sub_statuses"] == ["Announced", "Closed"]
        assert moved.json()["sub_status_index"] == 0

    async def test_moving_within_one_column_is_not_leaving_it(self, signed_in: AsyncClient) -> None:
        """Reordering a stack is not departing a column, so nothing gates it."""
        person = await _project(signed_in)
        columns = await _columns(signed_in)
        template = await _template(
            signed_in,
            "Hotfix",
            [{"column_id": columns["To do"], "sub_stage_labels": ["Confirmed", "Triaged"]}],
        )
        task = await _task(signed_in, person, template_id=template)

        response = await signed_in.post(
            f"/tasks/{task['id']}/move", json={"column_id": columns["To do"], "position": 0}
        )

        assert response.status_code == 200

    async def test_moving_backward_is_gated_too(self, signed_in: AsyncClient) -> None:
        """Every column gates the next move, whichever direction it is."""
        person = await _project(signed_in)
        review = await _add_middle_column(signed_in, "Review")
        columns = await _columns(signed_in)
        template = await _template(
            signed_in,
            "Hotfix",
            [
                {"column_id": columns["To do"], "sub_stage_labels": []},
                {"column_id": review, "sub_stage_labels": ["Get sign-off", "Confirmed"]},
                {"column_id": columns["Done"], "sub_stage_labels": []},
            ],
        )
        task = await _task(signed_in, person, template_id=template)
        moved = await signed_in.post(
            f"/tasks/{task['id']}/move", json={"column_id": review, "position": 0}
        )
        assert moved.status_code == 200, moved.text

        response = await signed_in.post(
            f"/tasks/{task['id']}/move", json={"column_id": columns["To do"], "position": 0}
        )

        assert response.status_code == 422
        assert "Confirmed" in response.json()["error"]["message"]

    async def test_a_hand_typed_checklist_item_never_gates_a_column(
        self, signed_in: AsyncClient
    ) -> None:
        """A template's sub-stages are the only thing that gates leaving a
        column. A hand-typed checklist item only ever holds up the board's
        last column, as it always has."""
        person = await _project(signed_in)
        review = await _add_middle_column(signed_in, "Review")
        task = await _task(signed_in, person)
        await signed_in.post(f"/tasks/{task['id']}/checklist", json={"title": "Ad hoc note"})

        response = await signed_in.post(
            f"/tasks/{task['id']}/move", json={"column_id": review, "position": 0}
        )

        assert response.status_code == 200

    async def test_a_hand_typed_item_still_gates_the_last_column(
        self, signed_in: AsyncClient
    ) -> None:
        """The existing, template-independent rule is unchanged."""
        person = await _project(signed_in)
        columns = await _columns(signed_in)
        task = await _task(signed_in, person)
        await signed_in.post(f"/tasks/{task['id']}/checklist", json={"title": "Ad hoc note"})

        response = await signed_in.post(
            f"/tasks/{task['id']}/move", json={"column_id": columns["Done"], "position": 0}
        )

        assert response.status_code == 422
        assert "Ad hoc note" in response.json()["error"]["message"]


class TestChangingACardsTemplate:
    async def test_a_card_takes_a_template_it_is_already_allowed(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _project(signed_in)
        columns = await _columns(signed_in)
        template = await _template(
            signed_in, "Hotfix", [{"column_id": columns["To do"], "sub_stage_labels": []}]
        )
        task = await _task(signed_in, person)

        response = await signed_in.patch(f"/tasks/{task['id']}", json={"template_id": template})

        assert response.status_code == 200
        assert response.json()["template_id"] == template

    async def test_a_card_is_not_retyped_into_a_column_it_would_be_barred_from(
        self, signed_in: AsyncClient
    ) -> None:
        """Changing a template is not a move, and must not become one."""
        person = await _project(signed_in)
        columns = await _columns(signed_in)
        template = await _template(
            signed_in, "Hotfix", [{"column_id": columns["To do"], "sub_stage_labels": []}]
        )
        task = await _task(signed_in, person)
        await signed_in.post(
            f"/tasks/{task['id']}/move", json={"column_id": columns["Done"], "position": 0}
        )

        response = await signed_in.patch(f"/tasks/{task['id']}", json={"template_id": template})

        assert response.status_code == 422
        assert response.json()["error"]["details"]["allowed_columns"] == ["To do"]

    async def test_changing_template_does_not_retroactively_set_sub_stages(
        self, signed_in: AsyncClient
    ) -> None:
        """A template's stages apply as a card lands somewhere. Retyping a card
        already sitting in a column does not rewrite the progress it carries."""
        person = await _project(signed_in)
        columns = await _columns(signed_in)
        template = await _template(
            signed_in,
            "Hotfix",
            [{"column_id": columns["To do"], "sub_stage_labels": ["Confirmed", "Triaged"]}],
        )
        task = await _task(signed_in, person)

        response = await signed_in.patch(f"/tasks/{task['id']}", json={"template_id": template})

        assert response.json()["sub_statuses"] == []

    async def test_a_card_is_taken_out_of_its_template_with_null(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _project(signed_in)
        columns = await _columns(signed_in)
        review = await _add_column(signed_in, "Review")
        template = await _template(
            signed_in, "Hotfix", [{"column_id": columns["To do"], "sub_stage_labels": []}]
        )
        task = await _task(signed_in, person, template_id=template)

        await signed_in.patch(f"/tasks/{task['id']}", json={"template_id": None})
        response = await signed_in.post(
            f"/tasks/{task['id']}/move", json={"column_id": review, "position": 0}
        )

        assert response.status_code == 200

    async def test_the_change_reads_in_the_cards_history_by_name(
        self, signed_in: AsyncClient
    ) -> None:
        person = await _project(signed_in)
        template = await _template(signed_in, "Hotfix")
        task = await _task(signed_in, person)

        await signed_in.patch(f"/tasks/{task['id']}", json={"template_id": template})

        entries = (await signed_in.get(f"/tasks/{task['id']}/history")).json()["entries"]
        change = entries[0]["changes"][0]
        assert change["field"] == "template"
        assert change["from"] is None
        assert change["to"] == "Hotfix"


class TestWhatTheTrailSays:
    async def test_a_template_is_written_down_when_it_is_created(
        self, signed_in: AsyncClient
    ) -> None:
        await _project(signed_in)
        columns = await _columns(signed_in)

        await _template(
            signed_in, "Hotfix", [{"column_id": columns["To do"], "sub_stage_labels": []}]
        )

        entries = (await signed_in.get("/activity", params={"entity_type": "template"})).json()

        assert entries[0]["verb"] == "template.created"

    async def test_the_days_report_words_it(self, signed_in: AsyncClient) -> None:
        """A day that set a project's rules up should say so in words."""
        await _project(signed_in)
        await _template(signed_in, "Hotfix")

        report = (
            await signed_in.get("/projects/ATL/reports/day", params={"timezone": "UTC"})
        ).json()

        assert any("Hotfix" in entry["summary"] for entry in report["elsewhere"])
