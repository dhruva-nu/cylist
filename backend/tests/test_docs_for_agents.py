"""The docs an agent is handed for a card, and the docs it writes back.

jev is replaced by :class:`FakeJudge`, which answers from a table and records
what it was asked. What is checked is Cylist's side of the conversation: what
the card and the docs look like to jev, what is done with its numbers, and
what an agent is told when there are no numbers to go on.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from jev import ChoiceAnswer, NoulAnswer, Result, Usage

from app.services.doc_judge import (
    Card,
    DocOnFile,
    Filing,
    JevJudge,
    JudgeUnavailableError,
    TopicOnFile,
    summary_of,
)
from app.services.docs import appended

ATLAS = {"key": "ATL", "name": "Atlas Billing Migration"}


@dataclass
class FakeJudge:
    """jev, as a table: a probability per doc title and a topic per doc title."""

    relevance_by_title: dict[str, float] = field(default_factory=dict)
    topic_by_title: dict[str, tuple[str, float]] = field(default_factory=dict)
    down: bool = False
    cards: list[Card] = field(default_factory=list)
    asked_about: list[list[DocOnFile]] = field(default_factory=list)
    offered: list[list[TopicOnFile]] = field(default_factory=list)

    async def relevance(self, card: Card, docs: Sequence[DocOnFile]) -> dict[UUID, float]:
        if self.down:
            raise JudgeUnavailableError("503 overloaded")
        self.cards.append(card)
        self.asked_about.append(list(docs))
        return {doc.id: self.relevance_by_title.get(doc.title, 0.0) for doc in docs}

    async def filing(self, title: str, body: str, topics: Sequence[TopicOnFile]) -> Filing:
        if self.down:
            raise JudgeUnavailableError("timed out")
        self.offered.append(list(topics))
        name, confidence = self.topic_by_title[title]
        chosen = next(topic for topic in topics if topic.name == name)
        rest = [topic for topic in topics if topic is not chosen]
        spread = (1 - confidence) / max(len(rest), 1)
        return Filing(
            topic_id=chosen.id,
            confidence=confidence,
            ranking=[(chosen.id, confidence), *((topic.id, spread) for topic in rest)],
        )


@pytest.fixture
def judge(signed_in: AsyncClient) -> FakeJudge:
    fake = FakeJudge()
    signed_in.app.state.doc_judge = fake  # type: ignore[attr-defined]
    return fake


async def _topic(client: AsyncClient, section: str, name: str) -> dict[str, Any]:
    response = await client.post(
        "/projects/ATL/doc-topics", json={"section": section, "name": name}
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _doc(client: AsyncClient, topic_id: str, title: str, body: str = "") -> dict[str, Any]:
    response = await client.post(
        f"/doc-topics/{topic_id}/docs", json={"title": title, "body": body}
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _card(client: AsyncClient, *checklist: str) -> str:
    response = await client.post(
        "/projects/ATL/tasks",
        json={
            "title": "Dedupe Stripe webhooks",
            "description": "Duplicate deliveries create double payments.",
            "type": "bug",
        },
    )
    assert response.status_code == 201, response.text
    reference = str(response.json()["reference"])
    for title in checklist:
        await client.post(f"/tasks/{reference}/checklist", json={"title": title})
    return reference


async def _a_filed_project(client: AsyncClient) -> dict[str, dict[str, Any]]:
    await client.post("/projects", json=ATLAS)
    apis = await _topic(client, "engineering", "APIs")
    schema = await _topic(client, "engineering", "DB schema")
    goals = await _topic(client, "product", "Goals")
    await _doc(client, apis["id"], "Webhooks", "# Webhooks\n\nHow Stripe events reach us.")
    await _doc(client, schema["id"], "Payments table", "One row per charge.")
    await _doc(client, goals["id"], "Q4 goals", "Ship search.")
    return {"apis": apis, "schema": schema, "goals": goals}


class TestTheDocsACardNeeds:
    async def test_jev_reads_the_card_and_is_asked_about_every_doc(
        self, signed_in: AsyncClient, judge: FakeJudge
    ) -> None:
        await _a_filed_project(signed_in)
        reference = await _card(signed_in, "Add a unique index on event id")

        await signed_in.get(f"/tasks/{reference}/docs")

        (card,) = judge.cards
        assert card.reference == reference
        assert card.title == "Dedupe Stripe webhooks"
        assert card.description == "Duplicate deliveries create double payments."
        assert card.checklist == ["Add a unique index on event id"]
        (asked,) = judge.asked_about
        assert [(doc.title, doc.section_label, doc.topic_name) for doc in asked] == [
            ("Q4 goals", "Product", "Goals"),
            ("Webhooks", "Engineering", "APIs"),
            ("Payments table", "Engineering", "DB schema"),
        ]
        assert asked[1].summary == "How Stripe events reach us.", "the heading is skipped"

    async def test_lists_only_what_passes_the_threshold_most_likely_first(
        self, signed_in: AsyncClient, judge: FakeJudge
    ) -> None:
        await _a_filed_project(signed_in)
        reference = await _card(signed_in)
        judge.relevance_by_title = {"Webhooks": 0.71, "Payments table": 0.93, "Q4 goals": 0.1}

        found = (await signed_in.get(f"/tasks/{reference}/docs")).json()

        assert found["ranked_by"] == "jev"
        assert found["threshold"] == 0.5
        assert found["reason"] is None
        assert [(doc["title"], doc["probability"]) for doc in found["docs"]] == [
            ("Payments table", 0.93),
            ("Webhooks", 0.71),
        ]
        assert found["docs"][0]["topic_name"] == "DB schema"
        assert found["docs"][0]["section"] == "engineering"
        assert found["doc_count"] == 3

    async def test_hands_over_the_whole_tree_when_jev_is_down(
        self, signed_in: AsyncClient, judge: FakeJudge
    ) -> None:
        await _a_filed_project(signed_in)
        reference = await _card(signed_in)
        judge.down = True

        found = (await signed_in.get(f"/tasks/{reference}/docs")).json()

        assert found["ranked_by"] == "none"
        assert found["threshold"] is None
        assert "503 overloaded" in found["reason"]
        assert [doc["title"] for doc in found["docs"]] == [
            "Q4 goals",
            "Webhooks",
            "Payments table",
        ]
        assert all(doc["probability"] is None for doc in found["docs"])

    async def test_and_when_no_key_is_configured(self, signed_in: AsyncClient) -> None:
        signed_in.app.state.doc_judge = None  # type: ignore[attr-defined]
        await _a_filed_project(signed_in)
        reference = await _card(signed_in)

        found = (await signed_in.get(f"/tasks/{reference}/docs")).json()

        assert found["ranked_by"] == "none"
        assert found["reason"] == "jev is not configured on this server."
        assert len(found["docs"]) == 3

    async def test_a_project_with_no_docs_asks_jev_nothing(
        self, signed_in: AsyncClient, judge: FakeJudge
    ) -> None:
        await signed_in.post("/projects", json=ATLAS)
        reference = await _card(signed_in)

        found = (await signed_in.get(f"/tasks/{reference}/docs")).json()

        assert found["docs"] == []
        assert found["reason"] is None
        assert judge.cards == []


class TestWritingADoc:
    async def test_a_named_topic_is_where_it_goes(
        self, signed_in: AsyncClient, judge: FakeJudge
    ) -> None:
        topics = await _a_filed_project(signed_in)

        response = await signed_in.post(
            "/projects/ATL/docs",
            json={
                "title": "Retries",
                "body": "Stripe retries for 3 days.",
                "topic_id": topics["apis"]["id"],
            },
        )

        assert response.status_code == 201, response.text
        written = response.json()
        assert written["filed_by"] == "caller"
        assert written["created"] is True
        assert written["doc"]["topic_name"] == "APIs"
        assert judge.offered == [], "jev is not asked where the writer already said"

    async def test_jev_files_it_when_it_is_confident(
        self, signed_in: AsyncClient, judge: FakeJudge
    ) -> None:
        await _a_filed_project(signed_in)
        judge.topic_by_title = {"Retries": ("APIs", 0.9)}

        response = await signed_in.post(
            "/projects/ATL/docs", json={"title": "Retries", "body": "Stripe retries for 3 days."}
        )

        assert response.status_code == 201, response.text
        written = response.json()
        assert written["filed_by"] == "jev"
        assert written["confidence"] == 0.9
        assert written["doc"]["topic_name"] == "APIs"
        (offered,) = judge.offered
        assert [(topic.label, list(topic.doc_titles)) for topic in offered] == [
            ("Product / Goals", ["Q4 goals"]),
            ("Engineering / APIs", ["Webhooks"]),
            ("Engineering / DB schema", ["Payments table"]),
        ]

    async def test_asks_the_writer_when_jev_is_unsure(
        self, signed_in: AsyncClient, judge: FakeJudge
    ) -> None:
        await _a_filed_project(signed_in)
        judge.topic_by_title = {"Retries": ("DB schema", 0.4)}

        response = await signed_in.post(
            "/projects/ATL/docs", json={"title": "Retries", "body": "…"}
        )

        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "topic_unclear"
        assert "name a topic" in error["message"]
        ranked = error["details"]["topics"]
        assert ranked[0]["name"] == "DB schema"
        assert ranked[0]["probability"] == 0.4
        assert {topic["name"] for topic in ranked} == {"Goals", "APIs", "DB schema"}
        tree = (await signed_in.get("/projects/ATL/docs")).json()
        assert tree["doc_count"] == 3, "nothing was written"

    async def test_asks_the_writer_when_jev_is_down(
        self, signed_in: AsyncClient, judge: FakeJudge
    ) -> None:
        await _a_filed_project(signed_in)
        judge.down = True

        response = await signed_in.post("/projects/ATL/docs", json={"title": "Retries"})

        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "topic_unclear"
        assert "timed out" in error["message"]
        assert len(error["details"]["topics"]) == 3

    async def test_the_only_topic_needs_no_question(
        self, signed_in: AsyncClient, judge: FakeJudge
    ) -> None:
        await signed_in.post("/projects", json=ATLAS)
        await _topic(signed_in, "engineering", "Notes")

        response = await signed_in.post("/projects/ATL/docs", json={"title": "Retries"})

        assert response.status_code == 201
        assert response.json()["filed_by"] == "only_topic"
        assert judge.offered == []

    async def test_a_project_with_no_topics_is_refused_and_none_is_made(
        self, signed_in: AsyncClient, judge: FakeJudge
    ) -> None:
        await signed_in.post("/projects", json=ATLAS)

        response = await signed_in.post("/projects/ATL/docs", json={"title": "Retries"})

        assert response.status_code == 422
        assert "Topics are made by people" in response.json()["error"]["message"]
        tree = (await signed_in.get("/projects/ATL/docs")).json()
        assert all(part["topics"] == [] for part in tree["sections"])

    async def test_a_title_already_in_the_topic_is_refused_without_append(
        self, signed_in: AsyncClient, judge: FakeJudge
    ) -> None:
        topics = await _a_filed_project(signed_in)

        response = await signed_in.post(
            "/projects/ATL/docs",
            json={"title": "webhooks", "body": "again", "topic_id": topics["apis"]["id"]},
        )

        assert response.status_code == 409
        assert "Set append" in response.json()["error"]["message"]

    async def test_append_adds_a_line_to_learned_md(
        self, signed_in: AsyncClient, judge: FakeJudge
    ) -> None:
        topics = await _a_filed_project(signed_in)
        apis = topics["apis"]["id"]
        first = await signed_in.post(
            "/projects/ATL/docs",
            json={
                "title": "learned.md",
                "body": "- Stripe signs with v1.",
                "topic_id": apis,
                "append": True,
            },
        )
        assert first.status_code == 201, "the first line makes the doc"

        second = await signed_in.post(
            "/projects/ATL/docs",
            json={
                "title": "learned.md",
                "body": "- Retries last 3 days.",
                "topic_id": apis,
                "append": True,
            },
        )

        assert second.status_code == 200
        written = second.json()
        assert written["created"] is False
        assert written["doc"]["id"] == first.json()["doc"]["id"]
        assert written["doc"]["body"] == "- Stripe signs with v1.\n- Retries last 3 days."

    async def test_a_topic_on_another_project_is_refused(
        self, signed_in: AsyncClient, judge: FakeJudge
    ) -> None:
        await _a_filed_project(signed_in)
        await signed_in.post("/projects", json={"key": "HRM", "name": "Hermes"})
        elsewhere = (
            await signed_in.post(
                "/projects/HRM/doc-topics", json={"section": "product", "name": "Goals"}
            )
        ).json()

        response = await signed_in.post(
            "/projects/ATL/docs", json={"title": "Retries", "topic_id": elsewhere["id"]}
        )

        assert response.status_code == 422
        assert response.json()["error"]["message"] == "That topic is not on this project."


class TestAppending:
    def test_a_list_item_continues_the_list(self) -> None:
        assert appended("- one\n", "- two") == "- one\n- two"

    def test_prose_starts_a_paragraph(self) -> None:
        assert appended("Intro.", "More.") == "Intro.\n\nMore."

    def test_onto_nothing_is_just_the_addition(self) -> None:
        assert appended("  \n", "- first") == "- first"


class TestSummaries:
    def test_skips_headings_and_folds_the_paragraph_to_a_line(self) -> None:
        body = "# Title\n## Sub\n\nThe **first** paragraph\nwraps here.\n\nSecond."
        assert summary_of(body) == "The first paragraph wraps here."

    def test_keeps_the_underscores_inside_a_name(self) -> None:
        assert summary_of("Run `write_doc` from _mcp_/cylist_mcp.") == (
            "Run write_doc from mcp/cylist_mcp."
        )

    def test_drops_the_marker_a_list_item_opens_with(self) -> None:
        assert summary_of("- First fact.\n- Second fact.") == "First fact. Second fact."

    def test_is_cut_short_with_an_ellipsis(self) -> None:
        assert summary_of("word " * 200).endswith("…")
        assert len(summary_of("word " * 200)) <= 240

    def test_an_empty_doc_has_none(self) -> None:
        assert summary_of("# Only a heading\n") == ""


class TestJevJudge:
    """The real judge, with jev's own client answering from a table."""

    async def test_asks_one_noul_per_doc_in_one_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[tuple[Any, list[str]]] = []

        async def ask(self: Any, state: Any, *questions: Any, model: Any = None) -> Result:
            seen.append((state, [question.name for question in questions]))
            return Result(
                {"doc_0": NoulAnswer(0.8), "doc_1": NoulAnswer(0.2)}, Usage(10), "jev-test"
            )

        monkeypatch.setattr("jev.AsyncJev.ask", ask)
        first, second = UUID(int=1), UUID(int=2)
        docs = [
            DocOnFile(first, "Webhooks", "Engineering", "APIs", "How events arrive."),
            DocOnFile(second, "Q4 goals", "Product", "Goals", ""),
        ]
        card = Card("ATL-1", "Dedupe", "Double payments.", ["Index"])

        probabilities = await JevJudge("key", model="jev-latest", timeout=1).relevance(card, docs)

        assert probabilities == {first: 0.8, second: 0.2}
        ((state, names),) = seen
        assert names == ["doc_0", "doc_1"]
        assert state == {
            "card": {
                "reference": "ATL-1",
                "title": "Dedupe",
                "description": "Double payments.",
                "checklist": ["Index"],
            }
        }

    async def test_files_by_one_choice_over_the_topics(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def ask(self: Any, state: Any, *questions: Any, model: Any = None) -> Result:
            (question,) = questions
            assert question.labels() == ["Product / Goals", "Engineering / APIs"]
            return Result(
                {
                    "topic": ChoiceAnswer(
                        "Engineering / APIs",
                        {"Product / Goals": 0.1, "Engineering / APIs": 0.9},
                        0.85,
                    )
                },
                Usage(10),
                "jev-test",
            )

        monkeypatch.setattr("jev.AsyncJev.ask", ask)
        goals, apis = UUID(int=1), UUID(int=2)
        topics = [
            TopicOnFile(goals, "Product", "Goals", []),
            TopicOnFile(apis, "Engineering", "APIs", ["Webhooks"]),
        ]

        filing = await JevJudge("key", model="jev-latest", timeout=1).filing("T", "B", topics)

        assert filing.topic_id == apis
        assert filing.confidence == 0.85
        assert list(filing.ranking) == [(apis, 0.9), (goals, 0.1)]

    async def test_a_jev_failure_is_unavailable_not_a_crash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from jev import JevConnectionError

        async def ask(self: Any, *args: Any, **kwargs: Any) -> Result:
            raise JevConnectionError("connection refused")

        monkeypatch.setattr("jev.AsyncJev.ask", ask)
        card = Card("ATL-1", "t", "d", [])
        docs = [DocOnFile(UUID(int=1), "Webhooks", "Engineering", "APIs", "")]

        with pytest.raises(JudgeUnavailableError, match="connection refused"):
            await JevJudge("key", model="jev-latest", timeout=1).relevance(card, docs)
