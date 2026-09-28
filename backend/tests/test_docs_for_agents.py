"""The questions an agent asks the docs, and the docs it writes back.

jev-docs is replaced by :class:`FakeEngine`, which answers with real jev-docs
routes and plans built over the corpus it was handed, and records what it was
asked. What is checked is Cylist's side: what a project's docs look like to
jev-docs, how its answers are addressed back to docs and topics, and what an
agent is told when there is no answer to go on. Two tests at the end run the
real router and planner over jev-docs' offline mock, to prove the plumbing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar
from uuid import uuid4

import pytest
from httpx import AsyncClient
from jevdocs import Action, Candidate, Change, Corpus, Hop, Plan, Route, mock_client

from app.models.doc import Doc, DocSection, DocTopic
from app.services import doc_corpus
from app.services.doc_corpus import summary_of
from app.services.doc_engine import EngineUnavailableError, JevDocEngine
from app.services.docs import appended

ATLAS = {"key": "ATL", "name": "Atlas Billing Migration"}

Router = Callable[[Corpus, str], Route]
Planner = Callable[[Corpus, Change], Plan]


@dataclass
class FakeEngine:
    """jev-docs, as whatever routes and plans the test builds."""

    route: Router | None = None
    plan: Planner | None = None
    down: bool = False
    asked: list[tuple[Corpus, str]] = field(default_factory=list)
    placed: list[tuple[Corpus, Change]] = field(default_factory=list)

    async def ask(self, corpus: Corpus, question: str) -> Route:
        if self.down:
            raise EngineUnavailableError("503 overloaded")
        self.asked.append((corpus, question))
        assert self.route is not None, "the test did not say how to route"
        return self.route(corpus, question)

    async def place(self, corpus: Corpus, change: Change) -> Plan:
        if self.down:
            raise EngineUnavailableError("timed out")
        self.placed.append((corpus, change))
        assert self.plan is not None, "the test did not say how to plan"
        return self.plan(corpus, change)


@pytest.fixture
def engine(signed_in: AsyncClient) -> FakeEngine:
    fake = FakeEngine()
    signed_in.app.state.doc_engine = fake  # type: ignore[attr-defined]
    return fake


def routed(
    ref: str,
    *,
    status: str = "ok",
    relevance: float | None = 0.93,
    also: str | None = None,
    weighed: tuple[str, ...] = (),
) -> Router:
    """A router that lands on ``ref`` — ``engineering/webhooks.md#retries`` —
    having weighed ``weighed`` too."""

    def candidate(corpus: Corpus, target: str) -> Candidate:
        path, _, anchor = target.partition("#")
        file = corpus.get(path)
        assert file is not None, f"{path} is not in the corpus: {[f.key for f in corpus.files()]}"
        # As the router does: a file of one section is routed to it.
        only = file.sections[0] if len(file.sections) == 1 else None
        section = file.section(anchor) if anchor else only
        return Candidate(
            file,
            0.8,
            section=Hop.certain("section", section.anchor) if section else None,
            section_obj=section,
            relevance=relevance,
        )

    def route(corpus: Corpus, question: str) -> Route:
        chosen = candidate(corpus, ref)
        found = Route(
            question=question,
            status=status,
            domain=Hop.certain("domain", chosen.file.domain),
            answerable=0.9,
            spans_domains=0.1,
            file=chosen.file,
            section_obj=chosen.section_obj,
            section=chosen.section,
            relevance=relevance,
            checked=[chosen, *(candidate(corpus, other) for other in weighed)],
            notes=[] if status == "ok" else ["no candidate passed the relevance check"],
        )
        if also is not None:
            twin = candidate(corpus, also)
            found.also = Route(
                question=question,
                status="ok",
                domain=Hop.certain("domain", twin.file.domain),
                answerable=1.0,
                spans_domains=0.0,
                file=twin.file,
                section_obj=twin.section_obj,
                relevance=relevance,
            )
        return found

    return route


def planned(*actions: Action) -> Planner:
    def plan(corpus: Corpus, change: Change) -> Plan:
        return Plan(change, [], list(actions))

    return plan


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


WEBHOOKS = """# Webhooks

How Stripe events reach us, and what we do with a duplicate.

## Index
1. Signing — how a delivery proves it came from Stripe
2. Retries — how long Stripe keeps retrying a failed delivery
3. Duplicates — what stops a redelivered event paying twice

## Signing
Every delivery carries a v1 signature, checked in webhooks/verify.py.

## Retries
Stripe retries a failed delivery for three days, backing off each time.

## Duplicates
The event id is unique in stripe_event, so a redelivery is a no-op.
"""


async def _a_filed_project(client: AsyncClient) -> dict[str, dict[str, Any]]:
    await client.post("/projects", json=ATLAS)
    apis = await _topic(client, "engineering", "APIs")
    schema = await _topic(client, "engineering", "DB schema")
    goals = await _topic(client, "product", "Goals")
    webhooks = await _doc(client, apis["id"], "Webhooks", WEBHOOKS)
    learned = await _doc(client, apis["id"], "learned.md", "- Stripe's test clock skips retries.\n")
    payments = await _doc(client, schema["id"], "Payments table", "One row per charge.")
    q4 = await _doc(client, goals["id"], "Q4 goals", "Ship search.")
    return {
        "apis": apis,
        "schema": schema,
        "goals": goals,
        "webhooks": webhooks,
        "learned": learned,
        "payments": payments,
        "q4": q4,
    }


class TestTheCorpus:
    """A project's rows as jev-docs reads them. No database, no jev."""

    @staticmethod
    def _topic(section: DocSection, name: str, *docs: tuple[str, str]) -> tuple[DocTopic, Any]:
        topic = DocTopic(id=uuid4(), section=section, name=name)
        return topic, [Doc(id=uuid4(), title=title, body=body) for title, body in docs]

    def test_sections_are_domains_topics_are_groups_docs_are_files(self) -> None:
        built = doc_corpus.build(
            "ATL",
            [
                self._topic(DocSection.ENGINEERING, "DB schema", ("Payments table", "One row.")),
                self._topic(DocSection.PRODUCT, "Goals", ("Q4 goals", "Ship search.")),
            ],
        )

        assert list(built.corpus.domains) == ["product", "engineering"]
        engineering = built.corpus.domains["engineering"]
        assert list(engineering.groups) == ["db-schema"]
        assert list(engineering.groups["db-schema"].files) == ["payments-table"]
        assert "Engineering / DB schema" in engineering.groups["db-schema"].description
        filed = built.doc_at("engineering/db-schema/payments-table.md")
        assert filed is not None
        assert filed.path == "Engineering / DB schema / Payments table"

    def test_both_domains_are_there_before_anything_is_written(self) -> None:
        built = doc_corpus.build("ATL", [])

        assert list(built.corpus.domains) == ["product", "engineering"]
        assert built.empty

    def test_a_doc_in_the_jev_docs_format_keeps_its_own_sections(self) -> None:
        file, shape = doc_corpus.as_file("Webhooks", WEBHOOKS, name="webhooks", domain="x")

        assert shape == "indexed"
        assert file.title == "Webhooks"
        assert file.summary_text() == "How Stripe events reach us, and what we do with a duplicate."
        assert [(s.title, s.blurb) for s in file.sections][1] == (
            "Retries",
            "how long Stripe keeps retrying a failed delivery",
        )
        assert file.sections[1].body.startswith("Stripe retries")

    def test_headings_become_sections_and_what_comes_first_is_an_overview(self) -> None:
        body = "# Deploys\n\nWe deploy from main.\n\n## Staging\nPort 8443.\n\n## Dev\nPort 9443."

        file, shape = doc_corpus.as_file("Deploys", body, name="deploys", domain="x")

        assert shape == "headings"
        assert [s.title for s in file.sections] == ["Overview", "Staging", "Dev"]
        assert file.sections[2].body == "Port 9443."
        assert file.sections[1].blurb == "Port 8443."
        assert file.summary_text() == "We deploy from main."

    def test_a_list_is_a_section_per_item(self) -> None:
        body = (
            "- `make db` cannot run here; there is no Docker.\n"
            "- Alembic revisions are numbered by hand,\n  so they collide across branches.\n"
        )

        file, shape = doc_corpus.as_file("learned.md", body, name="learned", domain="x")

        assert shape == "bullets"
        assert file.title == "What agents learned here"
        assert [s.body for s in file.sections] == [
            "`make db` cannot run here; there is no Docker.",
            "Alembic revisions are numbered by hand, so they collide across branches.",
        ]
        assert file.sections[1].title == "Alembic revisions are numbered by hand, so they…"

    def test_prose_is_one_section(self) -> None:
        file, shape = doc_corpus.as_file("Q4 goals", "Ship search.", name="q4", domain="x")

        assert shape == "plain"
        assert [(s.title, s.body) for s in file.sections] == [("Q4 goals", "Ship search.")]

    def test_sections_of_one_title_get_anchors_of_their_own(self) -> None:
        file, _ = doc_corpus.as_file("t", "- Retries\n- Retries\n", name="t", domain="x")

        assert len({s.anchor for s in file.sections}) == 2

    def test_every_topics_learned_md_is_a_file_of_its_own(self) -> None:
        built = doc_corpus.build(
            "ATL",
            [
                self._topic(DocSection.ENGINEERING, "APIs", ("learned.md", "- one")),
                self._topic(DocSection.ENGINEERING, "MCP", ("learned.md", "- two")),
            ],
        )

        files = built.corpus.domains["engineering"].files
        assert list(files) == ["learned", "mcp-learned"]
        second = built.doc_at("engineering/mcp/mcp-learned.md#two")
        assert second is not None
        assert second.path == "Engineering / MCP / learned.md"
        assert built.doc_at("engineering/mcp-learned.md") == second, "the group is optional"
        assert built.topic_at("engineering/mcp/(new topic).md") == second.topic


class TestAsking:
    async def test_an_ok_answer_is_the_section_and_where_it_lives(
        self, signed_in: AsyncClient, engine: FakeEngine
    ) -> None:
        filed = await _a_filed_project(signed_in)
        engine.route = routed("engineering/webhooks.md#retries")

        response = await signed_in.post(
            "/projects/ATL/docs/ask", json={"question": "  How long does Stripe retry?  "}
        )

        assert response.status_code == 200, response.text
        answer = response.json()
        assert answer["status"] == "ok"
        assert answer["question"] == "How long does Stripe retry?"
        assert answer["threshold"] == 0.7
        assert answer["found"] == {
            "doc_id": filed["webhooks"]["id"],
            "path": "Engineering / APIs / Webhooks",
            "section": "Retries",
            "text": "Stripe retries a failed delivery for three days, backing off each time.",
            "whole_doc": False,
            "relevance": 0.93,
        }
        assert answer["reason"] is None
        ((corpus, question),) = engine.asked
        assert question == "How long does Stripe retry?"
        assert sorted(file.key for file in corpus.files()) == [
            "engineering/learned.md",
            "engineering/payments-table.md",
            "engineering/webhooks.md",
            "product/q4-goals.md",
        ]

    async def test_a_miss_says_so_and_names_what_was_weighed(
        self, signed_in: AsyncClient, engine: FakeEngine
    ) -> None:
        filed = await _a_filed_project(signed_in)
        engine.route = routed(
            "engineering/webhooks.md#signing",
            status="unverified",
            relevance=0.41,
            weighed=("engineering/payments-table.md",),
        )

        answer = (
            await signed_in.post("/projects/ATL/docs/ask", json={"question": "Who owns refunds?"})
        ).json()

        assert answer["status"] == "unverified"
        assert answer["found"]["section"] == "Signing"
        assert answer["reason"] == "no candidate passed the relevance check"
        assert answer["alternatives"] == [
            {
                "doc_id": filed["payments"]["id"],
                "path": "Engineering / DB schema / Payments table",
                "section": "Payments table",
                "score": 0.8,
                "relevance": 0.41,
            }
        ]

    async def test_a_question_that_spans_why_and_how_brings_both_sides(
        self, signed_in: AsyncClient, engine: FakeEngine
    ) -> None:
        await _a_filed_project(signed_in)
        engine.route = routed("engineering/webhooks.md#duplicates", also="product/q4-goals.md")

        answer = (
            await signed_in.post("/projects/ATL/docs/ask", json={"question": "Why dedupe?"})
        ).json()

        assert answer["also"]["path"] == "Product / Goals / Q4 goals"
        assert answer["also"]["text"] == "Ship search."

    async def test_a_project_with_no_docs_is_not_documented_without_asking(
        self, signed_in: AsyncClient, engine: FakeEngine
    ) -> None:
        await signed_in.post("/projects", json=ATLAS)

        answer = (
            await signed_in.post("/projects/ATL/docs/ask", json={"question": "Anything?"})
        ).json()

        assert answer["status"] == "not_documented"
        assert answer["found"] is None
        assert engine.asked == []

    async def test_without_jev_every_question_is_unavailable(self, signed_in: AsyncClient) -> None:
        await _a_filed_project(signed_in)
        signed_in.app.state.doc_engine = None  # type: ignore[attr-defined]

        answer = (
            await signed_in.post("/projects/ATL/docs/ask", json={"question": "Retries?"})
        ).json()

        assert answer["status"] == "unavailable"
        assert answer["reason"] == "jev is not configured on this server."

    async def test_jev_down_is_a_miss_not_an_error(
        self, signed_in: AsyncClient, engine: FakeEngine
    ) -> None:
        await _a_filed_project(signed_in)
        engine.down = True

        response = await signed_in.post("/projects/ATL/docs/ask", json={"question": "Retries?"})

        assert response.status_code == 200
        assert response.json()["status"] == "unavailable"
        assert "503 overloaded" in response.json()["reason"]

    async def test_a_blank_question_is_refused(
        self, signed_in: AsyncClient, engine: FakeEngine
    ) -> None:
        await _a_filed_project(signed_in)

        response = await signed_in.post("/projects/ATL/docs/ask", json={"question": "   "})

        assert response.status_code == 422
        assert engine.asked == []

    async def test_a_card_no_longer_hands_out_docs(
        self, signed_in: AsyncClient, engine: FakeEngine
    ) -> None:
        await _a_filed_project(signed_in)
        created = await signed_in.post(
            "/projects/ATL/tasks",
            json={"title": "Dedupe", "description": "Double payments.", "type": "bug"},
        )

        response = await signed_in.get(f"/tasks/{created.json()['reference']}/docs")

        assert response.status_code in {404, 405}


class TestPlacing:
    FACTS: ClassVar[dict[str, Any]] = {
        "title": "Refund webhooks",
        "summary": "Refunds arrive as charge.refunded.",
        "entities": ["charge.refunded"],
        "facts": [
            {"text": "A refund arrives as charge.refunded.", "shape": "contract"},
            {"text": "Refunds are deduped by event id too."},
        ],
    }

    async def test_the_plan_is_addressed_by_doc_and_topic_and_nothing_is_written(
        self, signed_in: AsyncClient, engine: FakeEngine
    ) -> None:
        filed = await _a_filed_project(signed_in)
        engine.plan = planned(
            Action("add_section", "engineering/apis/webhooks.md", ["f0"], "no section holds it"),
            Action("add_index_entry", "engineering/apis/webhooks.md", ["f0"], "findable"),
            Action("update_section", "engineering/apis/webhooks.md#duplicates", ["f1"], "p=.8"),
            Action("new_file", "engineering/db-schema/(new topic).md", ["f1"], "nobody takes it"),
            Action("update_group_readme", "engineering/db-schema/", [], "name it"),
            Action("add_related", "engineering/webhooks.md -> engineering/payments-table.md"),
            Action("new_group", "product/(new group)/", [], "no product group fits"),
        )

        response = await signed_in.post("/projects/ATL/docs/place", json=self.FACTS)

        assert response.status_code == 200, response.text
        plan = response.json()
        assert plan["status"] == "planned"
        edits = {(edit["kind"], edit["path"]): edit for edit in plan["edits"]}
        assert list(edits) == [
            ("new_topic", None),
            ("new_file", "Engineering / DB schema"),
            ("add_section", "Engineering / APIs / Webhooks"),
            ("update_section", "Engineering / APIs / Webhooks"),
            ("add_index_entry", "Engineering / APIs / Webhooks"),
            ("add_related", "Engineering / APIs / Webhooks"),
        ], "in the order to make them; the README update is Cylist's own business"
        section = edits[("update_section", "Engineering / APIs / Webhooks")]
        assert section["section"] == "Duplicates"
        assert section["facts"] == [1]
        assert section["doc_id"] == filed["webhooks"]["id"]
        assert section["topic_id"] == filed["apis"]["id"]
        assert section["doc_shape"] == "indexed"
        assert edits[("new_file", "Engineering / DB schema")]["topic_id"] == filed["schema"]["id"]
        assert edits[("new_file", "Engineering / DB schema")]["doc_id"] is None
        related = edits[("add_related", "Engineering / APIs / Webhooks")]
        assert related["related_path"] == "Engineering / DB schema / Payments table"

        ((_, change),) = engine.placed
        assert [(fact.id, fact.text, fact.shape) for fact in change.facts] == [
            ("f0", "A refund arrives as charge.refunded.", "contract"),
            ("f1", "Refunds are deduped by event id too.", ""),
        ]
        assert change.entities == ["charge.refunded"]
        tree = (await signed_in.get("/projects/ATL/docs")).json()
        assert tree["doc_count"] == 4, "nothing was written"

    async def test_an_unsure_readme_update_is_the_fallback_topic_for_a_new_doc(
        self, signed_in: AsyncClient, engine: FakeEngine
    ) -> None:
        filed = await _a_filed_project(signed_in)
        engine.plan = planned(
            Action("update_group_readme", "engineering/apis/", [], "or put it here", 0.4, False)
        )

        plan = (await signed_in.post("/projects/ATL/docs/place", json=self.FACTS)).json()

        (edit,) = plan["edits"]
        assert (edit["kind"], edit["topic_id"], edit["sure"]) == (
            "new_file",
            filed["apis"]["id"],
            False,
        )

    async def test_without_jev_the_plan_is_unavailable(
        self, signed_in: AsyncClient, engine: FakeEngine
    ) -> None:
        await _a_filed_project(signed_in)
        engine.down = True

        plan = (await signed_in.post("/projects/ATL/docs/place", json=self.FACTS)).json()

        assert plan["status"] == "unavailable"
        assert "timed out" in plan["reason"]
        assert plan["edits"] == []

    async def test_a_project_with_no_topics_is_refused(
        self, signed_in: AsyncClient, engine: FakeEngine
    ) -> None:
        await signed_in.post("/projects", json=ATLAS)

        response = await signed_in.post("/projects/ATL/docs/place", json=self.FACTS)

        assert response.status_code == 422
        assert "Topics are made by people" in response.json()["error"]["message"]

    async def test_an_unknown_shape_is_refused(
        self, signed_in: AsyncClient, engine: FakeEngine
    ) -> None:
        await _a_filed_project(signed_in)
        body = {"title": "t", "facts": [{"text": "x", "shape": "vibe"}]}

        response = await signed_in.post("/projects/ATL/docs/place", json=body)

        assert response.status_code == 422


class TestTheRealEngineOffline:
    """jev-docs' own router and planner, over jev-docs' lexical mock of jev."""

    @pytest.fixture
    def offline(self, signed_in: AsyncClient) -> JevDocEngine:
        real = JevDocEngine(mock_client, relevance_threshold=0.7)
        signed_in.app.state.doc_engine = real  # type: ignore[attr-defined]
        return real

    async def test_a_question_lands_on_the_section_that_answers_it(
        self, signed_in: AsyncClient, offline: JevDocEngine
    ) -> None:
        await _a_filed_project(signed_in)

        answer = (
            await signed_in.post(
                "/projects/ATL/docs/ask",
                json={"question": "How many days does Stripe keep retrying a failed delivery?"},
            )
        ).json()

        assert answer["status"] == "ok", answer
        assert answer["found"]["path"] == "Engineering / APIs / Webhooks"
        assert answer["found"]["section"] == "Retries"

    async def test_a_fact_is_planned_into_the_docs(
        self, signed_in: AsyncClient, offline: JevDocEngine
    ) -> None:
        await _a_filed_project(signed_in)
        body = {
            "title": "Webhook signing secret",
            "facts": [{"text": "The Stripe webhook signature secret rotates every delivery."}],
        }

        plan = (await signed_in.post("/projects/ATL/docs/place", json=body)).json()

        assert plan["status"] == "planned", plan
        assert plan["edits"], plan
        assert all(edit["path"] or edit["kind"] == "new_topic" for edit in plan["edits"])


class TestWritingADoc:
    async def test_a_named_topic_is_where_it_goes(self, signed_in: AsyncClient) -> None:
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

    async def test_no_topic_on_a_project_with_several_is_refused_with_them_listed(
        self, signed_in: AsyncClient
    ) -> None:
        await _a_filed_project(signed_in)

        response = await signed_in.post(
            "/projects/ATL/docs", json={"title": "Retries", "body": "…"}
        )

        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "topic_unclear"
        assert "`place` says which" in error["message"]
        assert {topic["name"] for topic in error["details"]["topics"]} == {
            "Goals",
            "APIs",
            "DB schema",
        }
        tree = (await signed_in.get("/projects/ATL/docs")).json()
        assert tree["doc_count"] == 4, "nothing was written"

    async def test_the_only_topic_needs_no_naming(self, signed_in: AsyncClient) -> None:
        await signed_in.post("/projects", json=ATLAS)
        await _topic(signed_in, "engineering", "Notes")

        response = await signed_in.post("/projects/ATL/docs", json={"title": "Retries"})

        assert response.status_code == 201
        assert response.json()["filed_by"] == "only_topic"

    async def test_a_project_with_no_topics_is_refused_and_none_is_made(
        self, signed_in: AsyncClient
    ) -> None:
        await signed_in.post("/projects", json=ATLAS)

        response = await signed_in.post("/projects/ATL/docs", json={"title": "Retries"})

        assert response.status_code == 422
        assert "Topics are made by people" in response.json()["error"]["message"]
        tree = (await signed_in.get("/projects/ATL/docs")).json()
        assert all(part["topics"] == [] for part in tree["sections"])

    async def test_a_title_already_in_the_topic_is_refused_without_append(
        self, signed_in: AsyncClient
    ) -> None:
        topics = await _a_filed_project(signed_in)

        response = await signed_in.post(
            "/projects/ATL/docs",
            json={"title": "webhooks", "body": "again", "topic_id": topics["apis"]["id"]},
        )

        assert response.status_code == 409
        assert "Set append" in response.json()["error"]["message"]

    async def test_append_adds_a_line_to_learned_md(self, signed_in: AsyncClient) -> None:
        topics = await _a_filed_project(signed_in)
        schema = topics["schema"]["id"]
        first = await signed_in.post(
            "/projects/ATL/docs",
            json={
                "title": "learned.md",
                "body": "- Stripe signs with v1.",
                "topic_id": schema,
                "append": True,
            },
        )
        assert first.status_code == 201, "the first line makes the doc"

        second = await signed_in.post(
            "/projects/ATL/docs",
            json={
                "title": "learned.md",
                "body": "- Retries last 3 days.",
                "topic_id": schema,
                "append": True,
            },
        )

        assert second.status_code == 200
        written = second.json()
        assert written["created"] is False
        assert written["doc"]["id"] == first.json()["doc"]["id"]
        assert written["doc"]["body"] == "- Stripe signs with v1.\n- Retries last 3 days."

    async def test_a_topic_on_another_project_is_refused(self, signed_in: AsyncClient) -> None:
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
