"""A day's report.

Every fact in a report comes out of the audit trail, so these tests make the
day happen through the API — create a card, move it, tick something off — and
then ask what the day looked like. Nothing writes an ``activity`` row by hand:
a report of rows no endpoint produces would prove nothing.

Where a test needs an entry at a particular *moment*, it moves the row's
``occurred_at`` afterwards. That is the one thing the API cannot express, and
the boundary between one day and the next is most of what this feature is.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

TODAY = "2026-09-02"

ADITI = {
    "name": "Aditi K",
    "kind": "team",
    "role": "Backend engineer",
    "responsibilities": "Payments and webhooks.",
}

SETUP_ENTRIES = 2
"""What :func:`make_project` itself leaves in the day: the project, and its
membership. Both are changes to the project, so a report of the day it was
started counts them — which is why the tests that count say so out loud."""


async def make_project(client: AsyncClient, key: str = "ATL") -> str:
    """A project with one member, who can then own the cards."""
    await client.post("/projects", json={"key": key, "name": "Atlas migration"})
    person = (await client.post("/people", json={**ADITI, "name": f"Aditi {key}"})).json()["id"]
    await client.put(f"/projects/{key}/members", json={"person_ids": [person]})
    return key


async def make_task(client: AsyncClient, project: str, title: str = "Wire up billing") -> str:
    members = (await client.get(f"/projects/{project}/members")).json()["members"]
    created = await client.post(
        f"/projects/{project}/tasks",
        json={
            "title": title,
            "description": "What done looks like.",
            "type": "feature",
            "due_date": "2026-09-30",
            "assignee_id": members[0]["id"],
        },
    )
    assert created.status_code == 201, created.text
    return str(created.json()["reference"])


async def widen_board(client: AsyncClient, project: str) -> list[dict[str, str]]:
    """A board with somewhere to pass through: To do, In progress, Dev, Done.

    A new board is two columns wide, and a card cannot be walked across a
    board that has no middle.
    """
    board = (await client.get(f"/projects/{project}/columns")).json()["columns"]
    done = board[-1]["id"]
    for name in ("In progress", "Dev"):
        added = await client.post(
            f"/projects/{project}/columns", json={"name": name, "description": f"In {name}."}
        )
        assert added.status_code == 201, added.text
    # A column is added on the right, so Done needs putting back at the end.
    widened = (await client.get(f"/projects/{project}/columns")).json()["columns"]
    order = [column["id"] for column in widened if column["id"] != done] + [done]
    reordered = await client.put(f"/projects/{project}/columns/order", json={"column_ids": order})
    assert reordered.status_code == 200, reordered.text
    return list(reordered.json()["columns"])


async def move_everything_to(session: AsyncSession, moment: datetime) -> None:
    """Put every entry recorded so far at one instant.

    The report is cut by time, and a test cannot control the clock the server
    stamps rows with. Rewriting the stamps is the smallest way to say "these
    things happened then" without reaching past the API to say what happened.
    """
    await session.execute(text("UPDATE activity SET occurred_at = :moment"), {"moment": moment})
    await session.commit()


@pytest.fixture
async def project(signed_in: AsyncClient) -> str:
    return await make_project(signed_in)


# --- What the day covers ---------------------------------------------------


async def test_reports_todays_work_grouped_by_card(signed_in: AsyncClient, project: str) -> None:
    reference = await make_task(signed_in, project)
    board = (await signed_in.get(f"/projects/{project}/columns")).json()["columns"]
    await signed_in.post(
        f"/tasks/{reference}/move", json={"column_id": board[-1]["id"], "position": 0}
    )

    report = (await signed_in.get(f"/projects/{project}/reports/day")).json()

    assert [card["reference"] for card in report["tasks"]] == [reference]
    assert [entry["verb"] for entry in report["tasks"][0]["entries"]] == [
        "task.created",
        "task.moved",
    ]
    assert report["entry_count"] == SETUP_ENTRIES + 2  # the card, and the move


async def test_names_the_cards_that_reached_the_last_column(
    signed_in: AsyncClient, project: str
) -> None:
    """The headline of any day: what actually got finished."""
    done = await make_task(signed_in, project, "Finished today")
    ongoing = await make_task(signed_in, project, "Still going")
    board = (await signed_in.get(f"/projects/{project}/columns")).json()["columns"]
    await signed_in.post(f"/tasks/{done}/move", json={"column_id": board[-1]["id"], "position": 0})

    report = (await signed_in.get(f"/projects/{project}/reports/day")).json()

    assert report["finished"] == [done]
    assert {card["reference"]: card["finished"] for card in report["tasks"]} == {
        done: True,
        ongoing: False,
    }


async def test_changes_that_are_not_about_a_card_are_kept_apart(
    signed_in: AsyncClient, project: str
) -> None:
    """A file uploaded is part of the day, but it is not work on a card."""
    tree = (await signed_in.get(f"/projects/{project}/tree")).json()
    await signed_in.post(
        f"/folders/{tree['id']}/links",
        json={"name": "Signed MSA", "url": "https://example.com/msa", "source": "sharepoint"},
    )

    report = (await signed_in.get(f"/projects/{project}/reports/day")).json()

    assert report["tasks"] == []
    summaries = [entry["summary"] for entry in report["elsewhere"]]
    assert "Linked “Signed MSA” from SharePoint." in summaries


async def test_another_projects_day_is_not_in_this_ones(signed_in: AsyncClient) -> None:
    await make_project(signed_in, "ATL")
    await make_project(signed_in, "ORB")
    reference = await make_task(signed_in, "ORB")

    report = (await signed_in.get("/projects/ATL/reports/day")).json()

    assert reference not in [card["reference"] for card in report["tasks"]]


async def test_a_card_dragged_within_its_own_column_is_not_a_change(
    signed_in: AsyncClient, project: str
) -> None:
    """Same rule as a card's history: a no-op move is not something that happened."""
    reference = await make_task(signed_in, project)
    board = (await signed_in.get(f"/projects/{project}/columns")).json()["columns"]
    await signed_in.post(
        f"/tasks/{reference}/move", json={"column_id": board[0]["id"], "position": 0}
    )

    report = (await signed_in.get(f"/projects/{project}/reports/day")).json()

    assert [entry["verb"] for entry in report["tasks"][0]["entries"]] == ["task.created"]


async def test_a_quiet_day_says_so(signed_in: AsyncClient, project: str) -> None:
    report = (await signed_in.get(f"/projects/{project}/reports/day?date=2020-01-01")).json()

    assert report["entry_count"] == 0
    assert report["tasks"] == []
    assert report["headline"] == "Nothing was recorded on Wednesday 1 January 2020."
    assert report["markdown"].startswith("# Atlas migration — Wednesday 1 January 2020")


async def test_a_deleted_card_keeps_the_title_it_had(signed_in: AsyncClient, project: str) -> None:
    """The day a card was dropped is the day most worth reporting it."""
    reference = await make_task(signed_in, project, "Abandoned idea")
    await signed_in.delete(f"/tasks/{reference}")

    report = (await signed_in.get(f"/projects/{project}/reports/day")).json()

    (card,) = report["tasks"]
    assert card["reference"] == reference
    assert card["title"] == "Abandoned idea"
    assert card["column"] is None
    assert card["parent"] is None


async def test_a_finished_subtask_counts_as_finished(signed_in: AsyncClient, project: str) -> None:
    """A sub-task has no last column to end the day in, so ticking it is what
    finishing it looks like in the report."""
    parent = await make_task(signed_in, project)
    members = (await signed_in.get(f"/projects/{project}/members")).json()["members"]
    subtask = (
        await signed_in.post(
            f"/tasks/{parent}/subtasks",
            json={
                "title": "Drain the old queue",
                "description": "What done looks like.",
                "type": "chore",
                "assignee_id": members[0]["id"],
            },
        )
    ).json()["reference"]

    await signed_in.post(f"/tasks/{subtask}/finish", json={"finished": True})

    report = (await signed_in.get(f"/projects/{project}/reports/day")).json()
    child = next(card for card in report["tasks"] if card["reference"] == subtask)
    assert child["finished"] is True
    assert subtask in report["finished"]
    # No column, but named under the card it belongs to rather than as deleted.
    assert child["column"] is None
    assert child["parent"] == parent
    assert f"(of {parent})" in report["markdown"]


async def test_a_reopened_subtask_did_not_finish_today(
    signed_in: AsyncClient, project: str
) -> None:
    parent = await make_task(signed_in, project)
    members = (await signed_in.get(f"/projects/{project}/members")).json()["members"]
    subtask = (
        await signed_in.post(
            f"/tasks/{parent}/subtasks",
            json={
                "title": "Drain the old queue",
                "description": "What done looks like.",
                "type": "chore",
                "assignee_id": members[0]["id"],
            },
        )
    ).json()["reference"]

    await signed_in.post(f"/tasks/{subtask}/finish", json={"finished": True})
    await signed_in.post(f"/tasks/{subtask}/finish", json={"finished": False})

    report = (await signed_in.get(f"/projects/{project}/reports/day")).json()
    assert subtask not in report["finished"]


# --- What it leaves out ----------------------------------------------------


async def test_a_days_moves_read_as_the_one_move_they_amounted_to(
    signed_in: AsyncClient, project: str
) -> None:
    """A card walked across the board is one step forward, not three."""
    board = await widen_board(signed_in, project)
    reference = await make_task(signed_in, project)
    for column in board[1:3]:  # To do → In progress → Dev
        await signed_in.post(
            f"/tasks/{reference}/move", json={"column_id": column["id"], "position": 0}
        )

    report = (await signed_in.get(f"/projects/{project}/reports/day")).json()

    (card,) = report["tasks"]
    assert [entry["summary"] for entry in card["entries"]] == [
        "Created this task.",
        "Moved from To do to Dev.",
    ]
    moved = card["entries"][-1]
    assert moved["payload"]["moves"] == 2
    assert moved["changes"] == [
        {"field": "column", "label": "column", "from": "To do", "to": "Dev"}
    ]


async def test_the_card_keeps_every_move_in_its_own_history(
    signed_in: AsyncClient, project: str
) -> None:
    """The report is an account of the day; the history is the record."""
    board = await widen_board(signed_in, project)
    reference = await make_task(signed_in, project)
    for column in board[1:3]:
        await signed_in.post(
            f"/tasks/{reference}/move", json={"column_id": column["id"], "position": 0}
        )

    history = (await signed_in.get(f"/tasks/{reference}/history")).json()

    assert [entry["summary"] for entry in history["entries"]] == [
        "Moved from In progress to Dev.",
        "Moved from To do to In progress.",
        "Created this task.",
    ]


async def test_a_card_that_went_somewhere_and_came_back_says_so(
    signed_in: AsyncClient, project: str
) -> None:
    """One line either way, and silence would read as a card that sat still."""
    board = (await signed_in.get(f"/projects/{project}/columns")).json()["columns"]
    reference = await make_task(signed_in, project)
    for column in (board[-1], board[0]):
        await signed_in.post(
            f"/tasks/{reference}/move", json={"column_id": column["id"], "position": 0}
        )

    report = (await signed_in.get(f"/projects/{project}/reports/day")).json()

    (card,) = report["tasks"]
    assert card["entries"][-1]["summary"] == "Moved out and back to To do."


async def test_a_card_pulled_back_out_of_the_last_column_did_not_finish(
    signed_in: AsyncClient, project: str
) -> None:
    board = await widen_board(signed_in, project)
    reference = await make_task(signed_in, project)
    for column in (board[-1], board[1]):  # into Done, then back to In progress
        await signed_in.post(
            f"/tasks/{reference}/move", json={"column_id": column["id"], "position": 0}
        )

    report = (await signed_in.get(f"/projects/{project}/reports/day")).json()

    assert report["finished"] == []
    assert [card["finished"] for card in report["tasks"]] == [False]
    assert report["tasks"][0]["entries"][-1]["summary"] == "Moved from To do to In progress."


async def test_two_edits_an_hour_apart_are_still_two_things(
    signed_in: AsyncClient, project: str
) -> None:
    """Only moves collapse. A day that reported every save as one would hide
    half of itself."""
    reference = await make_task(signed_in, project)
    await signed_in.patch(f"/tasks/{reference}", json={"title": "Wire up billing properly"})
    await signed_in.patch(f"/tasks/{reference}", json={"priority": "urgent"})

    report = (await signed_in.get(f"/projects/{project}/reports/day")).json()

    assert [entry["summary"] for entry in report["tasks"][0]["entries"]] == [
        "Created this task.",
        "Changed the title.",
        "Changed the priority.",
    ]


async def test_the_headline_counts_the_lines_it_shows(signed_in: AsyncClient, project: str) -> None:
    """A count that disagreed with the lines under it would look like a bug."""
    board = (await signed_in.get(f"/projects/{project}/columns")).json()["columns"]
    reference = await make_task(signed_in, project)
    for column in (board[-1], board[0]):
        await signed_in.post(
            f"/tasks/{reference}/move", json={"column_id": column["id"], "position": 0}
        )

    report = (await signed_in.get(f"/projects/{project}/reports/day")).json()

    shown = sum(len(card["entries"]) for card in report["tasks"]) + len(report["elsewhere"])
    assert shown == SETUP_ENTRIES + 2  # the card, and the two moves as one
    assert report["entry_count"] == shown
    assert report["headline"] == "4 changes across 1 card, and 2 elsewhere on the project."


async def test_a_comment_is_quoted_in_the_note(signed_in: AsyncClient, project: str) -> None:
    """ "Added a comment." is a line that sends you back to the card."""
    reference = await make_task(signed_in, project)
    members = (await signed_in.get(f"/projects/{project}/members")).json()["members"]
    await signed_in.post(
        f"/tasks/{reference}/comments",
        json={"body": "Waiting on the sandbox key.", "author_id": members[0]["id"]},
    )

    report = (await signed_in.get(f"/projects/{project}/reports/day")).json()

    assert "Commented: \u201cWaiting on the sandbox key.\u201d" in report["markdown"]


# --- Where the day starts and ends -----------------------------------------


async def test_the_day_is_cut_in_the_callers_zone_not_in_utc(
    signed_in: AsyncClient, session: AsyncSession, project: str
) -> None:
    """A 9pm change in Kolkata belongs to that evening, not to the next day."""
    await make_task(signed_in, project)
    await move_everything_to(session, datetime(2026, 9, 2, 20, 30, tzinfo=UTC))  # 02:00 IST, 3rd

    in_utc = (
        await signed_in.get(f"/projects/{project}/reports/day?date={TODAY}&timezone=UTC")
    ).json()
    in_kolkata = (
        await signed_in.get(f"/projects/{project}/reports/day?date={TODAY}&timezone=Asia/Kolkata")
    ).json()
    next_day_in_kolkata = (
        await signed_in.get(
            f"/projects/{project}/reports/day?date=2026-09-03&timezone=Asia/Kolkata"
        )
    ).json()

    assert in_utc["entry_count"] > 0
    assert in_kolkata["entry_count"] == 0
    assert next_day_in_kolkata["entry_count"] == in_utc["entry_count"]


async def test_the_window_it_used_comes_back_with_the_report(
    signed_in: AsyncClient, project: str
) -> None:
    report = (
        await signed_in.get(f"/projects/{project}/reports/day?date={TODAY}&timezone=Asia/Kolkata")
    ).json()

    # 00:00 IST is 18:30 UTC the day before, and the end is exclusive.
    assert report["starts_at"].startswith("2026-09-01T18:30")
    assert report["ends_at"].startswith("2026-09-02T18:30")
    assert report["timezone"] == "Asia/Kolkata"


async def test_midnight_belongs_to_the_day_it_begins(
    signed_in: AsyncClient, session: AsyncSession, project: str
) -> None:
    """Consecutive days tile: an entry lands in one report, never in two."""
    await make_task(signed_in, project)
    await move_everything_to(session, datetime(2026, 9, 2, 0, 0, tzinfo=UTC))

    second = (await signed_in.get(f"/projects/{project}/reports/day?date={TODAY}")).json()
    first = (await signed_in.get(f"/projects/{project}/reports/day?date=2026-09-01")).json()

    assert second["entry_count"] > 0
    assert first["entry_count"] == 0


async def test_a_misspelled_zone_is_cut_where_it_meant(
    signed_in: AsyncClient, session: AsyncSession, project: str
) -> None:
    """A letter short of Calcutta still gets a report, not a 422.

    The day has to come out cut in India — the same window "Asia/Kolkata"
    gives — and the report has to say which zone that was, so a caller can see
    what their name was read as.
    """
    await make_task(signed_in, project)
    await move_everything_to(session, datetime(2026, 9, 2, 20, 30, tzinfo=UTC))  # 02:00 IST, 3rd

    report = (
        await signed_in.get(f"/projects/{project}/reports/day?date={TODAY}&timezone=Asia/Cacutta")
    ).json()
    in_kolkata = (
        await signed_in.get(f"/projects/{project}/reports/day?date={TODAY}&timezone=Asia/Kolkata")
    ).json()

    assert report["timezone"] == "Asia/Calcutta"
    assert (report["starts_at"], report["ends_at"]) == (
        in_kolkata["starts_at"],
        in_kolkata["ends_at"],
    )
    assert report["entry_count"] == in_kolkata["entry_count"]


async def test_a_zone_named_in_the_wrong_case_is_still_that_zone(
    signed_in: AsyncClient, project: str
) -> None:
    report = (
        await signed_in.get(f"/projects/{project}/reports/day?date={TODAY}&timezone=asia/kolkata")
    ).json()

    assert report["timezone"] == "Asia/Kolkata"
    assert report["starts_at"].startswith("2026-09-01T18:30")


async def test_a_zone_the_server_does_not_know_is_a_clean_422(
    signed_in: AsyncClient, project: str
) -> None:
    response = await signed_in.get(
        f"/projects/{project}/reports/day", params={"timezone": "Mars/Olympus_Mons"}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unprocessable"
    assert "IANA" in response.json()["error"]["message"]


async def test_a_zone_that_could_be_two_zones_is_a_422_naming_both(
    signed_in: AsyncClient, project: str
) -> None:
    """Forgiving a typo stops where the typo stops having one reading."""
    response = await signed_in.get(
        f"/projects/{project}/reports/day", params={"timezone": "Asia/Macaz"}
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"]["suggestions"] == ["Asia/Macao", "Asia/Macau"]


async def test_an_unknown_project_is_a_404(signed_in: AsyncClient) -> None:
    response = await signed_in.get("/projects/NOPE/reports/day")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


# --- The note it hands you -------------------------------------------------


async def test_the_markdown_is_a_note_you_could_paste(
    signed_in: AsyncClient, session: AsyncSession, project: str
) -> None:
    reference = await make_task(signed_in, project, "Wire up billing")
    board = (await signed_in.get(f"/projects/{project}/columns")).json()["columns"]
    await signed_in.post(
        f"/tasks/{reference}/move", json={"column_id": board[-1]["id"], "position": 0}
    )
    await move_everything_to(session, datetime(2026, 9, 2, 9, 15, tzinfo=UTC))

    report = (
        await signed_in.get(f"/projects/{project}/reports/day?date={TODAY}&timezone=Asia/Kolkata")
    ).json()
    note = report["markdown"]

    assert note.startswith("# Atlas migration — Wednesday 2 September 2026")
    assert report["headline"] in note
    assert "## Finished" in note
    assert f"- **{reference}** Wire up billing" in note
    # 09:15 UTC is 14:45 in Kolkata: the note is written in the zone asked for.
    assert "- 14:45 · Moved from To do to Done." in note
    assert note.endswith("\n")


async def test_an_agents_work_is_named_in_the_note(signed_in: AsyncClient, project: str) -> None:
    """The report is an account of a person's day, so a bot's line says so."""
    issued = await signed_in.post(
        "/tokens", json={"name": "board agent", "scopes": ["read", "write"]}
    )
    agent = {"Authorization": f"Bearer {issued.json()['token']}"}
    reference = await make_task(signed_in, project)
    await signed_in.patch(
        f"/tasks/{reference}", json={"title": "Renamed by the agent"}, headers=agent
    )

    report = (await signed_in.get(f"/projects/{project}/reports/day")).json()

    assert "— board agent (agent)" in report["markdown"]
    channels = {entry["channel"] for card in report["tasks"] for entry in card["entries"]}
    assert channels == {"web", "api"}


async def test_the_headline_counts_what_the_day_held(signed_in: AsyncClient, project: str) -> None:
    done = await make_task(signed_in, project, "Finished today")
    await make_task(signed_in, project, "Still going")
    board = (await signed_in.get(f"/projects/{project}/columns")).json()["columns"]
    await signed_in.post(f"/tasks/{done}/move", json={"column_id": board[-1]["id"], "position": 0})

    report = (await signed_in.get(f"/projects/{project}/reports/day")).json()

    assert report["headline"] == (
        "5 changes across 2 cards, 1 of them finished, and 2 elsewhere on the project."
    )
