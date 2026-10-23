"""The glue both of an agent's lines share: finding its card, and the long poll."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.core.clock import now
from app.models.agent_session import AgentPromptStatus
from app.services import agent_gateway

PROMPT_ID = uuid4()


class FakeWaiters:
    """Stands in for the hub: hands out an event the test can set."""

    def __init__(self) -> None:
        self.event = asyncio.Event()
        self.waited_on: list[Any] = []

    @contextmanager
    def prompt_waiter(self, prompt_id: Any) -> Iterator[asyncio.Event]:
        self.waited_on.append(prompt_id)
        yield self.event


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


def prompt(status: AgentPromptStatus, expires_in: float = 60.0) -> Any:
    return SimpleNamespace(status=status, expires_at=now() + timedelta(seconds=expires_in))


class Reads:
    """The states the prompt is read as, in turn, and who was polling each time."""

    def __init__(self) -> None:
        self.states: list[Any] = []
        self.asked: list[Any] = []

    def extend(self, states: list[Any]) -> None:
        self.states.extend(states)

    def append(self, state: Any) -> None:
        self.states.append(state)


@pytest.fixture
def reads(monkeypatch: pytest.MonkeyPatch) -> Reads:
    read = Reads()

    async def wait_state(_session: Any, _task: Any, _prompt_id: Any, *, poller: Any) -> Any:
        read.asked.append(poller)
        return read.states.pop(0)

    monkeypatch.setattr(agent_gateway.agent_prompts, "wait_state", wait_state)
    return read


async def poll(session: FakeSession, waiters: FakeWaiters, *, wait: int, agent: bool = True) -> Any:
    return await agent_gateway.long_poll_prompt(
        session,  # type: ignore[arg-type]
        waiters,
        SimpleNamespace(),  # type: ignore[arg-type]
        PROMPT_ID,
        wait=wait,
        principal=SimpleNamespace(),  # type: ignore[arg-type]
        is_agent=agent,
    )


class TestLongPollingAPrompt:
    async def test_without_a_wait_it_answers_at_once_and_waits_for_nothing(
        self, reads: Reads
    ) -> None:
        current = prompt(AgentPromptStatus.OPEN)
        reads.append(current)
        session, waiters = FakeSession(), FakeWaiters()

        assert await poll(session, waiters, wait=0) is current
        assert session.commits == 0

    async def test_a_prompt_already_closed_is_answered_at_once(self, reads: Reads) -> None:
        closed = prompt(AgentPromptStatus.ANSWERED)
        reads.append(closed)
        session = FakeSession()

        assert await poll(session, FakeWaiters(), wait=20) is closed
        assert session.commits == 0

    async def test_an_open_prompt_lets_go_of_the_connection_before_it_waits(
        self, reads: Reads
    ) -> None:
        reads.extend([prompt(AgentPromptStatus.OPEN), prompt(AgentPromptStatus.ANSWERED)])
        session, waiters = FakeSession(), FakeWaiters()
        waiters.event.set()  # a change is announced the moment it waits

        result = await poll(session, waiters, wait=20)

        assert session.commits == 1
        assert result.status is AgentPromptStatus.ANSWERED

    async def test_it_gives_up_after_the_wait_and_reads_it_once_more(self, reads: Reads) -> None:
        reads.extend(
            [prompt(AgentPromptStatus.OPEN, expires_in=0.0), prompt(AgentPromptStatus.OPEN)]
        )

        result = await poll(FakeSession(), FakeWaiters(), wait=20)

        assert result.status is AgentPromptStatus.OPEN
        assert reads.states == []

    async def test_only_an_agents_poll_counts_as_the_asking_tool_looking(
        self, reads: Reads
    ) -> None:
        reads.extend([prompt(AgentPromptStatus.ANSWERED)] * 2)

        await poll(FakeSession(), FakeWaiters(), wait=20, agent=False)
        await poll(FakeSession(), FakeWaiters(), wait=20, agent=True)

        first, second = reads.asked
        assert first is None
        assert second is not None

    async def test_it_waits_on_the_prompt_it_was_asked_about(self, reads: Reads) -> None:
        reads.append(prompt(AgentPromptStatus.ANSWERED))
        waiters = FakeWaiters()

        await poll(FakeSession(), waiters, wait=20)

        assert waiters.waited_on == [PROMPT_ID]
