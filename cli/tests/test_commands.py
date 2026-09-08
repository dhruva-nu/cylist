"""Every command, driven end to end against the fake API."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests import fake_api
from tests.conftest import Runner

# --- Projects --------------------------------------------------------------


def test_projects_defaults_to_listing(run: Runner) -> None:
    result = run("projects")
    assert result.code == 0
    assert "ATL" in result.out
    assert "Atlas migration" in result.out


def test_projects_json_is_the_api_response_verbatim(run: Runner) -> None:
    result = run("--json", "projects")
    assert result.code == 0
    assert json.loads(result.out) == [fake_api.PROJECT]


def test_project_show_uses_the_summary_endpoint(run: Runner, recorder: fake_api.Recorder) -> None:
    result = run("project", "show", "ATL")
    assert result.code == 0
    assert "GET /api/v1/projects/ATL/summary" in recorder.paths()
    assert "2 tasks in 3 columns" in result.out
    assert "1 blocked" in result.out


def test_project_new_posts_the_key_and_name(run: Runner, recorder: fake_api.Recorder) -> None:
    result = run("project", "new", "--key", "HER", "--name", "Hermes")
    assert result.code == 0
    assert recorder.body("POST", "/projects") == {
        "key": "HER",
        "name": "Hermes",
        "description": "",
    }


def test_project_members_add_sends_the_whole_list(run: Runner, recorder: fake_api.Recorder) -> None:
    """The API replaces membership, so an --add has to resend everyone."""
    result = run("project", "members", "ATL", "--remove", "Leo Wren")
    assert result.code == 0
    body = recorder.body("PUT", "/members")
    assert body["person_ids"] == [fake_api.ADITI_ID, fake_api.LENA_ID]


# --- Board -----------------------------------------------------------------


def test_board_renders_columns_side_by_side(run: Runner) -> None:
    result = run("board", "ATL")
    assert result.code == 0
    header = result.lines[0]
    # One line carrying every column name is the definition of "side by side".
    assert "BACKLOG" in header
    assert "IN PROGRESS" in header
    assert "DONE" in header
    assert header.index("BACKLOG") < header.index("IN PROGRESS") < header.index("DONE")


def test_board_marks_a_blocked_card_in_words(run: Runner) -> None:
    result = run("board", "ATL")
    assert "[blocked] ATL-2" in result.out


def test_board_says_when_an_agent_needs_you(run: Runner) -> None:
    """The border the web board draws, in words a terminal can carry."""
    result = run("board", "ATL")
    assert "agent: needs you" in result.out
    # One card only: ATL-1 has no session on it and says nothing about agents.
    assert result.out.count("agent:") == 1


def test_board_stacks_when_the_terminal_is_narrow(
    run: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cylist_cli import output

    monkeypatch.setattr(output, "terminal_width", lambda: 60)
    result = run("board", "ATL")
    assert result.code == 0
    assert result.lines[0] == "BACKLOG (1)"


# --- Tasks -----------------------------------------------------------------


def test_tasks_ls_lists_every_card(run: Runner) -> None:
    result = run("tasks", "ls", "ATL")
    assert result.code == 0
    assert "ATL-1" in result.out
    assert "ATL-2" in result.out
    assert "In progress" in result.out


def test_tasks_ls_filters_by_status_client_side(run: Runner) -> None:
    result = run("tasks", "ls", "ATL", "--status", "blocked")
    assert result.code == 0
    assert "ATL-2" in result.out
    assert "ATL-1" not in result.out


def test_tasks_ls_filters_by_assignee_name(run: Runner) -> None:
    result = run("tasks", "ls", "ATL", "--assignee", "Aditi K")
    assert result.code == 0
    assert "ATL-1" in result.out


def test_tasks_ls_rejects_an_unknown_status_before_any_request(
    run: Runner, recorder: fake_api.Recorder
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        run("tasks", "ls", "ATL", "--status", "sleeping")
    assert exit_info.value.code == 2
    assert recorder.paths() == []


def test_task_show_renders_the_timeline_with_names_not_ids(run: Runner) -> None:
    result = run("task", "show", "ATL-2")
    assert result.code == 0
    assert "Timeline" in result.out
    assert "active -> blocked" in result.out
    # meta.tagged holds ids; a person's name is what belongs on screen.
    assert "waiting on: Lena W" in result.out
    assert fake_api.LENA_ID not in result.out


def test_task_history_says_what_changed_and_who_changed_it(run: Runner) -> None:
    result = run("task", "history", "ATL-2")
    assert result.code == 0
    assert "Changed the due date and assignee." in result.out
    assert "due date: 2026-03-01 -> 2026-04-01" in result.out
    # An agent's work must not read as a person's.
    assert "board-tidy agent (agent)" in result.out
    assert "Web session\n" in result.out
    assert "Page 1 of 2 — 12 entries." in result.out


def test_task_new_resolves_the_assignee_and_normalises_the_date(
    run: Runner, recorder: fake_api.Recorder
) -> None:
    result = run(
        "task",
        "new",
        "ATL",
        "--title",
        "Draft the cutover plan",
        "--description",
        "A written plan with dates.",
        "--type",
        "chore",
        "--due",
        "2026-04-01",
        "--assignee",
        "Aditi K",
    )
    assert result.code == 0
    body = recorder.body("POST", "/tasks")
    assert body["assignee_id"] == fake_api.ADITI_ID
    assert body["due_date"] == "2026-04-01"
    assert body["type"] == "chore"


def test_task_new_without_a_due_date_omits_it(run: Runner, recorder: fake_api.Recorder) -> None:
    """CYLIST-17. No --due means an undated card, not a date the CLI picked."""
    result = run(
        "task",
        "new",
        "ATL",
        "--title",
        "Draft the cutover plan",
        "--description",
        "A written plan with dates.",
        "--type",
        "chore",
        "--assignee",
        "Aditi K",
    )
    assert result.code == 0
    assert "due_date" not in recorder.body("POST", "/tasks")


def test_task_new_rejects_a_date_that_is_not_a_date(run: Runner) -> None:
    result = run(
        "task",
        "new",
        "ATL",
        "--title",
        "x",
        "--description",
        "y",
        "--type",
        "bug",
        "--due",
        "next tuesday",
        "--assignee",
        "Aditi K",
    )
    assert result.code == 1
    assert "--due wants a date like 2026-03-31" in result.err


def test_task_finish_ticks_a_subtask_off(run: Runner, recorder: fake_api.Recorder) -> None:
    result = run("task", "finish", "ATL-2-1")
    assert result.code == 0
    assert "Finished ATL-2-1" in result.out
    assert recorder.body("POST", "/tasks/ATL-2-1/finish") == {"finished": True}


def test_task_finish_reopens_one(run: Runner, recorder: fake_api.Recorder) -> None:
    result = run("task", "finish", "ATL-2-1", "--reopen")
    assert result.code == 0
    assert "Reopened ATL-2-1" in result.out
    assert recorder.body("POST", "/tasks/ATL-2-1/finish") == {"finished": False}


def test_task_show_marks_an_open_subtask(run: Runner) -> None:
    result = run("task", "show", "ATL-2")
    assert result.code == 0
    assert "[ ] ATL-2-1" in result.out


def test_task_show_says_whether_a_subtask_is_finished_not_where_it_is(run: Runner) -> None:
    """A sub-task is in no column, so the row that would name one names this."""
    result = run("task", "show", "ATL-2-1")
    assert result.code == 0
    assert "Finished" in result.out
    assert "Column" not in result.out


def test_task_move_resolves_a_column_name(run: Runner, recorder: fake_api.Recorder) -> None:
    result = run("task", "move", "ATL-2", "--column", "In progress")
    assert result.code == 0
    assert recorder.body("POST", "/move") == {"column_id": fake_api.DOING_ID, "position": 0}


def test_task_move_is_case_insensitive_about_the_column(
    run: Runner, recorder: fake_api.Recorder
) -> None:
    assert run("task", "move", "ATL-2", "--column", "in PROGRESS").code == 0
    assert recorder.body("POST", "/move")["column_id"] == fake_api.DOING_ID


def test_task_move_refuses_an_unknown_column_and_says_what_exists(run: Runner) -> None:
    result = run("task", "move", "ATL-2", "--column", "Shipped")
    assert result.code == 1
    assert "No column called 'Shipped'" in result.err
    assert "Backlog, In progress, Done" in result.err


def test_task_status_requires_a_reason_for_blocked(
    run: Runner, recorder: fake_api.Recorder
) -> None:
    """Caught locally, so the round trip is not spent to learn it."""
    result = run("task", "status", "ATL-2", "blocked")
    assert result.code == 1
    assert "--reason is required" in result.err
    assert not any(path.endswith("/status") for path in recorder.paths())


def test_task_status_sends_the_reason_and_the_tagged_person(
    run: Runner, recorder: fake_api.Recorder
) -> None:
    result = run(
        "task",
        "status",
        "ATL-2",
        "blocked",
        "--reason",
        "waiting for the finance sign-off",
        "--waiting-on",
        "Lena W",
    )
    assert result.code == 0
    assert recorder.body("POST", "/status") == {
        "status": "blocked",
        "waiting_on": [fake_api.LENA_ID],
        "reason": "waiting for the finance sign-off",
    }
    assert "Waiting on Lena W." in result.out


def test_task_status_back_to_active_needs_no_reason(run: Runner) -> None:
    assert run("task", "status", "ATL-2", "active").code == 0


def test_task_comment_posts_the_body(run: Runner, recorder: fake_api.Recorder) -> None:
    result = run("task", "comment", "ATL-2", "Chased finance again.")
    assert result.code == 0
    assert recorder.body("POST", "/comments") == {"body": "Chased finance again."}


# --- Name resolution -------------------------------------------------------


def test_an_ambiguous_person_is_an_error_not_a_guess(run: Runner) -> None:
    """'Le' matches both Lena W and Leo Wren. Picking one would be worse."""
    result = run("task", "status", "ATL-2", "blocked", "--reason", "x", "--waiting-on", "Le")
    assert result.code == 1
    assert "matches more than one person" in result.err
    assert "Lena W" in result.err
    assert "Leo Wren" in result.err


def test_an_exact_match_wins_over_a_longer_one(run: Runner, recorder: fake_api.Recorder) -> None:
    """'Lena W' is a prefix of nothing else, but the exact pass must run first."""
    result = run("task", "status", "ATL-2", "blocked", "--reason", "x", "--waiting-on", "Lena W")
    assert result.code == 0
    assert recorder.body("POST", "/status")["waiting_on"] == [fake_api.LENA_ID]


def test_a_uuid_is_passed_through_without_a_lookup(
    run: Runner, recorder: fake_api.Recorder
) -> None:
    result = run(
        "task", "status", "ATL-2", "blocked", "--reason", "x", "--waiting-on", fake_api.LENA_ID
    )
    assert result.code == 0
    assert not any(path == "GET /api/v1/projects/ATL/members" for path in recorder.paths())


def test_an_unknown_person_lists_the_ones_that_exist(run: Runner) -> None:
    result = run("task", "status", "ATL-2", "blocked", "--reason", "x", "--waiting-on", "Nobody")
    assert result.code == 1
    assert "No person called 'Nobody'" in result.err
    assert "Aditi K" in result.err


# --- People ----------------------------------------------------------------


def test_people_ls_lists_the_directory(run: Runner) -> None:
    result = run("people", "ls")
    assert result.code == 0
    assert "Aditi K" in result.out
    assert "Leo Wren" in result.out


def test_people_ls_passes_the_kind_filter_to_the_api(
    run: Runner, recorder: fake_api.Recorder
) -> None:
    result = run("people", "ls", "--kind", "client")
    assert result.code == 0
    assert recorder.sent("GET", "/people").url.params["kind"] == "client"
    assert "Aditi K" not in result.out


def test_people_new_posts_the_required_fields(run: Runner, recorder: fake_api.Recorder) -> None:
    result = run(
        "people",
        "new",
        "--name",
        "Ravi S",
        "--kind",
        "team",
        "--role",
        "Data engineer",
        "--responsibilities",
        "Owns the export pipeline.",
    )
    assert result.code == 0
    assert recorder.body("POST", "/people")["name"] == "Ravi S"


# --- Files -----------------------------------------------------------------


def test_files_ls_with_no_path_shows_top_level_folders(run: Runner) -> None:
    result = run("files", "ls", "ATL")
    assert result.code == 0
    assert "Contracts" in result.out


def test_files_ls_walks_a_path(run: Runner) -> None:
    result = run("files", "ls", "ATL", "Contracts/2026")
    assert result.code == 0
    assert "msa.txt" in result.out
    assert "portal" in result.out


def test_files_get_writes_the_bytes(run: Runner, tmp_path: Path) -> None:
    target = tmp_path / "downloaded.txt"
    result = run("files", "get", "ATL", "Contracts/2026/msa.txt", "-o", str(target))
    assert result.code == 0
    assert target.read_bytes() == fake_api.MSA_BYTES


def test_files_get_refuses_a_link_and_gives_its_url(run: Runner) -> None:
    result = run("files", "get", "ATL", "Contracts/2026/portal")
    assert result.code == 1
    assert "is a link" in result.err
    assert "https://example.invalid/portal" in result.err


def test_files_get_explains_a_path_with_no_folder(run: Runner) -> None:
    result = run("files", "get", "ATL", "msa.txt")
    assert result.code == 1
    assert "Files live in folders" in result.err


# --- Vault -----------------------------------------------------------------


def test_vault_ls_lists_trees_with_counts(run: Runner) -> None:
    result = run("vault", "ls", "ATL")
    assert result.code == 0
    assert "Logins" in result.out


def test_vault_ls_renders_a_tree_without_any_value(run: Runner) -> None:
    result = run("vault", "ls", "ATL", "Logins")
    assert result.code == 0
    assert "Billing" in result.out
    assert "Stripe" in result.out
    assert "(secret, billing@example.com)" in result.out
    assert fake_api.STRIPE_SECRET not in result.out


def test_vault_reveal_without_a_flag_shows_metadata_and_fetches_nothing(
    run: Runner, recorder: fake_api.Recorder
) -> None:
    """The safety property this module exists for."""
    result = run("vault", "reveal", "ATL", "Logins/Billing/Stripe")
    assert result.code == 0
    assert fake_api.STRIPE_SECRET not in result.out
    assert fake_api.STRIPE_SECRET not in result.err
    assert "billing@example.com" in result.out
    assert "Add --show to print it" in result.out
    assert not any(path.endswith("/reveal") for path in recorder.paths())


def test_vault_reveal_with_show_prints_the_value_and_warns(run: Runner) -> None:
    result = run("vault", "reveal", "ATL", "Logins/Billing/Stripe", "--show")
    assert result.code == 0
    assert result.out.strip() == fake_api.STRIPE_SECRET
    assert "scrollback" in result.err


def test_vault_reveal_to_a_file_keeps_it_off_the_screen(run: Runner, tmp_path: Path) -> None:
    target = tmp_path / "stripe.txt"
    result = run("vault", "reveal", "ATL", "Logins/Billing/Stripe", "-o", str(target))
    assert result.code == 0
    assert target.read_text() == fake_api.STRIPE_SECRET
    assert oct(target.stat().st_mode)[-4:] == "0600"
    assert fake_api.STRIPE_SECRET not in result.out
    assert fake_api.STRIPE_SECRET not in result.err


def test_vault_reveal_json_omits_the_value_unless_asked(run: Runner, tmp_path: Path) -> None:
    target = tmp_path / "stripe.txt"
    result = run("--json", "vault", "reveal", "ATL", "Logins/Billing/Stripe", "-o", str(target))
    assert result.code == 0
    assert "value" not in json.loads(result.out)


def test_vault_reveal_refuses_a_branch(run: Runner) -> None:
    result = run("vault", "reveal", "ATL", "Logins/Billing", "--show")
    assert result.code == 1
    assert "is a branch" in result.err


def test_vault_add_reads_the_value_from_stdin_not_an_argument(
    run: Runner, recorder: fake_api.Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("sk_live_new_key\n"))
    result = run("vault", "add", "ATL", "Logins/Billing/Twilio", "--value-stdin")
    assert result.code == 0
    body = recorder.body("POST", "/vault/nodes")
    assert body["name"] == "Twilio"
    assert body["parent_id"] == fake_api.BILLING_ID
    assert body["secret"]["value"] == "sk_live_new_key"


# --- Activity --------------------------------------------------------------


def test_activity_passes_the_project_key_straight_through(
    run: Runner, recorder: fake_api.Recorder
) -> None:
    """The feed takes a key like every other project-scoped path.

    It used to accept only a UUID, so this command spent a request resolving
    the key first. Asserting the absence of that request keeps it gone.
    """
    result = run("activity", "--project", "ATL")
    assert result.code == 0
    assert recorder.sent("GET", "/activity").url.params["project"] == "ATL"
    assert recorder.count("GET", "/projects/ATL") == 0
    assert "board-tidy agent" in result.out
    assert "task.status_changed" in result.out


def test_vault_add_url_is_not_the_server_url(
    run: Runner, recorder: fake_api.Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`vault add --url` is the login page, not where Cylist lives.

    Regression: the global --url and this one shared argparse's single
    namespace, so naming a credential's URL silently repointed the CLI at it.
    """
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("sk_live_key\n"))
    result = run(
        "vault",
        "add",
        "ATL",
        "Logins/Billing/Twilio",
        "--url",
        "https://dashboard.stripe.com",
        "--value-stdin",
    )
    assert result.code == 0
    assert recorder.body("POST", "/vault/nodes")["secret"]["url"] == "https://dashboard.stripe.com"
    # Every request still went to the configured server, not to Stripe.
    for request in recorder.requests:
        assert request.url.host == "cylist.test"


def test_a_name_error_quotes_the_project_key_not_a_uuid(run: Runner) -> None:
    """The project is only known by id at that point; the key is what helps."""
    result = run("task", "move", "ATL-2", "--column", "Shipped")
    assert result.code == 1
    assert "ATL's board" in result.err
    assert fake_api.PROJECT_ID not in result.err


def test_goals_ls_reads_progress_as_done_over_total(run: Runner) -> None:
    """A goal is read at a glance as a fraction, not as six separate counts."""
    result = run("goals", "ls", "ATL")
    assert result.code == 0
    assert "ATL-G1" in result.out
    assert "Ledger cutover" in result.out
    assert "1/2" in result.out


def test_goals_ls_can_leave_out_settled_goals(run: Runner, recorder: fake_api.Recorder) -> None:
    result = run("goals", "ls", "ATL", "--open")
    assert result.code == 0
    assert recorder.sent("GET", "/goals").url.params["open_only"] == "true"


def test_goals_show_lists_the_cards_on_the_goal(run: Runner) -> None:
    result = run("goals", "show", "ATL-G1")
    assert result.code == 0
    assert "Ledger cutover" in result.out
    assert "ATL-1" in result.out


def test_goals_new_posts_the_owner_and_target(run: Runner, recorder: fake_api.Recorder) -> None:
    result = run(
        "goals",
        "new",
        "ATL",
        "--name",
        "Ledger cutover",
        "--owner",
        fake_api.ADITI_ID,
        "--target",
        "2026-06-30",
    )
    assert result.code == 0
    body = recorder.body("POST", "/goals")
    assert body["owner_id"] == fake_api.ADITI_ID
    assert body["target_date"] == "2026-06-30"
    assert "colour" not in body  # left to the palette rather than guessed at


def test_goals_link_sends_the_goals_id_not_its_reference(
    run: Runner, recorder: fake_api.Recorder
) -> None:
    """The reference is what a person types; the id is what the API stores."""
    result = run("goals", "link", "ATL-1", "ATL-G1")
    assert result.code == 0
    assert recorder.body("PATCH", "/tasks/ATL-1") == {"goal_id": fake_api.GOAL_ID}


def test_goals_unlink_clears_it_with_a_null(run: Runner, recorder: fake_api.Recorder) -> None:
    """Null rather than an omitted field: on this one the API reads null as
    "take it off" rather than as "leave it alone"."""
    result = run("goals", "unlink", "ATL-1")
    assert result.code == 0
    assert recorder.body("PATCH", "/tasks/ATL-1") == {"goal_id": None}
    assert "still on the board" in result.out


def test_goals_status_sends_the_word_it_was_given(run: Runner, recorder: fake_api.Recorder) -> None:
    result = run("goals", "status", "ATL-G1", "achieved")
    assert result.code == 0
    assert recorder.body("PATCH", "/goals/ATL-G1") == {"status": "achieved"}
