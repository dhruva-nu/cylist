"""Route a reader's question to `domain/file.md#section`, then check the section is relevant.

    router = Router(corpus, Jev())
    route = router.route("why do we have 20 copies of Book A in inventory?")
    route.ref            # "product/inventory.md#book-a-minimum-stock"
    route.relevance      # P(this section is relevant), from reading the section text
    route.checked        # every candidate that was considered, with its scores
    route.explain()      # every hop with its probabilities

Hop 1 (one request): domain Choice + "answerable" Noul + "spans both domains" Noul, plus a
  group Choice for every grouped domain (the group READMEs), so groups cost no extra trip.
Hop 2 (one request): a file Choice per searched scope, over its files' summaries. A scope is
  one of the top `top_groups` groups of a grouped domain (P(file) = P(group) x P(file | group)),
  or a whole flat domain.
Hop 3 (one request): a section Choice for each of the top `top_files` files, together.
Check (one request per candidate, in parallel): a "relevant" Noul whose state is the
  question plus the candidate section's text. This is the only hop that reads a body.
Pick: the most probable candidate (P(file) x P(section)) that passes the check; if none
  passes, the most probable candidate, marked `unverified`.
When the question spans domains the router follows the file's `Related:` link (or its
same-named twin), routes and checks a section there too, returned as `route.also`.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Type

from jev import Choice, ChoiceAnswer, Jev, Noul, Result

from .corpus import Corpus, DocFile, Domain, Group, Section
from .questions import (
    domain_questions,
    group_question,
    relevance_question,
    relevance_state,
    section_question,
    topic_question,
    whole_file_question,
)

OK = "ok"
AMBIGUOUS = "ambiguous"
UNVERIFIED = "unverified"  # routed, but no candidate section passed the relevance check
NOT_DOCUMENTED = "not_documented"


@dataclass(frozen=True)
class Hop:
    """One Choice the router made, with the full distribution so callers can second-guess it."""

    name: str
    chosen: str
    probabilities: Dict[str, float]
    confidence: float

    @property
    def probability(self) -> float:
        return self.probabilities[self.chosen]

    def ranked(self) -> List[Tuple[str, float]]:
        return sorted(self.probabilities.items(), key=lambda kv: kv[1], reverse=True)

    def runner_up(self) -> Optional[Tuple[str, float]]:
        r = self.ranked()
        return r[1] if len(r) > 1 else None

    @staticmethod
    def of(name: str, answer: ChoiceAnswer) -> "Hop":
        return Hop(name, answer.choice, dict(answer.probabilities), answer.confidence)

    @staticmethod
    def certain(name: str, label: str) -> "Hop":
        return Hop(name, label, {label: 1.0}, 1.0)

    def to_dict(self) -> Dict[str, Any]:
        return {"chosen": self.chosen, "confidence": round(self.confidence, 3),
                "probabilities": {k: round(v, 3) for k, v in self.ranked()}}


@dataclass
class Candidate:
    """One file the section hop looked inside, and the section it picked there."""

    file: DocFile
    file_probability: float
    section: Optional[Hop] = None
    section_obj: Optional[Section] = None
    needs_whole_file: float = 0.0
    relevance: Optional[float] = None  # None until checked

    @property
    def score(self) -> float:
        """P(file) x P(section | file): how likely the route is before reading the text."""
        return self.file_probability * (self.section.probability if self.section else 1.0)

    @property
    def ref(self) -> str:
        return "%s#%s" % (self.file.ref, self.section_obj.anchor) if self.section_obj else self.file.ref

    @property
    def key(self) -> str:
        """The ref by file identity (`domain/name.md#anchor`): unchanged when the file changes group."""
        return "%s#%s" % (self.file.key, self.section_obj.anchor) if self.section_obj else self.file.key

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ref": self.ref,
            "file_probability": round(self.file_probability, 3),
            "section": self.section.to_dict() if self.section else None,
            "score": round(self.score, 3),
            "relevance": None if self.relevance is None else round(self.relevance, 3),
        }


@dataclass
class Route:
    question: str
    status: str
    domain: Hop
    answerable: float
    spans_domains: float
    group: Optional[Hop] = None  # the chosen file's domain's group Choice; None in a flat domain
    topic: Optional[Hop] = None  # over file refs, across every searched scope
    section: Optional[Hop] = None
    needs_whole_file: float = 0.0
    whole_file: bool = False  # set by the router when needs_whole_file crosses its threshold
    file: Optional[DocFile] = None
    section_obj: Optional[Section] = None
    relevance: Optional[float] = None  # P(the chosen section is relevant); None if not checked
    checked: List[Candidate] = field(default_factory=list)  # every candidate, best score first
    also: Optional["Route"] = None  # the twin route when the question spans domains
    searched_domains: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    requests: int = 0
    input_tokens: int = 0
    model: str = ""

    @property
    def ref(self) -> Optional[str]:
        if self.file is None:
            return None
        if self.section_obj is None:
            return self.file.ref
        return "%s#%s" % (self.file.ref, self.section_obj.anchor)

    @property
    def key(self) -> Optional[str]:
        """The ref by file identity (`domain/name.md#anchor`): unchanged when the file changes group."""
        if self.file is None:
            return None
        return self.file.key if self.section_obj is None else "%s#%s" % (self.file.key, self.section_obj.anchor)

    @property
    def verified(self) -> bool:
        return self.relevance is not None and self.status != UNVERIFIED

    @property
    def text(self) -> str:
        if self.file is None:
            return ""
        if self.section_obj is None or self.whole_file:
            return "\n\n".join("## %s\n%s" % (s.title, s.body) for s in self.file.sections)
        return self.section_obj.body

    def alternatives(self, limit: int = 3) -> List[str]:
        """Other refs worth showing when the route is not confident, best first."""
        out: List[str] = []
        for c in self.checked:
            if c.ref != self.ref:
                out.append("%s (score=%.2f, relevance=%s)" % (c.ref, c.score, "-" if c.relevance is None else "%.2f" % c.relevance))
        if self.file and self.section:
            for anchor, p in self.section.ranked()[1:limit]:
                out.append("%s#%s (p=%.2f)" % (self.file.ref, anchor, p))
        return out[:limit]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question": self.question,
            "status": self.status,
            "ref": self.ref,
            "relevance": None if self.relevance is None else round(self.relevance, 3),
            "domain": self.domain.to_dict(),
            "group": self.group.to_dict() if self.group else None,
            "topic": self.topic.to_dict() if self.topic else None,
            "section": self.section.to_dict() if self.section else None,
            "checked": [c.to_dict() for c in self.checked],
            "answerable": round(self.answerable, 3),
            "spans_domains": round(self.spans_domains, 3),
            "needs_whole_file": round(self.needs_whole_file, 3),
            "searched_domains": self.searched_domains,
            "also": self.also.to_dict() if self.also else None,
            "notes": self.notes,
            "requests": self.requests,
            "input_tokens": self.input_tokens,
            "model": self.model,
        }

    def explain(self, show_text: bool = False) -> str:
        lines = ["question: %s" % self.question, "status:   %s" % self.status]
        lines.append("domain:   %s  (p=%.2f, conf=%.2f)  answerable=%.2f spans=%.2f"
                     % (self.domain.chosen, self.domain.probability, self.domain.confidence,
                        self.answerable, self.spans_domains))
        if len(self.searched_domains) > 1:
            lines.append("          searched: %s" % ", ".join(self.searched_domains))
        if self.group:
            lines.append("group:    %s" % ", ".join("%s p=%.2f" % kv for kv in self.group.ranked()[:3]))
        if self.topic:
            lines.append("files:    %s" % ", ".join("%s p=%.2f" % kv for kv in self.topic.ranked()[:3]))
        for c in self.checked:
            mark = "->" if c.ref == self.ref else "  "
            rel = "not checked" if c.relevance is None else "relevant=%.2f" % c.relevance
            lines.append("checked:  %s %s  score=%.2f (file %.2f x section %.2f)  %s"
                         % (mark, c.ref, c.score, c.file_probability, c.section.probability if c.section else 1.0, rel))
        lines.append("route:    %s" % (self.ref or "-"))
        if self.also and self.also.ref:
            rel = "" if self.also.relevance is None else "  relevant=%.2f" % self.also.relevance
            lines.append("also:     %s  (p=%.2f)%s" % (self.also.ref, self.also.section.probability if self.also.section else 0, rel))
        for n in self.notes:
            lines.append("note:     %s" % n)
        lines.append("cost:     %d request(s), %d input tokens, model %s" % (self.requests, self.input_tokens, self.model))
        if show_text and self.text:
            lines += ["", "--- %s ---" % self.ref, self.text]
            if self.also and self.also.text:
                lines += ["", "--- %s ---" % self.also.ref, self.also.text]
        return "\n".join(lines)


class Router:
    def __init__(
        self,
        corpus: Corpus,
        jev: Jev,
        *,
        top_groups: int = 2,
        top_files: int = 2,
        verify: bool = True,
        relevance_threshold: float = 0.7,
        answerable_threshold: float = 0.3,
        min_domain_confidence: float = 0.25,
        min_topic_probability: float = 0.35,
        min_section_probability: float = 0.35,
        spans_threshold: float = 0.6,
        whole_file_threshold: float = 0.6,
        follow_related: bool = True,
    ):
        if top_files < 1 or top_groups < 1:
            raise ValueError("top_files and top_groups must be >= 1")
        self.corpus = corpus
        self.jev = jev
        self.top_groups = top_groups
        self.top_files = top_files
        self.verify = verify
        self.relevance_threshold = relevance_threshold
        self.answerable_threshold = answerable_threshold
        self.min_domain_confidence = min_domain_confidence
        self.min_topic_probability = min_topic_probability
        self.min_section_probability = min_section_probability
        self.spans_threshold = spans_threshold
        self.whole_file_threshold = whole_file_threshold
        self.follow_related = follow_related
        # Questions are built once: the corpus is static for the life of the router.
        self._domain_q, self._answerable_q, self._spans_q = domain_questions(corpus)
        self._group_q: Dict[str, Type[Choice]] = {
            n: group_question(d, "group_%d" % i) for i, (n, d) in enumerate(corpus.domains.items()) if len(d.groups) > 1}
        # One file Choice per scope (a group, or a flat domain), each with its own name so that
        # every searched scope's Choice fits in one request.
        scopes = [sc for d in corpus.domains.values() for sc in d.scopes()]
        self._topic_q: Dict[int, Type[Choice]] = {
            id(sc): topic_question(sc, "topic_%d" % i) for i, sc in enumerate(scopes) if len(sc.files) > 1}
        self._section_q: Dict[Tuple[str, int], Tuple[Type[Choice], Type[Noul]]] = {}
        self._relevance_q = relevance_question()

    # ------------------------------------------------------------------ public
    def route(self, question: str) -> Route:
        r1 = self.jev.ask(question, self._domain_q, self._answerable_q, self._spans_q, *self._group_q.values())
        route = Route(
            question=question,
            status=OK,
            domain=Hop.of("domain", r1[self._domain_q]),
            answerable=r1[self._answerable_q].probability,
            spans_domains=r1[self._spans_q].probability,
        )
        self._charge(route, r1)
        group_hops = {n: Hop.of("group", r1[q]) for n, q in self._group_q.items()}

        if route.answerable < self.answerable_threshold:
            route.status = NOT_DOCUMENTED
            route.notes.append("P(answerable from docs)=%.2f is below %.2f" % (route.answerable, self.answerable_threshold))
            return route

        # Hop 2: which files. Search only the chosen domain unless the domain call was unsure;
        # in a grouped domain, only inside its top groups.
        domains = [route.domain.chosen]
        if route.domain.confidence < self.min_domain_confidence:
            domains = [n for n, _ in route.domain.ranked()]
            route.notes.append("domain confidence %.2f < %.2f; searching every domain" % (route.domain.confidence, self.min_domain_confidence))
        scopes: List[Tuple["Domain | Group", float]] = []
        for name in domains:
            domain = self.corpus.domains[name]
            if not domain.files:
                continue
            route.searched_domains.append(name)
            if domain.grouped:
                hop = group_hops.get(name) or Hop.certain("group", next(iter(domain.groups)))
                scopes += [(domain.groups[g], p) for g, p in hop.ranked()[: self.top_groups]]
            else:
                scopes.append((domain, 1.0))
        ranked_files = self._files_hop(question, scopes, route)
        if not ranked_files:
            route.status = NOT_DOCUMENTED
            route.notes.append("no routable domain")
            return route
        probs = {doc.ref: p for doc, p in ranked_files}
        top = sorted(probs.values(), reverse=True) + [0.0]
        route.topic = Hop("topic", ranked_files[0][0].ref, probs, top[0] - top[1])

        # Hop 3: sections for the top files, one request. Then read and check each candidate.
        candidates = [Candidate(doc, p) for doc, p in ranked_files[: self.top_files]]
        self._sections_hop(question, candidates, route)
        if self.verify:
            self._check(question, candidates, route)
        self._pick(candidates, route)
        if route.file is not None and route.file.group:
            route.group = group_hops.get(route.file.domain) or Hop.certain("group", route.file.group)

        # Follow the twin when the reader asked for both why and how.
        if self.follow_related and route.spans_domains >= self.spans_threshold:
            twin = self._twin(route.file)
            if twin is not None:
                route.also = self._route_within(question, twin)
                route.requests += route.also.requests
                route.input_tokens += route.also.input_tokens
                route.notes.append("question spans domains (P=%.2f); also routed inside %s" % (route.spans_domains, twin.ref))
        return route

    # ------------------------------------------------------------------ hops
    def _files_hop(self, question: str, scopes: List[Tuple["Domain | Group", float]], route: Route) -> List[Tuple[DocFile, float]]:
        """One request: a file Choice per scope. Returns every file with P(scope) x P(file | scope), best first."""
        ranked: List[Tuple[DocFile, float]] = []
        asked = [(sc, prior) for sc, prior in scopes if id(sc) in self._topic_q]
        for sc, prior in scopes:
            if id(sc) not in self._topic_q:  # a one-file scope has nothing to choose between
                ranked += [(doc, prior) for doc in sc.files.values()]
        if asked:
            r = self.jev.ask(question, *[self._topic_q[id(sc)] for sc, _ in asked])
            self._charge(route, r)
            for sc, prior in asked:
                answer = r[self._topic_q[id(sc)]]
                ranked += [(sc.files[label], prior * p) for label, p in answer.probabilities.items()]
        ranked.sort(key=lambda fp: fp[1], reverse=True)
        return ranked

    def _sections_hop(self, question: str, candidates: List[Candidate], route: Route) -> None:
        """One request: a section Choice and a whole-file Noul per candidate file."""
        asked: List[Tuple[Candidate, Type[Choice], Type[Noul]]] = []
        for i, c in enumerate(candidates):
            if len(c.file.sections) < 2:
                c.section_obj = c.file.sections[0] if c.file.sections else None
                c.section = Hop.certain("section", c.section_obj.anchor) if c.section_obj else None
                continue
            key = (c.file.ref, i)
            if key not in self._section_q:
                self._section_q[key] = (section_question(c.file, "section_%d" % i), whole_file_question(c.file, "whole_file_%d" % i))
            sq, wq = self._section_q[key]
            asked.append((c, sq, wq))
        if not asked:
            return
        r = self.jev.ask(question, *[q for _, sq, wq in asked for q in (sq, wq)])
        self._charge(route, r)
        for c, sq, wq in asked:
            c.section = Hop.of("section", r[sq])
            c.section_obj = c.file.section(c.section.chosen)
            c.needs_whole_file = r[wq].probability

    def _check(self, question: str, candidates: List[Candidate], route: Route) -> None:
        """Ask, per candidate and in parallel, whether its section text is relevant."""
        todo = [c for c in candidates if c.section_obj is not None]

        def ask(c: Candidate) -> Result:
            state = relevance_state(question, c.file, c.section_obj.title, c.section_obj.body)
            return self.jev.ask(state, self._relevance_q)

        with ThreadPoolExecutor(max_workers=max(len(todo), 1)) as pool:
            results = list(pool.map(ask, todo))
        for c, r in zip(todo, results):
            c.relevance = r[self._relevance_q].probability
            self._charge(route, r)

    def _pick(self, candidates: List[Candidate], route: Route) -> None:
        ranked = sorted(candidates, key=lambda c: c.score, reverse=True)
        route.checked = ranked
        chosen = ranked[0]
        if self.verify:
            passing = [c for c in ranked if c.relevance is not None and c.relevance >= self.relevance_threshold]
            if passing:
                chosen = passing[0]
                if chosen is not ranked[0]:
                    route.notes.append("%s scored higher but failed the relevance check (%.2f); chose %s (%.2f)"
                                       % (ranked[0].ref, ranked[0].relevance or 0, chosen.ref, chosen.relevance))
            else:
                route.status = UNVERIFIED
                route.notes.append("no candidate passed the relevance check (threshold %.2f)" % self.relevance_threshold)
        route.file = chosen.file
        route.section = chosen.section
        route.section_obj = chosen.section_obj
        route.needs_whole_file = chosen.needs_whole_file
        route.whole_file = chosen.needs_whole_file >= self.whole_file_threshold
        route.relevance = chosen.relevance
        if route.status == OK:
            if chosen.file_probability < self.min_topic_probability:
                route.status = AMBIGUOUS
                route.notes.append("file probability %.2f < %.2f" % (chosen.file_probability, self.min_topic_probability))
            elif chosen.section and chosen.section.probability < self.min_section_probability:
                route.status = AMBIGUOUS
                route.notes.append("section probability %.2f < %.2f" % (chosen.section.probability, self.min_section_probability))

    def _route_within(self, question: str, doc: DocFile) -> Route:
        """A route that skips hops 1 and 2 because the file is already known."""
        sub = Route(question=question, status=OK, domain=Hop.certain("domain", doc.domain),
                    answerable=1.0, spans_domains=0.0, topic=Hop.certain("topic", doc.ref),
                    group=Hop.certain("group", doc.group) if doc.group else None,
                    searched_domains=[doc.domain])
        candidates = [Candidate(doc, 1.0)]
        self._sections_hop(question, candidates, sub)
        if self.verify:
            self._check(question, candidates, sub)
        self._pick(candidates, sub)
        return sub

    def _twin(self, doc: DocFile) -> Optional[DocFile]:
        for ref in doc.related:
            twin = self.corpus.get(ref)
            if twin is not None and twin.domain != doc.domain:
                return twin
        for name, domain in self.corpus.domains.items():
            if name != doc.domain and doc.name in domain.files:
                return domain.files[doc.name]
        return None

    @staticmethod
    def _charge(route: Route, result: Result) -> None:
        route.requests += 1
        route.input_tokens += result.usage.input_tokens
        route.model = result.model
