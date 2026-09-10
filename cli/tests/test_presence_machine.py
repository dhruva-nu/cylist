"""Every rule about when the daemon does what, without a daemon.

The rules here are about durations — five minutes idle, a minute between
keepalives, half an hour of backoff — and a suite that tested them by
waiting would take three quarters of an hour and still be flaky. `step`
takes the elapsed time as an argument, so all of it runs in microseconds and
the answers are exact.
"""

from __future__ import annotations

import json
from typing import Any

from cylist_cli.presence import machine as m

START = m.Machine(session="s-1", task="ATL-1", client_name="ATL-1")


def frames(actions: list[m.Action]) -> list[dict[str, Any]]:
    return [json.loads(a.frame) for a in actions if isinstance(a, m.Send)]


def finishes(actions: list[m.Action]) -> list[str]:
    return [a.reason for a in actions if isinstance(a, m.Finish)]


def live(machine: m.Machine = START) -> m.Machine:
    return m.step(machine, m.LinkUp())[0]


class TestSaying:
    def test_connecting_sends_where_the_session_is(self) -> None:
        _, actions = m.step(START, m.LinkUp())

        assert frames(actions) == [
            {
                "type": "state",
                "task": "ATL-1",
                "report": {"state": "working", "reason": None, "client_name": "ATL-1"},
            }
        ]

    def test_a_change_of_state_is_sent(self) -> None:
        _, actions = m.step(live(), m.HookState("waiting", "turn_ended", "ATL-1"))

        assert frames(actions)[0]["report"]["state"] == "waiting"

    def test_the_same_state_twice_is_sent_once(self) -> None:
        """Two `Stop` events in a row are one fact, and the server has it."""
        machine, _ = m.step(live(), m.HookState("waiting", "turn_ended", "ATL-1"))

        _, actions = m.step(machine, m.HookState("waiting", "turn_ended", "ATL-1"))

        assert frames(actions) == []

    def test_the_same_state_for_a_different_reason_is_sent(self) -> None:
        """A turn ending and a permission prompt both read as waiting, and a
        card that could not tell them apart would be the poorer for it."""
        machine, _ = m.step(live(), m.HookState("waiting", "turn_ended", "ATL-1"))

        _, actions = m.step(machine, m.HookState("waiting", "permission", "ATL-1"))

        assert frames(actions)[0]["report"]["reason"] == "permission"

    def test_walking_to_another_card_names_it(self) -> None:
        _, actions = m.step(live(), m.HookBind("ATL-2", "ATL-2"))

        assert frames(actions)[0]["task"] == "ATL-2"


class TestWhileTheLinkIsDown:
    def test_nothing_is_sent(self) -> None:
        down, _ = m.step(live(), m.LinkDown())

        _, actions = m.step(down, m.HookState("waiting", "turn_ended", "ATL-1"))

        assert frames(actions) == []

    def test_reconnecting_sends_where_it_is_now_not_how_it_got_there(self) -> None:
        """A resync, never a replay. Three states passed while the socket was
        down; the server is told the third and nothing about the first two."""
        machine, _ = m.step(live(), m.LinkDown())
        machine, _ = m.step(machine, m.HookState("waiting", "permission", "ATL-1"))
        machine, _ = m.step(machine, m.HookState("working", None, "ATL-1"))
        machine, _ = m.step(machine, m.HookBind("ATL-9", "ATL-9"))

        _, actions = m.step(machine, m.LinkUp())

        sent = frames(actions)
        assert len(sent) == 1
        assert sent[0]["task"] == "ATL-9"
        assert sent[0]["report"]["state"] == "working"

    def test_it_backs_off_further_each_time(self) -> None:
        machine = live()
        delays = []
        for _ in range(5):
            machine, actions = m.step(machine, m.LinkDown())
            delays += [a.after for a in actions if isinstance(a, m.Reconnect)]

        assert delays == [0.5, 1.0, 2.0, 4.0, 8.0]

    def test_the_backoff_flattens_rather_than_growing_forever(self) -> None:
        assert m.backoff(6) == m.backoff(60) == 30.0

    def test_a_successful_connection_forgives_the_backoff(self) -> None:
        machine, _ = m.step(live(), m.LinkDown())
        machine, _ = m.step(machine, m.LinkDown())
        machine, _ = m.step(machine, m.LinkUp())

        _, actions = m.step(machine, m.LinkDown())

        assert [a.after for a in actions if isinstance(a, m.Reconnect)] == [0.5]

    def test_it_gives_up_eventually(self) -> None:
        """Half an hour of a backend that is not there means it is not coming
        back inside this session. The next hook event starts a fresh daemon."""
        machine, _ = m.step(live(), m.LinkDown())

        _, actions = m.step(machine, m.Tick(), elapsed=m.BACKOFF_GIVE_UP)

        assert finishes(actions) == ["backend_gone"]


class TestTheKeepalive:
    def test_it_runs_while_working(self) -> None:
        _, actions = m.step(live(), m.Tick(), elapsed=m.KEEPALIVE_EVERY)

        assert frames(actions) == [{"type": "heartbeat"}]

    def test_it_stops_while_waiting(self) -> None:
        """The client's half of the idle contract. Silence while waiting is
        the signal — it is how the server learns the human has gone."""
        machine, _ = m.step(live(), m.HookState("waiting", "turn_ended", "ATL-1"))

        _, actions = m.step(machine, m.Tick(), elapsed=m.KEEPALIVE_EVERY)

        assert frames(actions) == []

    def test_a_long_tool_call_keeps_the_session_alive(self) -> None:
        """Twenty minutes inside one Bash call, and not one hook event. The
        socket must not be read as absence — which is exactly what a
        wall-clock idle rule would have done."""
        machine = live()
        beats = 0
        for _ in range(20):
            machine, actions = m.step(machine, m.Tick(), elapsed=60.0)
            beats += len(frames(actions))
            assert finishes(actions) == []

        assert beats == 20


class TestEnding:
    def test_a_goodbye_is_said_before_finishing(self) -> None:
        _, actions = m.step(live(), m.HookEnd("session_ended"))

        assert frames(actions) == [{"type": "bye", "reason": "session_ended"}]
        assert finishes(actions) == ["session_ended"]

    def test_it_finishes_even_with_no_link_to_say_so_on(self) -> None:
        down, _ = m.step(live(), m.LinkDown())

        _, actions = m.step(down, m.HookEnd("session_ended"))

        assert frames(actions) == []
        assert finishes(actions) == ["session_ended"]

    def test_waiting_five_minutes_ends_the_session(self) -> None:
        machine, _ = m.step(live(), m.HookState("waiting", "turn_ended", "ATL-1"))

        _, actions = m.step(machine, m.Tick(), elapsed=m.IDLE_AFTER)

        assert frames(actions) == [{"type": "bye", "reason": "session_ended"}]
        assert finishes(actions) == ["idle"]

    def test_a_prompt_just_before_the_window_resets_it(self) -> None:
        machine, _ = m.step(live(), m.HookState("waiting", "turn_ended", "ATL-1"))
        machine, _ = m.step(machine, m.Tick(), elapsed=m.IDLE_AFTER - 1)
        machine, _ = m.step(machine, m.HookState("working", None, "ATL-1"))

        _, actions = m.step(machine, m.Tick(), elapsed=m.IDLE_AFTER - 1)

        assert finishes(actions) == []

    def test_working_is_never_idle_however_quiet(self) -> None:
        """The rule the ticket's literal wording would have got wrong."""
        _, actions = m.step(live(), m.Tick(), elapsed=m.IDLE_AFTER * 10)

        assert finishes(actions) == []
