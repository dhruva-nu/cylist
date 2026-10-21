"""Two rules about something settled, asked from both ends at once (CYLIST-128).

A card in the board's last column has nothing open under it, and an achieved
goal has no open card on it. Each rule is asked by two kinds of request — the
one settling the parent and the one opening something beneath it — and each
used to read the other's state and then write its own. Sent at the same moment,
both read "fine", and the board committed an impossible state.

Both ends now lock the parent's row before they read. The races here are two
real requests in flight together on separate database sessions; a rendezvous
right after each lock holds the first arrival until the second has caught up
or plainly cannot. Without the lock both get there and both pass their checks.
With it the second is still waiting on the database, so the first goes on
alone, commits, and the second then reads what it wrote and is refused.

The late write is refused, never taken as reopening what was settled — those
cases are pinned down one request at a time first.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from httpx import AsyncClient, Response

from app.services import goals
from app.services.tasks import moves, parentage
from tests.conftest import put_members

ATLAS = {"key": "ATL", "name": "Atlas Billing Migration"}
ADITI = {
    "name": "Aditi K",
    "kind": "team",
    "title": "Backend engineer",
    "responsibilities": "Payments and webhooks.",
}
CATCH_UP = 1.0
"""How long the first request at the rendezvous waits for the second: long
enough that a second request which is not blocked arrives, short enough that
a blocked one costs the suite a second."""


class Rendezvous:
    """Hold the first of two requests until the second arrives, or cannot."""

    def __init__(self) -> None:
        self._arrived = 0
        self._both = asyncio.Event()

    async def meet(self) -> None:
        self._arrived += 1
        if self._arrived >= 2:
            self._both.set()
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._both.wait(), CATCH_UP)


def _after[**P, R](
    original: Callable[P, Awaitable[R]], rendezvous: Rendezvous
) -> Callable[P, Awaitable[R]]:
    """``original``, then the rendezvous: the lock is held by the time it waits."""

    async def gated(*args: P.args, **kwargs: P.kwargs) -> R:
        result = await original(*args, **kwargs)
        await rendezvous.meet()
        return result

    return gated


@pytest.fixture
def parent_rendezvous(monkeypatch: pytest.MonkeyPatch) -> None:
    """Meet just after a card's row is locked, from either end of the rule."""
    gated = _after(parentage.lock_parent, Rendezvous())
    monkeypatch.setattr(parentage, "lock_parent", gated)
    monkeypatch.setattr(moves, "lock_parent", gated)


@pytest.fixture
def goal_rendezvous(monkeypatch: pytest.MonkeyPatch) -> None:
    """Meet just after a goal's row is locked, from either end of the rule."""
    monkeypatch.setattr(goals, "lock", _after(goals.lock, Rendezvous()))


async def _setup(client: AsyncClient) -> str:
    await client.post("/projects", json=ATLAS)
    person = (await client.post("/people", json=ADITI)).json()["id"]
    await put_members(client, "ATL", [person])
    return str(person)


def _body(assignee: str, **overrides: Any) -> dict[str, Any]:
    return {
        "title": "Stripe webhook idempotency",
        "description": "Duplicate deliveries create double payments. Dedupe on event id.",
        "type": "bug",
        "assignee_id": assignee,
        **overrides,
    }


async def _card(client: AsyncClient, assignee: str, **overrides: Any) -> dict[str, Any]:
    response = await client.post("/projects/ATL/tasks", json=_body(assignee, **overrides))
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _subtask(client: AsyncClient, parent: str, assignee: str) -> Response:
    return await client.post(f"/tasks/{parent}/subtasks", json=_body(assignee))


async def _goal(client: AsyncClient, owner: str) -> dict[str, Any]:
    response = await client.post(
        "/projects/ATL/goals", json={"name": "Search revamp", "owner_id": owner}
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _columns(client: AsyncClient) -> list[str]:
    return [
        column["id"] for column in (await client.get("/projects/ATL/columns")).json()["columns"]
    ]


async def _move(client: AsyncClient, task: str, column: str) -> Response:
    return await client.post(f"/tasks/{task}/move", json={"column_id": column, "position": 0})


async def _tick(client: AsyncClient, task: str, *, finished: bool) -> Response:
    return await client.post(f"/tasks/{task}/finish", json={"finished": finished})


async def _status(client: AsyncClient, task: str, status: str) -> Response:
    body = {"status": status, "reason": "The vendor withdrew the endpoint."}
    return await client.post(f"/tasks/{task}/status", json=body)


async def _achieve(client: AsyncClient, goal: str) -> Response:
    return await client.patch(f"/goals/{goal}", json={"status": "achieved"})


def _refused_by(response: Response, field: str, reference: str) -> None:
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["details"][field] == reference
    assert reference in error["message"]


def _one_of_each(*responses: Response) -> Response:
    """The two outcomes of a race fought fairly: one through, one refused.

    Returns the refusal, whichever request it was.
    """
    codes = sorted(response.status_code for response in responses)
    assert codes[0] in (200, 201) and codes[1] == 422, [r.text for r in responses]
    return next(response for response in responses if response.status_code == 422)


async def _parent_holds(client: AsyncClient, parent: str) -> None:
    """A finished card has nothing open under it."""
    body = (await client.get(f"/tasks/{parent}")).json()
    if body["finished_at"] is None:
        return
    open_under = [
        sub["reference"]
        for sub in body["subtasks"]
        if sub["finished_at"] is None and sub["status"] != "cancelled"
    ]
    assert open_under == [], f"{parent} is finished over {open_under}"


async def _goal_holds(client: AsyncClient, goal: str) -> None:
    """An achieved goal has no open card on it."""
    body = (await client.get(f"/goals/{goal}")).json()
    if body["status"] != "achieved":
        return
    assert body["progress"]["open"] == 0, f"{goal} is achieved over open cards"


class TestUnderAFinishedCard:
    """One request at a time: what a finished card refuses under it."""

    async def test_a_subtask_cannot_be_added(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        parent = await _card(signed_in, person)
        assert (
            await _move(signed_in, parent["reference"], (await _columns(signed_in))[-1])
        ).is_success

        _refused_by(await _subtask(signed_in, parent["reference"], person), "parent", "ATL-1")

    async def test_a_subtask_cannot_be_reopened(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        parent = await _card(signed_in, person)
        sub = (await _subtask(signed_in, parent["reference"], person)).json()
        await _tick(signed_in, sub["reference"], finished=True)
        assert (
            await _move(signed_in, parent["reference"], (await _columns(signed_in))[-1])
        ).is_success

        response = await _tick(signed_in, sub["reference"], finished=False)

        _refused_by(response, "parent", "ATL-1")
        assert (await signed_in.get(f"/tasks/{sub['reference']}")).json()["finished_at"]

    async def test_a_subtask_cannot_be_uncancelled(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        parent = await _card(signed_in, person)
        sub = (await _subtask(signed_in, parent["reference"], person)).json()
        assert (await _status(signed_in, sub["reference"], "cancelled")).is_success
        assert (
            await _move(signed_in, parent["reference"], (await _columns(signed_in))[-1])
        ).is_success

        _refused_by(await _status(signed_in, sub["reference"], "active"), "parent", "ATL-1")

    async def test_the_parent_is_not_reopened_behind_anyones_back(
        self, signed_in: AsyncClient
    ) -> None:
        """Refused, not taken as reopening it: the card stays where it was put."""
        person = await _setup(signed_in)
        parent = await _card(signed_in, person)
        done = (await _columns(signed_in))[-1]
        await _move(signed_in, parent["reference"], done)

        await _subtask(signed_in, parent["reference"], person)

        body = (await signed_in.get(f"/tasks/{parent['reference']}")).json()
        assert body["column_id"] == done
        assert body["finished_at"] is not None
        assert body["subtasks"] == []

    async def test_moving_the_parent_out_lets_it_through(self, signed_in: AsyncClient) -> None:
        """The way through the refusal is the one it names."""
        person = await _setup(signed_in)
        parent = await _card(signed_in, person)
        first, *_, done = await _columns(signed_in)
        await _move(signed_in, parent["reference"], done)
        await _move(signed_in, parent["reference"], first)

        assert (await _subtask(signed_in, parent["reference"], person)).status_code == 201

    async def test_a_subtask_can_still_be_finished_or_cancelled(
        self, signed_in: AsyncClient
    ) -> None:
        """Only opening is refused; settling more under a done card is no harm."""
        person = await _setup(signed_in)
        parent = await _card(signed_in, person)
        sub = (await _subtask(signed_in, parent["reference"], person)).json()
        await _tick(signed_in, sub["reference"], finished=True)
        await _move(signed_in, parent["reference"], (await _columns(signed_in))[-1])

        assert (await _status(signed_in, sub["reference"], "cancelled")).is_success


class TestOnAnAchievedGoal:
    """One request at a time: what an achieved goal refuses on it."""

    async def _achieved(self, client: AsyncClient, person: str) -> dict[str, Any]:
        goal = await _goal(client, person)
        assert (await _achieve(client, goal["reference"])).is_success
        return goal

    async def test_a_new_card_cannot_be_written_on_it(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        goal = await self._achieved(signed_in, person)

        response = await signed_in.post(
            "/projects/ATL/tasks", json=_body(person, goal_id=goal["id"])
        )

        _refused_by(response, "goal", "ATL-G1")
        assert (await signed_in.get("/projects/ATL/tasks")).json() == []

    async def test_an_open_card_cannot_be_put_on_it(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        goal = await self._achieved(signed_in, person)
        card = await _card(signed_in, person)

        response = await signed_in.patch(
            f"/tasks/{card['reference']}", json={"goal_id": goal["id"]}
        )

        _refused_by(response, "goal", "ATL-G1")

    async def test_a_finished_card_can_be_put_on_it(self, signed_in: AsyncClient) -> None:
        """Filing done work under a goal already reached breaks nothing."""
        person = await _setup(signed_in)
        goal = await self._achieved(signed_in, person)
        card = await _card(signed_in, person)
        await _move(signed_in, card["reference"], (await _columns(signed_in))[-1])

        response = await signed_in.patch(
            f"/tasks/{card['reference']}", json={"goal_id": goal["id"]}
        )

        assert response.status_code == 200, response.text

    async def test_a_card_on_it_cannot_leave_the_last_column(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        goal = await _goal(signed_in, person)
        card = await _card(signed_in, person, goal_id=goal["id"])
        first, *_, done = await _columns(signed_in)
        await _move(signed_in, card["reference"], done)
        await _achieve(signed_in, goal["reference"])

        _refused_by(await _move(signed_in, card["reference"], first), "goal", "ATL-G1")
        assert (await signed_in.get(f"/goals/{goal['reference']}")).json()["status"] == "achieved"

    async def test_a_card_on_it_cannot_be_uncancelled(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        goal = await _goal(signed_in, person)
        card = await _card(signed_in, person, goal_id=goal["id"])
        await _status(signed_in, card["reference"], "cancelled")
        await _achieve(signed_in, goal["reference"])

        _refused_by(await _status(signed_in, card["reference"], "active"), "goal", "ATL-G1")

    async def test_a_card_in_cold_storage_does_not_hold_it_open(
        self, signed_in: AsyncClient
    ) -> None:
        """Stored work left the board finished; progress counts it done, and so
        does the rule."""
        person = await _setup(signed_in)
        goal = await _goal(signed_in, person)
        card = await _card(signed_in, person, goal_id=goal["id"])
        await _move(signed_in, card["reference"], (await _columns(signed_in))[-1])
        stored = await signed_in.post("/projects/ATL/cold-storage")
        assert stored.is_success, stored.text

        assert (await _achieve(signed_in, goal["reference"])).status_code == 200


@pytest.mark.usefixtures("parent_rendezvous")
class TestFinishingACardWhileSomethingOpensUnderIt:
    """Two requests in flight at once: the parent's move against the sub-task."""

    async def test_against_a_new_subtask(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        parent = await _card(signed_in, person)
        done = (await _columns(signed_in))[-1]

        moved, split = await asyncio.gather(
            _move(signed_in, parent["reference"], done),
            _subtask(signed_in, parent["reference"], person),
        )

        refused = _one_of_each(moved, split)
        if refused is split:
            _refused_by(split, "parent", "ATL-1")
        else:
            assert moved.json()["error"]["details"]["open_subtasks"] == ["ATL-1-1"]
        await _parent_holds(signed_in, parent["reference"])

    async def test_against_a_subtask_reopened(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        parent = await _card(signed_in, person)
        sub = (await _subtask(signed_in, parent["reference"], person)).json()
        await _tick(signed_in, sub["reference"], finished=True)
        done = (await _columns(signed_in))[-1]

        moved, reopened = await asyncio.gather(
            _move(signed_in, parent["reference"], done),
            _tick(signed_in, sub["reference"], finished=False),
        )

        _one_of_each(moved, reopened)
        await _parent_holds(signed_in, parent["reference"])

    async def test_against_a_subtask_uncancelled(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        parent = await _card(signed_in, person)
        sub = (await _subtask(signed_in, parent["reference"], person)).json()
        await _status(signed_in, sub["reference"], "cancelled")
        done = (await _columns(signed_in))[-1]

        moved, revived = await asyncio.gather(
            _move(signed_in, parent["reference"], done),
            _status(signed_in, sub["reference"], "active"),
        )

        _one_of_each(moved, revived)
        await _parent_holds(signed_in, parent["reference"])


@pytest.mark.usefixtures("goal_rendezvous")
class TestAchievingAGoalWhileACardOpensOnIt:
    """Two requests in flight at once: the goal's status against a card."""

    async def test_against_a_new_card(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        goal = await _goal(signed_in, person)

        achieved, written = await asyncio.gather(
            _achieve(signed_in, goal["reference"]),
            signed_in.post("/projects/ATL/tasks", json=_body(person, goal_id=goal["id"])),
        )

        refused = _one_of_each(achieved, written)
        if refused is written:
            _refused_by(written, "goal", "ATL-G1")
        else:
            assert refused.json()["error"]["details"]["open_tasks"] == ["ATL-1"]
        await _goal_holds(signed_in, goal["reference"])

    async def test_against_a_card_linked(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        goal = await _goal(signed_in, person)
        card = await _card(signed_in, person)

        achieved, linked = await asyncio.gather(
            _achieve(signed_in, goal["reference"]),
            signed_in.patch(f"/tasks/{card['reference']}", json={"goal_id": goal["id"]}),
        )

        _one_of_each(achieved, linked)
        await _goal_holds(signed_in, goal["reference"])

    async def test_against_a_card_reopened(self, signed_in: AsyncClient) -> None:
        person = await _setup(signed_in)
        goal = await _goal(signed_in, person)
        card = await _card(signed_in, person, goal_id=goal["id"])
        first, *_, done = await _columns(signed_in)
        await _move(signed_in, card["reference"], done)

        achieved, reopened = await asyncio.gather(
            _achieve(signed_in, goal["reference"]),
            _move(signed_in, card["reference"], first),
        )

        _one_of_each(achieved, reopened)
        await _goal_holds(signed_in, goal["reference"])
