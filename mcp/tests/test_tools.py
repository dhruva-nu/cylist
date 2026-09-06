"""The tool layer, exercised through ``MCPServer.call_tool``."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
from mcp.server.mcpserver import MCPServer

from cylist_mcp.client import ApiClient
from cylist_mcp.server import build_server, local_timezone
from tests import fake_api
from tests.conftest import call

EXPECTED_TOOLS = {
    "list_projects",
    "get_project",
    "list_tasks",
    "get_task",
    "read_task_history",
    "create_task",
    "create_subtask",
    "finish_subtask",
    "add_checklist_item",
    "set_checklist_item",
    "move_task",
    "set_task_status",
    "list_goals",
    "get_goal",
    "create_goal",
    "set_task_goal",
    "set_goal_status",
    "add_comment",
    "list_people",
    "list_files",
    "add_link",
    "list_vault",
    "read_activity",
    "day_report",
}


# --- What is on offer ------------------------------------------------------


async def test_every_documented_tool_is_registered(server: MCPServer) -> None:
    names = {tool.name for tool in await server.list_tools()}
    assert names >= EXPECTED_TOOLS


async def test_reveal_secret_is_absent_without_the_scope(server: MCPServer) -> None:
    """A tool that would always 403 is worse than no tool at all."""
    names = {tool.name for tool in await server.list_tools()}
    assert "reveal_secret" not in names


async def test_reveal_secret_appears_with_the_scope(
    make_server: Callable[..., MCPServer],
) -> None:
    scoped = make_server(scopes=("read", "write", "vault:read", "vault:reveal"))
    names = {tool.name for tool in await scoped.list_tools()}
    assert "reveal_secret" in names


async def test_vault_read_alone_is_not_enough(make_server: Callable[..., MCPServer]) -> None:
    """vault:read sees structure; only vault:reveal decrypts."""
    scoped = make_server(scopes=("read", "write", "vault:read"))
    names = {tool.name for tool in await scoped.list_tools()}
    assert "list_vault" in names
    assert "reveal_secret" not in names


async def test_every_tool_has_a_description_and_documented_arguments(
    make_server: Callable[..., MCPServer],
) -> None:
    scoped = make_server(scopes=("read", "write", "vault:reveal"))
    for tool in await scoped.list_tools():
        assert tool.description, f"{tool.name} has no description"
        assert len(tool.description) > 80, f"{tool.name}'s description is too thin to act on"
        properties = tool.input_schema.get("properties", {})
        for argument, schema in properties.items():
            assert schema.get("description"), f"{tool.name}.{argument} is undocumented"


# --- Reading ---------------------------------------------------------------


async def test_list_projects_returns_structured_content(server: MCPServer) -> None:
    result = await call(server, "list_projects")
    assert not result.is_error
    assert result.data["projects"][0]["key"] == "ATL"


async def test_get_project_includes_the_columns(server: MCPServer) -> None:
    """So a model can name a column in move_task without a second guess."""
    result = await call(server, "get_project", project="ATL")
    assert not result.is_error
    assert result.data["project"]["task_count"] == 1
    assert [column["name"] for column in result.data["columns"]] == ["Backlog", "In progress"]


async def test_list_tasks_filters_by_status(server: MCPServer) -> None:
    result = await call(server, "list_tasks", project="ATL", status="blocked")
    assert not result.is_error
    assert result.data["tasks"] == []


async def test_list_tasks_rejects_a_status_that_is_not_one(server: MCPServer) -> None:
    result = await call(server, "list_tasks", project="ATL", status="stuck")
    assert result.is_error
    assert "'active', 'hold', 'blocked'" in result.text


async def test_get_task_returns_the_task(server: MCPServer) -> None:
    result = await call(server, "get_task", task="ATL-2")
    assert not result.is_error
    assert result.data["task"]["reference"] == "ATL-2"


async def test_list_people_scoped_to_a_project(server: MCPServer) -> None:
    result = await call(server, "list_people", project="ATL", kind="client")
    assert not result.is_error
    assert {person["name"] for person in result.data["people"]} == {"Lena W", "Leo Wren"}


async def test_list_files_without_a_path_gives_the_top_level(server: MCPServer) -> None:
    result = await call(server, "list_files", project="ATL")
    assert not result.is_error
    assert result.data["folders"][0]["name"] == "Contracts"


async def test_list_files_walks_a_path(server: MCPServer) -> None:
    result = await call(server, "list_files", project="ATL", path="Contracts/2026")
    assert not result.is_error
    assert result.data["items"][0]["name"] == "Signed MSA"


async def test_read_task_history_says_what_changed_and_who_changed_it(
    server: MCPServer,
) -> None:
    result = await call(server, "read_task_history", task="ATL-2")

    assert not result.is_error
    entry = result.data["history"]["entries"][0]
    assert entry["summary"] == "Moved from To do to In progress."
    assert entry["actor_label"] == "board-tidy agent"
    assert entry["channel"] == "api"
    assert entry["changes"][0]["from"] == "To do"
    assert result.data["history"]["pages"] == 1


async def test_read_activity_passes_the_project_key_straight_through(
    server: MCPServer, recorder: fake_api.Recorder
) -> None:
    """No key-to-id round trip: the feed accepts a key directly."""
    result = await call(server, "read_activity", project="ATL", limit=5)
    assert not result.is_error
    query = recorder.sent("GET", "/activity").url.params
    assert query["project"] == "ATL"
    assert query["limit"] == "5"
    assert recorder.count("GET", "/projects/ATL") == 0


async def test_day_report_asks_for_the_day_in_a_named_zone(
    server: MCPServer, recorder: fake_api.Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TZ", "Asia/Kolkata")

    result = await call(server, "day_report", project="ATL", date="2026-02-03")

    assert not result.is_error
    query = recorder.sent("GET", "/projects/ATL/reports/day").url.params
    assert query["date"] == "2026-02-03"
    assert query["timezone"] == "Asia/Kolkata"
    assert result.data["report"]["finished"] == ["ATL-2"]


async def test_day_report_prefers_the_zone_it_was_given(
    server: MCPServer, recorder: fake_api.Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The machine's zone is a default, not a rule: an agent may be asked for
    the day as somebody else lived it."""
    monkeypatch.setenv("TZ", "Asia/Kolkata")

    result = await call(server, "day_report", project="ATL", timezone="Europe/Berlin")

    assert not result.is_error
    assert recorder.sent("GET", "/projects/ATL/reports/day").url.params["timezone"] == (
        "Europe/Berlin"
    )


async def test_day_report_carries_the_note_the_server_wrote(server: MCPServer) -> None:
    """The wording is the server's, so every client tells the same story."""
    result = await call(server, "day_report", project="ATL")

    assert result.data["report"]["markdown"].startswith("# Atlas migration")


async def test_the_local_zone_comes_from_tz_when_it_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A leading colon is legal in TZ and is not part of the name."""
    monkeypatch.setenv("TZ", ":Europe/Berlin")

    assert local_timezone() == "Europe/Berlin"


def test_the_local_zone_is_read_off_etc_localtime_without_tz(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`tzname()` would say "IST", which no zone database can be looked up by."""
    monkeypatch.delenv("TZ", raising=False)

    zone = local_timezone()

    # Whatever this machine is set to, it is either a nameable zone or nothing
    # at all — never an abbreviation, and never a path.
    assert zone is None or ("/" in zone or zone.isalpha())


# --- Writing ---------------------------------------------------------------


async def test_create_task_resolves_the_assignee(
    server: MCPServer, recorder: fake_api.Recorder
) -> None:
    result = await call(
        server,
        "create_task",
        project="ATL",
        title="Draft the cutover plan",
        description="A written plan with dates.",
        task_type="chore",
        due_date="2026-04-01",
        assignee="Aditi K",
    )
    assert not result.is_error
    body = recorder.body("POST", "/tasks")
    assert body["assignee_id"] == fake_api.ADITI_ID
    assert body["type"] == "chore"


async def test_create_task_omits_a_due_date_it_was_not_given(
    server: MCPServer, recorder: fake_api.Recorder
) -> None:
    """CYLIST-17. An absent date is left out of the body rather than sent as
    null: the card is simply undated, which is what the caller said."""
    result = await call(
        server,
        "create_task",
        project="ATL",
        title="Draft the cutover plan",
        description="A written plan with dates.",
        task_type="chore",
        assignee="Aditi K",
    )
    assert not result.is_error
    assert "due_date" not in recorder.body("POST", "/tasks")


async def test_create_subtask_omits_a_due_date_it_was_not_given(
    server: MCPServer, recorder: fake_api.Recorder
) -> None:
    result = await call(
        server,
        "create_subtask",
        task="ATL-2",
        title="Drain the old queue",
        description="Nothing is left in it before the cutover.",
        task_type="chore",
        assignee="Aditi K",
    )
    assert not result.is_error
    assert "due_date" not in recorder.body("POST", "/tasks/ATL-2/subtasks")


async def test_create_task_rejects_an_invented_type(server: MCPServer) -> None:
    result = await call(
        server,
        "create_task",
        project="ATL",
        title="x",
        description="y",
        task_type="epic",
        due_date="2026-04-01",
        assignee="Aditi K",
    )
    assert result.is_error
    assert "'feature', 'bug', 'chore'" in result.text


# --- Sub-tasks -------------------------------------------------------------


async def test_create_subtask_posts_under_the_parent(
    server: MCPServer, recorder: fake_api.Recorder
) -> None:
    result = await call(
        server,
        "create_subtask",
        task="ATL-2",
        title="Drain the old queue",
        description="Nothing is left in it before the cutover.",
        task_type="chore",
        due_date="2026-03-20",
        assignee="Aditi K",
    )
    assert not result.is_error
    assert result.data["task"]["reference"] == "ATL-2-1"
    assert recorder.body("POST", "/tasks/ATL-2/subtasks")["assignee_id"] == fake_api.ADITI_ID


async def test_create_subtask_resolves_the_assignee_against_the_right_project(
    server: MCPServer, recorder: fake_api.Recorder
) -> None:
    """The parent's key comes off the front of its reference, not the back.

    'ATL-2-1' rsplit on a dash would ask for the members of a project called
    'ATL-2', which does not exist.
    """
    result = await call(
        server,
        "create_subtask",
        task="ATL-2",
        title="Drain the old queue",
        description="Nothing is left in it before the cutover.",
        task_type="chore",
        due_date="2026-03-20",
        assignee="Aditi K",
    )
    assert not result.is_error
    assert recorder.sent("GET", "/projects/ATL/members")


async def test_a_subtask_is_addressed_by_its_own_reference(server: MCPServer) -> None:
    result = await call(server, "get_task", task="ATL-2-1")
    assert not result.is_error
    assert result.data["task"]["parent_reference"] == "ATL-2"
    # Not on the board, which is the one thing that tells it from a card.
    assert result.data["task"]["column_id"] is None


async def test_finish_subtask_ticks_it_off(server: MCPServer, recorder: fake_api.Recorder) -> None:
    result = await call(server, "finish_subtask", task="ATL-2-1")
    assert not result.is_error
    assert result.data["task"]["finished_at"] is not None
    assert recorder.body("POST", "/tasks/ATL-2-1/finish") == {"finished": True}


async def test_finish_subtask_reopens_one(server: MCPServer, recorder: fake_api.Recorder) -> None:
    result = await call(server, "finish_subtask", task="ATL-2-1", finished=False)
    assert not result.is_error
    assert result.data["task"]["finished_at"] is None
    assert recorder.body("POST", "/tasks/ATL-2-1/finish") == {"finished": False}


async def test_add_checklist_item_posts_the_title(
    server: MCPServer, recorder: fake_api.Recorder
) -> None:
    result = await call(server, "add_checklist_item", task="ATL-2", title="Tell support")
    assert not result.is_error
    assert result.data["item"]["state"] == "open"
    assert recorder.body("POST", "/tasks/ATL-2/checklist") == {"title": "Tell support"}


async def test_set_checklist_item_ticks_it(server: MCPServer, recorder: fake_api.Recorder) -> None:
    result = await call(server, "set_checklist_item", item=fake_api.CHECKLIST_ITEM_ID, state="done")
    assert not result.is_error
    assert result.data["item"]["state"] == "done"
    assert recorder.body("PATCH", f"/checklist/{fake_api.CHECKLIST_ITEM_ID}") == {"state": "done"}


async def test_set_checklist_item_rejects_a_state_that_is_not_one(server: MCPServer) -> None:
    result = await call(
        server, "set_checklist_item", item=fake_api.CHECKLIST_ITEM_ID, state="ticked"
    )
    assert result.is_error
    assert "'open', 'done', 'cancelled'" in result.text


async def test_set_checklist_item_needs_something_to_change(server: MCPServer) -> None:
    result = await call(server, "set_checklist_item", item=fake_api.CHECKLIST_ITEM_ID)
    assert result.is_error
    assert "Nothing to change" in result.text


async def test_cancelled_is_a_status_a_task_can_take(
    server: MCPServer, recorder: fake_api.Recorder
) -> None:
    result = await call(
        server,
        "set_task_status",
        task="ATL-2",
        status="cancelled",
        reason="The vendor withdrew the endpoint.",
    )
    assert not result.is_error
    assert recorder.body("POST", "/status")["status"] == "cancelled"


async def test_cancelling_without_a_reason_is_refused_locally(
    server: MCPServer, recorder: fake_api.Recorder
) -> None:
    result = await call(server, "set_task_status", task="ATL-2", status="cancelled")
    assert result.is_error
    assert recorder.count("POST", "/status") == 0


async def test_move_task_accepts_a_column_name(
    server: MCPServer, recorder: fake_api.Recorder
) -> None:
    result = await call(server, "move_task", task="ATL-2", column="Backlog", position=0)
    assert not result.is_error
    assert recorder.body("POST", "/move") == {"column_id": fake_api.BACKLOG_ID, "position": 0}


async def test_move_task_names_the_columns_that_exist(server: MCPServer) -> None:
    result = await call(server, "move_task", task="ATL-2", column="Shipped")
    assert result.is_error
    assert "No column called 'Shipped'" in result.text
    assert "Backlog, In progress" in result.text


async def test_set_task_status_requires_a_reason_locally(
    server: MCPServer, recorder: fake_api.Recorder
) -> None:
    """The model is told what to do, without spending a round trip on a 422."""
    result = await call(server, "set_task_status", task="ATL-2", status="blocked")
    assert result.is_error
    assert "A reason is required" in result.text
    assert result.data["error"]["details"]["field"] == "reason"
    assert not any(path.endswith("/status") for path in recorder.paths())


async def test_set_task_status_sends_the_reason_and_tags(
    server: MCPServer, recorder: fake_api.Recorder
) -> None:
    result = await call(
        server,
        "set_task_status",
        task="ATL-2",
        status="blocked",
        reason="waiting for the finance sign-off",
        waiting_on=["Lena W"],
    )
    assert not result.is_error
    assert recorder.body("POST", "/status") == {
        "status": "blocked",
        "waiting_on": [fake_api.LENA_ID],
        "reason": "waiting for the finance sign-off",
    }


async def test_add_comment_posts_as_the_agent_by_default(
    server: MCPServer, recorder: fake_api.Recorder
) -> None:
    result = await call(server, "add_comment", task="ATL-2", body="Chased finance again.")
    assert not result.is_error
    assert recorder.body("POST", "/comments") == {"body": "Chased finance again."}


async def test_add_link_resolves_the_folder_path(
    server: MCPServer, recorder: fake_api.Recorder
) -> None:
    result = await call(
        server,
        "add_link",
        project="ATL",
        folder="Contracts/2026",
        name="Signed MSA",
        url="https://example.invalid/msa",
        source="sharepoint",
    )
    assert not result.is_error
    assert recorder.body("POST", "/links")["source"] == "sharepoint"


async def test_add_link_rejects_upload_as_a_source(server: MCPServer) -> None:
    """'upload' means bytes we hold; a link never has those."""
    result = await call(
        server,
        "add_link",
        project="ATL",
        folder="Contracts/2026",
        name="x",
        url="https://example.invalid/x",
        source="upload",
    )
    assert result.is_error
    assert "source must be one of" in result.text


# --- Name resolution -------------------------------------------------------


async def test_an_ambiguous_name_is_refused_with_the_candidates(server: MCPServer) -> None:
    result = await call(
        server, "set_task_status", task="ATL-2", status="hold", reason="x", waiting_on=["Le"]
    )
    assert result.is_error
    assert "matches more than one person" in result.text
    assert "Lena W" in result.text
    assert "Leo Wren" in result.text


async def test_an_unknown_name_lists_what_exists(server: MCPServer) -> None:
    result = await call(
        server, "set_task_status", task="ATL-2", status="hold", reason="x", waiting_on=["Nobody"]
    )
    assert result.is_error
    assert "No person called 'Nobody'" in result.text
    assert "Aditi K" in result.text


# --- The vault -------------------------------------------------------------


async def test_list_vault_never_carries_a_value(server: MCPServer) -> None:
    result = await call(server, "list_vault", project="ATL", tree="Logins")
    assert not result.is_error
    assert fake_api.STRIPE_SECRET not in result.text
    assert "billing@example.com" in result.text


async def test_reveal_secret_returns_the_value_when_scoped(
    make_server: Callable[..., MCPServer],
) -> None:
    scoped = make_server(scopes=("read", "vault:read", "vault:reveal"))
    result = await call(scoped, "reveal_secret", project="ATL", path="Logins/Billing/Stripe")
    assert not result.is_error
    assert result.data["secret"]["value"] == fake_api.STRIPE_SECRET


async def test_reveal_secret_refuses_a_branch(make_server: Callable[..., MCPServer]) -> None:
    scoped = make_server(scopes=("read", "vault:reveal"))
    result = await call(scoped, "reveal_secret", project="ATL", path="Logins/Billing")
    assert result.is_error
    assert "is a branch" in result.text


# --- Failures --------------------------------------------------------------


async def test_an_api_error_becomes_a_readable_result_not_an_exception(
    make_server: Callable[..., MCPServer],
) -> None:
    server = make_server(
        overrides={
            ("GET", "/projects/ATL/summary"): httpx.Response(
                404,
                json={
                    "error": {
                        "code": "not_found",
                        "message": "No project matches 'ATL'.",
                        "details": {},
                    }
                },
            )
        }
    )
    result = await call(server, "get_project", project="ATL")
    assert result.is_error
    assert result.text == "No project matches 'ATL'."
    assert result.data["error"]["status"] == 404


async def test_a_403_says_the_token_needs_reissuing(
    make_server: Callable[..., MCPServer],
) -> None:
    """Retrying a scope failure is wasted turns; the model should be told."""
    server = make_server(
        scopes=("read", "write", "vault:reveal"),
        overrides={
            ("POST", f"/vault/nodes/{fake_api.STRIPE_ID}/reveal"): httpx.Response(
                403,
                json={
                    "error": {
                        "code": "forbidden",
                        "message": "This token does not hold `vault:reveal`.",
                        "details": {},
                    }
                },
            )
        },
    )
    result = await call(server, "reveal_secret", project="ATL", path="Logins/Billing/Stripe")
    assert result.is_error
    assert "reissued rather than retried" in result.text


async def test_an_unreachable_server_is_a_result_too() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = ApiClient(
        "http://cylist.test", "cyl_test_token", transport=httpx.MockTransport(refuse)
    )
    try:
        server = build_server(client, frozenset({"read"}))
        result = await call(server, "list_projects")
        assert result.is_error
        assert "Cannot reach the Cylist API" in result.text
    finally:
        await client.aclose()


async def test_calling_a_tool_that_is_not_registered_raises(server: MCPServer) -> None:
    """The SDK's own guard: an absent tool is not a silent no-op."""
    with pytest.raises(Exception, match="reveal_secret"):
        await server.call_tool("reveal_secret", {"project": "ATL", "path": "Logins/x"})


async def test_a_name_error_quotes_the_project_key_not_a_uuid(server: MCPServer) -> None:
    """A UUID in an error tells the model nothing it can act on."""
    result = await call(server, "move_task", task="ATL-2", column="Shipped")
    assert result.is_error
    assert "ATL's board" in result.text
    assert fake_api.PROJECT_ID not in result.text


# --- Goals -----------------------------------------------------------------


async def test_list_goals_returns_their_progress(server: MCPServer) -> None:
    result = await call(server, "list_goals", project="ATL")
    assert not result.is_error
    goal = result.data["goals"][0]
    assert goal["reference"] == "ATL-G1"
    assert goal["progress"]["done"] == 1


async def test_list_goals_can_ask_for_the_open_ones(
    server: MCPServer, recorder: fake_api.Recorder
) -> None:
    result = await call(server, "list_goals", project="ATL", open_only=True)
    assert not result.is_error
    assert recorder.sent("GET", "/goals").url.params["open_only"] == "true"


async def test_get_goal_takes_the_name_off_the_screen(server: MCPServer) -> None:
    """The point of resolving names: a model reads "Ledger cutover" and can
    use it without first finding a reference."""
    result = await call(server, "get_goal", project="ATL", goal="Ledger cutover")
    assert not result.is_error
    assert result.data["goal"]["reference"] == "ATL-G1"
    assert result.data["goal"]["tasks"][0]["reference"] == "ATL-2"


async def test_get_goal_takes_a_reference_too(server: MCPServer) -> None:
    result = await call(server, "get_goal", project="ATL", goal="ATL-G1")
    assert not result.is_error
    assert result.data["goal"]["name"] == "Ledger cutover"


async def test_an_unknown_goal_names_the_ones_that_exist(server: MCPServer) -> None:
    result = await call(server, "get_goal", project="ATL", goal="Nothing like it")
    assert result.is_error
    assert "Ledger cutover" in result.text


async def test_create_goal_resolves_the_owner_by_name(
    server: MCPServer, recorder: fake_api.Recorder
) -> None:
    result = await call(server, "create_goal", project="ATL", name="Search revamp", owner="Aditi")
    assert not result.is_error
    body = recorder.body("POST", "/goals")
    assert body["owner_id"] == fake_api.ADITI_ID
    # Nothing invented: no colour, no date, no description sent unasked.
    assert set(body) == {"name", "owner_id"}


async def test_set_task_goal_sends_the_goals_id(
    server: MCPServer, recorder: fake_api.Recorder
) -> None:
    result = await call(server, "set_task_goal", task="ATL-2", goal="Ledger cutover")
    assert not result.is_error
    assert recorder.body("PATCH", "/tasks/ATL-2") == {"goal_id": fake_api.GOAL_ID}
    assert result.data["task"]["goal_reference"] == "ATL-G1"


async def test_set_task_goal_with_no_goal_takes_the_card_off(
    server: MCPServer, recorder: fake_api.Recorder
) -> None:
    result = await call(server, "set_task_goal", task="ATL-2")
    assert not result.is_error
    assert recorder.body("PATCH", "/tasks/ATL-2") == {"goal_id": None}
    assert result.data["task"]["goal_id"] is None


async def test_set_goal_status_rejects_a_word_that_is_not_a_status(server: MCPServer) -> None:
    result = await call(server, "set_goal_status", project="ATL", goal="ATL-G1", status="done")
    assert result.is_error
    assert "'open', 'achieved'" in result.text


async def test_set_goal_status_sends_the_status(
    server: MCPServer, recorder: fake_api.Recorder
) -> None:
    result = await call(server, "set_goal_status", project="ATL", goal="ATL-G1", status="achieved")
    assert not result.is_error
    assert recorder.body("PATCH", "/goals/ATL-G1") == {"status": "achieved"}


async def test_create_task_can_name_the_goal_it_is_written_under(
    server: MCPServer, recorder: fake_api.Recorder
) -> None:
    result = await call(
        server,
        "create_task",
        project="ATL",
        title="Backfill the index",
        description="So search has something to search.",
        task_type="chore",
        assignee="Aditi",
        goal="Ledger cutover",
    )
    assert not result.is_error
    assert recorder.body("POST", "/tasks")["goal_id"] == fake_api.GOAL_ID
