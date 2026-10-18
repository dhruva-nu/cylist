"""Today: picking cards up for a day, to-dos under them, and wrapping the day up.

Everything goes through the API, as the browser does it. The report tests at
the bottom read the plan back out of the day report, which is where the plan
ends up being read by anyone but its author.
"""

from __future__ import annotations

from datetime import UTC, datetime

from httpx import AsyncClient

from tests.conftest import INVITEE_PASSWORD, open_account

FRIDAY = "2026-10-16"
TODAY_HERE = datetime.now(UTC).date().isoformat()
"""Today in UTC, which is the zone a plan is read in when none is named."""
SATURDAY = "2026-10-17"
MONDAY = "2026-10-19"


async def make_project(client: AsyncClient, key: str = "ATL") -> str:
    created = await client.post("/projects", json={"key": key, "name": "Atlas migration"})
    assert created.status_code == 201, created.text
    return key


async def make_card(client: AsyncClient, project: str, title: str = "Invoice PDF export") -> str:
    created = await client.post(
        f"/projects/{project}/tasks",
        json={"title": title, "description": "What done looks like.", "type": "feature"},
    )
    assert created.status_code == 201, created.text
    return str(created.json()["reference"])


async def pick(client: AsyncClient, project: str, card: str, day: str = FRIDAY) -> dict:
    picked = await client.post(f"/projects/{project}/today/picks", json={"task": card, "day": day})
    assert picked.status_code == 201, picked.text
    return dict(picked.json())


async def add_todo(client: AsyncClient, pick_id: str, text: str) -> dict:
    added = await client.post(f"/today/picks/{pick_id}/todos", json={"text": text})
    assert added.status_code == 201, added.text
    return dict(added.json())


async def plan(client: AsyncClient, project: str, day: str = FRIDAY) -> dict:
    found = await client.get(f"/projects/{project}/today", params={"date": day})
    assert found.status_code == 200, found.text
    return dict(found.json())


# --- Picking cards up --------------------------------------------------------


async def test_an_unplanned_day_is_empty(signed_in: AsyncClient) -> None:
    project = await make_project(signed_in)

    day = await plan(signed_in, project)

    assert day["picks"] == []
    assert (day["open_count"], day["done_count"]) == (0, 0)


async def test_picked_cards_come_back_in_the_order_picked(signed_in: AsyncClient) -> None:
    project = await make_project(signed_in)
    first = await make_card(signed_in, project, "First")
    second = await make_card(signed_in, project, "Second")

    await pick(signed_in, project, second)
    await pick(signed_in, project, first)

    day = await plan(signed_in, project)
    assert [p["task"]["reference"] for p in day["picks"]] == [second, first]
    assert day["picks"][0]["task"]["column"] == "To do"


async def test_picking_a_card_twice_hands_back_the_same_pick(signed_in: AsyncClient) -> None:
    project = await make_project(signed_in)
    card = await make_card(signed_in, project)

    once = await pick(signed_in, project, card)
    again = await pick(signed_in, project, card)

    assert once["id"] == again["id"]
    assert len((await plan(signed_in, project))["picks"]) == 1


async def test_a_pick_is_for_its_day_alone(signed_in: AsyncClient) -> None:
    project = await make_project(signed_in)
    card = await make_card(signed_in, project)

    await pick(signed_in, project, card, FRIDAY)

    assert (await plan(signed_in, project, MONDAY))["picks"] == []


async def test_a_sub_task_cannot_be_picked(signed_in: AsyncClient) -> None:
    project = await make_project(signed_in)
    card = await make_card(signed_in, project)
    sub = await signed_in.post(
        f"/tasks/{card}/subtasks",
        json={"title": "Layout", "description": "The page.", "type": "feature"},
    )
    assert sub.status_code == 201, sub.text

    refused = await signed_in.post(
        f"/projects/{project}/today/picks",
        json={"task": sub.json()["reference"], "day": FRIDAY},
    )

    assert refused.status_code == 422
    assert "sub-task" in refused.json()["error"]["message"]


async def test_a_card_on_another_project_cannot_be_picked(signed_in: AsyncClient) -> None:
    project = await make_project(signed_in)
    elsewhere = await make_project(signed_in, "BRV")
    card = await make_card(signed_in, elsewhere)

    refused = await signed_in.post(
        f"/projects/{project}/today/picks", json={"task": card, "day": FRIDAY}
    )

    assert refused.status_code == 422


async def test_taking_a_card_off_the_day_takes_its_todos(signed_in: AsyncClient) -> None:
    project = await make_project(signed_in)
    card = await make_card(signed_in, project)
    picked = await pick(signed_in, project, card)
    await add_todo(signed_in, picked["id"], "Email the sample")

    removed = await signed_in.delete(f"/today/picks/{picked['id']}")

    assert removed.status_code == 200, removed.text
    assert (await plan(signed_in, project))["picks"] == []
    again = await pick(signed_in, project, card)
    assert again["todos"] == []


# --- To-dos --------------------------------------------------------------------


async def test_todos_keep_their_tags_as_written(signed_in: AsyncClient) -> None:
    project = await make_project(signed_in)
    picked = await pick(signed_in, project, await make_card(signed_in, project))

    todo = await add_todo(signed_in, picked["id"], "  Email @Neha Rao   the >invoice-sample.pdf ")

    assert todo["text"] == "Email @Neha Rao the >invoice-sample.pdf"
    assert todo["state"] == "open"
    day = await plan(signed_in, project)
    assert [t["text"] for t in day["picks"][0]["todos"]] == [todo["text"]]
    assert day["open_count"] == 1


async def test_ticking_a_todo_counts_it_done(signed_in: AsyncClient) -> None:
    project = await make_project(signed_in)
    picked = await pick(signed_in, project, await make_card(signed_in, project))
    todo = await add_todo(signed_in, picked["id"], "Read the RFC")

    ticked = await signed_in.patch(f"/today/todos/{todo['id']}", json={"state": "done"})

    assert ticked.json()["state"] == "done"
    assert ticked.json()["done_at"] is not None
    day = await plan(signed_in, project)
    assert (day["open_count"], day["done_count"]) == (0, 1)

    unticked = await signed_in.patch(f"/today/todos/{todo['id']}", json={"state": "open"})
    assert unticked.json()["done_at"] is None


async def test_a_todo_can_be_reworded_and_deleted(signed_in: AsyncClient) -> None:
    project = await make_project(signed_in)
    picked = await pick(signed_in, project, await make_card(signed_in, project))
    todo = await add_todo(signed_in, picked["id"], "Read the RFC")

    reworded = await signed_in.patch(f"/today/todos/{todo['id']}", json={"text": "Skim it"})
    assert reworded.json()["text"] == "Skim it"

    deleted = await signed_in.delete(f"/today/todos/{todo['id']}")
    assert deleted.status_code == 200
    assert (await plan(signed_in, project))["picks"][0]["todos"] == []


async def test_a_blank_todo_is_refused(signed_in: AsyncClient) -> None:
    project = await make_project(signed_in)
    picked = await pick(signed_in, project, await make_card(signed_in, project))

    refused = await signed_in.post(f"/today/picks/{picked['id']}/todos", json={"text": "   "})

    assert refused.status_code == 422


async def test_a_plan_is_its_authors_alone(
    signed_in: AsyncClient, other_client: AsyncClient
) -> None:
    project = await make_project(signed_in)
    picked = await pick(signed_in, project, await make_card(signed_in, project))
    todo = await add_todo(signed_in, picked["id"], "Read the RFC")

    _, token = await open_account(
        signed_in,
        {"name": "Aditi K", "kind": "team", "title": "Engineer", "responsibilities": "Billing."},
        "aditi@example.com",
    )
    accepted = await other_client.post(
        "/auth/accept-invite", json={"token": token, "password": INVITEE_PASSWORD}
    )
    assert accepted.status_code == 200, accepted.text

    assert (await plan(other_client, project))["picks"] == []
    assert (
        await other_client.post(f"/today/picks/{picked['id']}/todos", json={"text": "x"})
    ).status_code == 404
    assert (
        await other_client.patch(f"/today/todos/{todo['id']}", json={"state": "done"})
    ).status_code == 404
    assert (await other_client.delete(f"/today/picks/{picked['id']}")).status_code == 404


async def test_the_bootstrap_session_has_no_plan(bootstrapped: AsyncClient) -> None:
    await make_project(bootstrapped)

    refused = await bootstrapped.get("/projects/ATL/today")

    assert refused.status_code == 403


# --- Wrapping up -------------------------------------------------------------


async def friday_with_three_todos(client: AsyncClient) -> tuple[str, str, list[dict]]:
    """A Friday with one card and three to-dos on it, the first already done."""
    project = await make_project(client)
    card = await make_card(client, project)
    picked = await pick(client, project, card)
    todos = [
        await add_todo(client, picked["id"], text)
        for text in ("Email @Neha Rao", "Check >Q4 invoices.xlsx", "Ask about retries")
    ]
    await client.patch(f"/today/todos/{todos[0]['id']}", json={"state": "done"})
    return project, card, todos


async def test_wrap_up_moves_the_chosen_and_leaves_the_rest(signed_in: AsyncClient) -> None:
    project, card, todos = await friday_with_three_todos(signed_in)

    wrapped = await signed_in.post(
        f"/projects/{project}/today/wrap-up",
        json={"day": FRIDAY, "to_day": MONDAY, "move": [todos[1]["id"]]},
    )

    assert wrapped.status_code == 200, wrapped.text
    assert (wrapped.json()["moved"], wrapped.json()["not_done"]) == (1, 1)

    friday = (await plan(signed_in, project))["picks"][0]["todos"]
    assert [(t["state"], t["moved_to"]) for t in friday] == [
        ("done", None),
        ("moved", MONDAY),
        ("not_done", None),
    ]

    monday = (await plan(signed_in, project, MONDAY))["picks"]
    assert [p["task"]["reference"] for p in monday] == [card]
    assert [(t["text"], t["state"], t["carried_from"]) for t in monday[0]["todos"]] == [
        ("Check >Q4 invoices.xlsx", "open", FRIDAY)
    ]


async def test_wrap_up_joins_a_card_already_on_the_next_day(signed_in: AsyncClient) -> None:
    project, card, todos = await friday_with_three_todos(signed_in)
    monday_pick = await pick(signed_in, project, card, MONDAY)
    await add_todo(signed_in, monday_pick["id"], "Ship it")

    await signed_in.post(
        f"/projects/{project}/today/wrap-up",
        json={"day": FRIDAY, "to_day": MONDAY, "move": [todos[1]["id"], todos[2]["id"]]},
    )

    monday = (await plan(signed_in, project, MONDAY))["picks"]
    assert len(monday) == 1
    assert [t["text"] for t in monday[0]["todos"]] == [
        "Ship it",
        "Check >Q4 invoices.xlsx",
        "Ask about retries",
    ]


async def test_wrap_up_only_moves_forward(signed_in: AsyncClient) -> None:
    project, _, todos = await friday_with_three_todos(signed_in)

    refused = await signed_in.post(
        f"/projects/{project}/today/wrap-up",
        json={"day": FRIDAY, "to_day": FRIDAY, "move": [todos[1]["id"]]},
    )

    assert refused.status_code == 422


async def test_wrap_up_refuses_a_todo_that_is_not_open(signed_in: AsyncClient) -> None:
    project, _, todos = await friday_with_three_todos(signed_in)

    refused = await signed_in.post(
        f"/projects/{project}/today/wrap-up",
        json={"day": FRIDAY, "to_day": MONDAY, "move": [todos[0]["id"]]},
    )

    assert refused.status_code == 422
    assert refused.json()["error"]["details"]["not_open_on_this_day"] == [todos[0]["id"]]


async def test_a_moved_todo_is_changed_on_its_new_day(signed_in: AsyncClient) -> None:
    project, _, todos = await friday_with_three_todos(signed_in)
    await signed_in.post(
        f"/projects/{project}/today/wrap-up",
        json={"day": FRIDAY, "to_day": SATURDAY, "move": [todos[1]["id"]]},
    )

    refused = await signed_in.patch(f"/today/todos/{todos[1]['id']}", json={"state": "done"})

    assert refused.status_code == 422
    assert "Saturday 17 October" in refused.json()["error"]["message"]


async def test_a_todo_left_behind_can_be_reopened(signed_in: AsyncClient) -> None:
    project, _, todos = await friday_with_three_todos(signed_in)
    await signed_in.post(
        f"/projects/{project}/today/wrap-up",
        json={"day": FRIDAY, "to_day": MONDAY, "move": []},
    )

    reopened = await signed_in.patch(f"/today/todos/{todos[2]['id']}", json={"state": "open"})

    assert reopened.json()["state"] == "open"


# --- The day report ----------------------------------------------------------


async def test_the_report_carries_the_plan(signed_in: AsyncClient) -> None:
    project, card, todos = await friday_with_three_todos(signed_in)
    await signed_in.post(
        f"/projects/{project}/today/wrap-up",
        json={"day": FRIDAY, "to_day": MONDAY, "move": [todos[1]["id"]]},
    )

    report = (
        await signed_in.get(f"/projects/{project}/reports/day", params={"date": FRIDAY})
    ).json()

    assert [card_["reference"] for card_ in report["todos"]] == [card]
    assert [t["state"] for t in report["todos"][0]["todos"]] == ["done", "moved", "not_done"]
    assert report["headline"].endswith("1 of 3 to-dos done, 1 moved to Monday, 1 not done.")
    markdown = report["markdown"]
    assert "## To-dos" in markdown
    assert f"### {card} — Invoice PDF export (To do)" in markdown
    assert "- [x] Email @Neha Rao" in markdown
    assert "- [ ] Check >Q4 invoices.xlsx → moved to Mon 19 Oct" in markdown
    assert "- [ ] Ask about retries — not done" in markdown
    # The plan comes straight after the headline, before what happened to
    # the cards: it is what the day was meant to hold.
    assert markdown.splitlines()[4] == "## To-dos"


async def test_the_report_says_where_a_todo_was_carried_from(signed_in: AsyncClient) -> None:
    project, _, todos = await friday_with_three_todos(signed_in)
    await signed_in.post(
        f"/projects/{project}/today/wrap-up",
        json={"day": FRIDAY, "to_day": MONDAY, "move": [todos[1]["id"]]},
    )

    report = (
        await signed_in.get(f"/projects/{project}/reports/day", params={"date": MONDAY})
    ).json()

    assert "- [ ] Check >Q4 invoices.xlsx (carried from Fri)" in report["markdown"]


async def test_a_day_nobody_planned_reports_as_before(signed_in: AsyncClient) -> None:
    project = await make_project(signed_in)

    report = (
        await signed_in.get(f"/projects/{project}/reports/day", params={"date": FRIDAY})
    ).json()

    assert report["todos"] == []
    assert "to-do" not in report["headline"]
    assert "## To-dos" not in report["markdown"]


async def test_a_day_of_only_planning_leads_with_the_plan(signed_in: AsyncClient) -> None:
    project, _, _ = await friday_with_three_todos(signed_in)
    # The project and card were made today, not on Friday, so Friday's trail
    # is empty and its plan is all there is to say.
    report = (
        await signed_in.get(f"/projects/{project}/reports/day", params={"date": FRIDAY})
    ).json()

    assert report["entry_count"] == 0
    assert report["headline"] == "1 of 3 to-dos done."


# --- What a freshly picked card brings with it --------------------------------


async def test_a_picked_card_brings_what_is_outstanding_on_it(signed_in: AsyncClient) -> None:
    project = await make_project(signed_in)
    card = await make_card(signed_in, project)
    staged = await signed_in.patch(
        f"/tasks/{card}", json={"sub_statuses": ["Spec", "Build", "Review"]}
    )
    assert staged.status_code == 200, staged.text
    await signed_in.post(f"/tasks/{card}/sub-status", json={"index": 1})
    subs = []
    for title in ("Layout", "Totals row", "Old template"):
        made = await signed_in.post(
            f"/tasks/{card}/subtasks",
            json={"title": title, "description": "Part of it.", "type": "feature"},
        )
        assert made.status_code == 201, made.text
        subs.append(made.json()["reference"])
    await signed_in.post(f"/tasks/{subs[0]}/finish", json={"finished": True})
    await signed_in.post(
        f"/tasks/{subs[2]}/status", json={"status": "cancelled", "reason": "Not needed."}
    )
    await signed_in.post(f"/tasks/{card}/checklist", json={"title": "Tell support"})

    picked = await pick(signed_in, project, card)

    assert [todo["text"] for todo in picked["todos"]] == [
        "Stage: Build",
        "Stage: Review",
        f"{subs[1]} Totals row",
        "Tell support",
    ]
    assert {todo["state"] for todo in picked["todos"]} == {"open"}


async def test_a_deleted_import_stays_deleted(signed_in: AsyncClient) -> None:
    project = await make_project(signed_in)
    card = await make_card(signed_in, project)
    await signed_in.post(f"/tasks/{card}/checklist", json={"title": "Tell support"})
    first = await pick(signed_in, project, card, TODAY_HERE)
    await signed_in.delete(f"/today/todos/{first['todos'][0]['id']}")

    assert (await plan(signed_in, project, TODAY_HERE))["picks"][0]["todos"] == []


async def test_a_card_with_nothing_outstanding_starts_empty(signed_in: AsyncClient) -> None:
    project = await make_project(signed_in)

    picked = await pick(signed_in, project, await make_card(signed_in, project))

    assert picked["todos"] == []


# --- A plan from today on follows its cards -------------------------------------


async def card_with_parts(client: AsyncClient) -> tuple[str, str, str]:
    """A card with three stages, the first in hand, and one sub-task."""
    project = await make_project(client)
    card = await make_card(client, project)
    await client.patch(f"/tasks/{card}", json={"sub_statuses": ["Spec", "Build", "Review"]})
    made = await client.post(
        f"/tasks/{card}/subtasks",
        json={"title": "Layout", "description": "Part of it.", "type": "feature"},
    )
    return project, card, made.json()["reference"]


def todo_named(day: dict, text: str) -> dict:
    return next(t for p in day["picks"] for t in p["todos"] if t["text"] == text)


async def test_what_is_added_to_the_card_later_comes_onto_today(signed_in: AsyncClient) -> None:
    project, card, _ = await card_with_parts(signed_in)
    await pick(signed_in, project, card, TODAY_HERE)

    made = await signed_in.post(
        f"/tasks/{card}/subtasks",
        json={"title": "Totals row", "description": "Part of it.", "type": "feature"},
    )
    await signed_in.post(f"/tasks/{card}/checklist", json={"title": "Tell support"})

    texts = [t["text"] for t in (await plan(signed_in, project, TODAY_HERE))["picks"][0]["todos"]]
    assert texts[-2:] == [f"{made.json()['reference']} Totals row", "Tell support"]


async def test_a_day_gone_by_keeps_the_plan_it_had(signed_in: AsyncClient) -> None:
    project, card, _ = await card_with_parts(signed_in)
    before = await pick(signed_in, project, card, FRIDAY)
    await signed_in.post(f"/tasks/{card}/checklist", json={"title": "Tell support"})

    friday = (await plan(signed_in, project, FRIDAY))["picks"][0]["todos"]

    assert len(friday) == len(before["todos"])


async def test_ticking_a_sub_task_on_the_plan_finishes_it_on_the_card(
    signed_in: AsyncClient,
) -> None:
    project, card, sub = await card_with_parts(signed_in)
    await pick(signed_in, project, card, TODAY_HERE)
    todo = todo_named(await plan(signed_in, project, TODAY_HERE), f"{sub} Layout")
    assert todo["source_kind"] == "subtask"

    await signed_in.patch(f"/today/todos/{todo['id']}", json={"state": "done"})
    assert (await signed_in.get(f"/tasks/{sub}")).json()["finished_at"] is not None

    await signed_in.patch(f"/today/todos/{todo['id']}", json={"state": "open"})
    assert (await signed_in.get(f"/tasks/{sub}")).json()["finished_at"] is None


async def test_finishing_a_sub_task_on_the_card_ticks_it_on_the_plan(
    signed_in: AsyncClient,
) -> None:
    project, card, sub = await card_with_parts(signed_in)
    await pick(signed_in, project, card, TODAY_HERE)

    await signed_in.post(f"/tasks/{sub}/finish", json={"finished": True})

    day = await plan(signed_in, project, TODAY_HERE)
    assert todo_named(day, f"{sub} Layout")["state"] == "done"


async def test_ticking_a_stage_moves_the_card_on(signed_in: AsyncClient) -> None:
    project, card, _ = await card_with_parts(signed_in)
    await pick(signed_in, project, card, TODAY_HERE)
    spec = todo_named(await plan(signed_in, project, TODAY_HERE), "Stage: Spec")
    assert spec["source_kind"] == "stage"

    await signed_in.patch(f"/today/todos/{spec['id']}", json={"state": "done"})

    assert (await signed_in.get(f"/tasks/{card}")).json()["sub_status_index"] == 1
    history = (await signed_in.get(f"/tasks/{card}/history")).json()["entries"]
    assert "task.sub_status_moved" in [entry["verb"] for entry in history]


async def test_moving_the_stage_on_the_card_ticks_the_plan(signed_in: AsyncClient) -> None:
    project, card, _ = await card_with_parts(signed_in)
    await pick(signed_in, project, card, TODAY_HERE)

    await signed_in.post(f"/tasks/{card}/sub-status", json={"index": 2})

    day = await plan(signed_in, project, TODAY_HERE)
    assert [todo_named(day, f"Stage: {s}")["state"] for s in ("Spec", "Build", "Review")] == [
        "done",
        "done",
        "open",
    ]

    # And back: unticking Build on the plan puts the card back on Build.
    build = todo_named(day, "Stage: Build")
    await signed_in.patch(f"/today/todos/{build['id']}", json={"state": "open"})
    assert (await signed_in.get(f"/tasks/{card}")).json()["sub_status_index"] == 1


async def test_ticking_a_tick_box_ticks_it_on_the_card(signed_in: AsyncClient) -> None:
    project, card, _ = await card_with_parts(signed_in)
    await signed_in.post(f"/tasks/{card}/checklist", json={"title": "Tell support"})
    await pick(signed_in, project, card, TODAY_HERE)
    todo = todo_named(await plan(signed_in, project, TODAY_HERE), "Tell support")

    await signed_in.patch(f"/today/todos/{todo['id']}", json={"state": "done"})

    checklist = (await signed_in.get(f"/tasks/{card}")).json()["checklist"]
    assert [item["state"] for item in checklist] == ["done"]


async def test_a_written_todo_never_touches_the_card(signed_in: AsyncClient) -> None:
    project, card, _ = await card_with_parts(signed_in)
    picked = await pick(signed_in, project, card, TODAY_HERE)
    mine = await add_todo(signed_in, picked["id"], "Email the client")

    await signed_in.patch(f"/today/todos/{mine['id']}", json={"state": "done"})

    assert mine["source_kind"] is None
    assert (await signed_in.get(f"/tasks/{card}")).json()["sub_status_index"] == 0
