"""Place a change to the software into the docs: every file and section it touches.

Routing takes a reader's question to the ONE section that answers it. Placement goes the
other way: one change fans out into many edits, some of them in sections that do not exist
yet. The caller (an agent, or a person) splits the change into facts; Jev only classifies.

    change = load_change("poc/changes/book-c.json")
    plan = Placer(corpus, Jev()).place(change)
    plan.actions        # add_section / update_section / new_file / update_summary / ...
    plan.explain()

Per fact, in parallel:
  touches   one request: a "does this file need an edit?" Noul per file, over each file's
            summary and index (the same text routing reads). In a grouped domain the Noul is
            per group instead (its README and file summaries), and the files of the touched
            groups are asked in the next request, so a big repo costs no more requests
  place     one request: for each touched file, a section Choice with an extra
            `new_section` option
Then, once for the whole change:
  affected  one request per section of every touched file, in parallel: state = {the facts
            that touched the file, the section text}, "is this section wrong or incomplete
            after the change?". This finds the ripples a fact does not name: the rules
            overview table, the history section, a failure-modes list.
Then, without Jev:
  indexing  every added section needs an index entry; every file that gains a fact must
            name the change's entities in its summary, or questions about them will never
            reach it; files touched by the same fact should link with `Related:`;
            a fact no file in its domain accepts becomes a new file.
  home      in a grouped domain, one more request per domain that needs a new file: a Choice
            over its groups (and "a new group") for where that file goes. A Noul per group
            says whether a group clearly holds a fact; which group a new topic fits best is a
            relative question, so it is a Choice.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type

from jev import Choice, Jev, Noul, Result

from .corpus import GROUP_FILES, INDEX_ENTRIES, Corpus, DocFile, Group, Section
from .questions import (
    NEW_GROUP as NEW_GROUP_LABEL,
    NEW_SECTION,
    new_file_group_question,
    affected_question,
    affected_state,
    domain_questions,
    group_touches_question,
    place_question,
    touches_question,
)

# The section shapes of DOCS_FORMAT.md §5 and the domain each usually lives in.
SHAPES = {
    "rule": "product", "history": "product", "contract": "engineering", "mechanism": "engineering",
    "lifecycle": None, "failure": None, "glossary": None,
}

ADD_SECTION = "add_section"
UPDATE_SECTION = "update_section"
NEW_GROUP = "new_group"
NEW_FILE = "new_file"
UPDATE_GROUP_README = "update_group_readme"
SPLIT_GROUP = "split_group"
ADD_INDEX_ENTRY = "add_index_entry"
UPDATE_SUMMARY = "update_summary"
ADD_RELATED = "add_related"
SPLIT_FILE = "split_file"
CHECK_TWIN = "check_twin"

LINK = " -> "  # an add_related ref between two existing files: "a.md -> b.md"

ORDER = [NEW_GROUP, NEW_FILE, ADD_SECTION, UPDATE_SECTION, ADD_INDEX_ENTRY, UPDATE_SUMMARY, UPDATE_GROUP_README,
         ADD_RELATED, SPLIT_FILE, SPLIT_GROUP, CHECK_TWIN]


# ----------------------------------------------------------------------------- the change
@dataclass
class Fact:
    id: str
    text: str
    shape: str = ""

    @property
    def expected_domain(self) -> Optional[str]:
        return SHAPES.get(self.shape)


@dataclass
class Change:
    title: str
    facts: List[Fact]
    summary: str = ""
    entities: List[str] = field(default_factory=list)  # proper nouns a reader will type: "Book C", "Kafka"

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Change":
        facts = []
        for i, f in enumerate(d.get("facts") or []):
            f = {"text": f} if isinstance(f, str) else f
            shape = (f.get("shape") or "").lower()
            if shape and shape not in SHAPES:
                raise ValueError("fact %r: shape %r is not one of %s" % (f.get("id", i), shape, ", ".join(SHAPES)))
            facts.append(Fact(str(f.get("id") or "f%d" % (i + 1)), f["text"].strip(), shape))
        if not facts:
            raise ValueError("a change needs at least one fact")
        ids = [f.id for f in facts]
        if len(set(ids)) != len(ids):
            raise ValueError("fact ids must be unique: %r" % ids)
        return Change(d.get("title") or facts[0].text[:60], facts, d.get("summary", ""), list(d.get("entities") or []))


def load_change(path: str | Path) -> Change:
    return Change.from_dict(json.loads(Path(path).read_text()))


# ----------------------------------------------------------------------------- the plan
@dataclass
class Action:
    kind: str
    ref: str  # "domain/file.md", "domain/file.md#anchor", or a proposed "domain/<new>.md"
    facts: List[str] = field(default_factory=list)
    why: str = ""
    probability: float = 1.0  # 1.0 for actions that follow from others without Jev
    sure: bool = True  # False: worth a look, not worth doing blind

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "ref": self.ref, "facts": self.facts, "why": self.why,
                "probability": round(self.probability, 3), "sure": self.sure}


@dataclass
class Placement:
    """Where one fact landed, with the distributions behind it."""

    fact: Fact
    domain: str
    domain_probabilities: Dict[str, float]
    touches: Dict[str, float]  # file ref -> P(file needs an edit); 0 for files in groups it does not touch
    sections: Dict[str, Dict[str, float]] = field(default_factory=dict)  # file ref -> {anchor | new_section: p}
    groups: Dict[str, float] = field(default_factory=dict)  # group ref -> P(group needs an edit)

    def ranked_files(self) -> List[Tuple[str, float]]:
        return sorted(self.touches.items(), key=lambda kv: kv[1], reverse=True)

    def to_dict(self) -> Dict[str, Any]:
        return {"fact": self.fact.id, "shape": self.fact.shape, "domain": self.domain,
                "domain_probabilities": {k: round(v, 3) for k, v in self.domain_probabilities.items()},
                "groups": {k: round(v, 3) for k, v in sorted(self.groups.items(), key=lambda kv: -kv[1])},
                "touches": {k: round(v, 3) for k, v in self.ranked_files()},
                "sections": {f: {k: round(v, 3) for k, v in sorted(d.items(), key=lambda kv: -kv[1])} for f, d in self.sections.items()}}


@dataclass
class Plan:
    change: Change
    placements: List[Placement]
    actions: List[Action]
    affected: Dict[str, float] = field(default_factory=dict)  # "file#anchor" -> P(affected)
    homes: Dict[str, Dict[str, float]] = field(default_factory=dict)  # domain -> {group | new_group: p} for a new file
    requests: int = 0
    input_tokens: int = 0
    model: str = ""

    def by_file(self) -> Dict[str, List[Action]]:
        out: Dict[str, List[Action]] = {}
        for a in sorted(self.actions, key=lambda a: (ORDER.index(a.kind), -a.probability)):
            out.setdefault(a.ref.split("#")[0].split(LINK)[0], []).append(a)
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {"change": self.change.title,
                "actions": [a.to_dict() for a in self.actions],
                "placements": [p.to_dict() for p in self.placements],
                "affected": {k: round(v, 3) for k, v in sorted(self.affected.items(), key=lambda kv: -kv[1])},
                "requests": self.requests, "input_tokens": self.input_tokens, "model": self.model}

    def explain(self, verbose: bool = False) -> str:
        facts = {f.id: f for f in self.change.facts}
        lines = ["change:  %s" % self.change.title]
        for f in self.change.facts:
            lines.append("  %-10s %-9s %s" % (f.id, f.shape or "-", f.text[:100] + ("…" if len(f.text) > 100 else "")))
        lines.append("")
        maybe: List[Action] = []
        for ref, actions in self.by_file().items():
            sure = [a for a in actions if a.sure]
            maybe += [a for a in actions if not a.sure]
            if not sure:
                continue
            lines.append(ref)
            for a in sure:
                lines.append("  %-16s %-40s %s" % (a.kind.upper(), _tail(a.ref), _facts(a, facts)))
                if a.why:
                    lines.append("  %-16s %s" % ("", a.why))
        if maybe:
            lines += ["", "maybe (below the threshold; confirm before editing)"]
            for a in maybe:
                lines.append("  %-16s %-48s p=%.2f  %s" % (a.kind.upper(), a.ref, a.probability, a.why))
        if verbose:
            lines += ["", "placements"]
            for p in self.placements:
                lines.append("  %s  domain %s (p=%.2f)" % (p.fact.id, p.domain, p.domain_probabilities.get(p.domain, 0)))
                for ref, pg in sorted(p.groups.items(), key=lambda kv: -kv[1])[:4]:
                    lines.append("      group   %-34s p=%.2f" % (ref, pg))
                for ref, pt in p.ranked_files()[:5]:
                    lines.append("      touches %-34s p=%.2f" % (ref, pt))
                for ref, dist in p.sections.items():
                    top = sorted(dist.items(), key=lambda kv: -kv[1])[:3]
                    lines.append("      in %-39s %s" % (ref, "  ".join("%s=%.2f" % kv for kv in top)))
            lines.append("  affected sections: " + ", ".join("%s=%.2f" % kv for kv in sorted(self.affected.items(), key=lambda kv: -kv[1])[:8]))
        lines += ["", "cost:    %d request(s), %d input tokens, model %s" % (self.requests, self.input_tokens, self.model)]
        return "\n".join(lines)


def _tail(ref: str) -> str:
    if LINK in ref:
        return "-> " + ref.split(LINK, 1)[1]
    return "#" + ref.split("#", 1)[1] if "#" in ref else ""


def _facts(a: Action, facts: Dict[str, Fact]) -> str:
    return "[%s]" % ", ".join(a.facts) if a.facts else ""


# ----------------------------------------------------------------------------- the placer
class Placer:
    def __init__(
        self,
        corpus: Corpus,
        jev: Jev,
        *,
        touch_threshold: float = 0.5,
        maybe_threshold: float = 0.3,
        affected_threshold: float = 0.6,
        strong_home: float = 0.7,
        max_workers: int = 8,
    ):
        self.corpus = corpus
        self.jev = jev
        self.touch_threshold = touch_threshold
        self.maybe_threshold = maybe_threshold
        self.affected_threshold = affected_threshold
        self.strong_home = strong_home  # below this, a fact's best existing file is a weak home
        self.max_workers = max_workers
        self._domain_q = domain_questions(corpus)[0]
        self._files = list(corpus.files())
        self._touch_q: Dict[str, Type[Noul]] = {d.ref: touches_question(d, "touches_%d" % i) for i, d in enumerate(self._files)}
        self._groups: List[Group] = list(corpus.groups())
        self._group_q: Dict[str, Type[Noul]] = {g.ref: group_touches_question(g, "group_touches_%d" % i) for i, g in enumerate(self._groups)}
        self._affected_q = affected_question()

    # ------------------------------------------------------------------ public
    def place(self, change: Change) -> Plan:
        plan = Plan(change, [], [])
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            plan.placements = list(pool.map(lambda f: self._place_fact(f, plan), change.facts))
            homes = pool.submit(self._homes, plan)
            plan.affected = self._affected(change, plan, pool)
            plan.homes = homes.result()
        plan.actions = self._actions(change, plan)
        return plan

    # ------------------------------------------------------------------ hops
    def _place_fact(self, fact: Fact, plan: Plan) -> Placement:
        """Two requests. First: the groups it touches, and every file of a flat domain. Second: the
        files of the touched groups, plus where the fact goes in every file touched so far."""
        state = self._state(fact, plan.change)
        flat = [d for d in self._files if not d.group]
        r = self.jev.ask(state, self._domain_q, *[self._group_q[g.ref] for g in self._groups], *[self._touch_q[d.ref] for d in flat])
        self._charge(plan, r)
        dom = r[self._domain_q]
        p = Placement(fact, dom.choice, dict(dom.probabilities), {d.ref: 0.0 for d in self._files})
        p.groups = {g.ref: r[self._group_q[g.ref]].probability for g in self._groups}
        p.touches.update({d.ref: r[self._touch_q[d.ref]].probability for d in flat})

        in_groups = [d for g in self._groups if p.groups[g.ref] >= self.maybe_threshold for d in g.files.values()]
        touched_flat = [d for d in flat if p.touches[d.ref] >= self.maybe_threshold and d.sections]
        # A grouped file's Choice goes out before we know it is touched: one request, not two.
        placeable = touched_flat + [d for d in in_groups if d.sections]
        asked: List[Tuple[DocFile, Type[Choice]]] = [(d, place_question(d, "place_%d" % i)) for i, d in enumerate(placeable)]
        if asked or in_groups:
            r = self.jev.ask(state, *[self._touch_q[d.ref] for d in in_groups], *[q for _, q in asked])
            self._charge(plan, r)
            p.touches.update({d.ref: r[self._touch_q[d.ref]].probability for d in in_groups})
            for doc, q in asked:
                if p.touches[doc.ref] >= self.maybe_threshold:
                    p.sections[doc.ref] = dict(r[q].probabilities)
        return p

    def _unplaced(self, plan: Plan) -> Dict[str, List[Fact]]:
        """domain -> the facts no file of that domain accepts: they need a new file."""
        out: Dict[str, List[Fact]] = {}
        for pl in plan.placements:
            domain = pl.fact.expected_domain or pl.domain
            if not any(ref.startswith(domain + "/") and pt >= self.touch_threshold for ref, pt in pl.touches.items()):
                out.setdefault(domain, []).append(pl.fact)
        return out

    def _homes(self, plan: Plan) -> Dict[str, Dict[str, float]]:
        """One request per grouped domain that needs a new file: which group should hold it."""
        out = {}
        for domain, facts in self._unplaced(plan).items():
            dom = self.corpus.domains.get(domain)
            if dom is None or not dom.grouped:
                continue
            if len(dom.groups) == 1:
                out[domain] = {next(iter(dom.groups)): 1.0}
                continue
            q = new_file_group_question(dom)
            state: Dict[str, Any] = {"facts": [f.text for f in facts]}
            if plan.change.summary:
                state["part_of_change"] = plan.change.summary
            r = self.jev.ask(state, q)
            self._charge(plan, r)
            out[domain] = dict(r[q].probabilities)
        return out

    def _affected(self, change: Change, plan: Plan, pool: ThreadPoolExecutor) -> Dict[str, float]:
        """Read every section of every touched file against the facts that touched it."""
        work: List[Tuple[DocFile, Section, List[Dict[str, str]]]] = []
        for doc in self._files:
            facts = [pl.fact for pl in plan.placements if pl.touches[doc.ref] >= self.maybe_threshold]
            if not facts:
                continue
            payload = [{"id": f.id, "shape": f.shape, "text": f.text} for f in facts]
            work += [(doc, s, payload) for s in doc.sections if s.body]

        def ask(item) -> Result:
            doc, s, payload = item
            return self.jev.ask(affected_state(payload, doc, s.title, s.body), self._affected_q)

        results = list(pool.map(ask, work))
        out = {}
        for (doc, s, _), r in zip(work, results):
            self._charge(plan, r)
            out["%s#%s" % (doc.ref, s.anchor)] = r[self._affected_q].probability
        return out

    # ------------------------------------------------------------------ the plan, no Jev from here on
    def _actions(self, change: Change, plan: Plan) -> List[Action]:
        acts: Dict[Tuple[str, str], Action] = {}

        def add(kind: str, ref: str, fact: Optional[str], why: str, p: float = 1.0, sure: bool = True) -> Action:
            a = acts.get((kind, ref))
            if a is None:
                a = acts[(kind, ref)] = Action(kind, ref, [], why, p, sure)
            elif sure and not a.sure:
                a.facts, a.why, a.probability, a.sure = [], why, p, True  # a sure reason replaces a maybe
            elif a.sure and not sure:
                return a  # a maybe reason adds nothing to a sure action
            elif p > a.probability:
                a.why, a.probability = why, p
            if fact and fact not in a.facts:
                a.facts.append(fact)
            return a

        gains: Dict[str, List[str]] = {}  # file ref -> fact ids that land in it
        new_files = self._unplaced(plan)  # domain -> facts nobody accepted

        for pl in plan.placements:
            fid = pl.fact.id
            for ref, pt in pl.ranked_files():
                if pt < self.maybe_threshold:
                    break
                sure = pt >= self.touch_threshold
                dist = pl.sections.get(ref) or {}
                if not dist:
                    continue
                target, p_target = max(dist.items(), key=lambda kv: kv[1])
                if target == NEW_SECTION:
                    near = max(((a, p) for a, p in dist.items() if a != NEW_SECTION), key=lambda kv: kv[1], default=None)
                    why = "no section holds this %s" % (pl.fact.shape or "fact")
                    if near:
                        why += "; place it near #%s" % near[0]
                    add(ADD_SECTION, ref, fid, why, pt * p_target, sure)
                    if sure:
                        add(ADD_INDEX_ENTRY, ref, fid, "a new section needs an index entry with a blurb that says what it answers")
                else:
                    add(UPDATE_SECTION, "%s#%s" % (ref, target), fid, "the fact belongs in this section (p=%.2f)" % p_target, pt * p_target, sure)
                if sure:
                    gains.setdefault(ref, []).append(fid)

        # Ripples: sections the change makes wrong without a fact naming them.
        for sref, pa in plan.affected.items():
            if pa < self.maybe_threshold:
                continue
            ref = sref.split("#")[0]
            facts = [pl.fact.id for pl in plan.placements if pl.touches[ref] >= self.maybe_threshold]
            existing = acts.get((UPDATE_SECTION, sref))
            if existing:
                existing.why += "; its text is affected (p=%.2f)" % pa
                existing.sure = existing.sure or pa >= self.affected_threshold
                continue
            add(UPDATE_SECTION, sref, None, "its text is out of date after the change (p=%.2f)" % pa, pa, pa >= self.affected_threshold).facts = facts
            if pa >= self.affected_threshold:
                gains.setdefault(ref, [])

        # New files: facts that no file in their domain accepts. In a grouped domain the file
        # goes in the group that accepts the fact most, or in a new group when none does.
        for domain, facts in new_files.items():
            nearest = self._nearest(plan, facts, domain)
            ref = "%s/(new topic).md" % domain
            why = "no %s file accepts %s" % (domain, "these facts" if len(facts) > 1 else "this fact")
            home = plan.homes.get(domain)
            if home:
                ranked = sorted(home.items(), key=lambda kv: -kv[1])
                best, pb = ranked[0]
                runner = next(((g, p) for g, p in ranked if g != NEW_GROUP_LABEL), None)
                if best == NEW_GROUP_LABEL:
                    ref = "%s/(new group)/(new topic).md" % domain
                    add(NEW_GROUP, "%s/(new group)/" % domain, None,
                        "no %s group fits (p=%.2f for a new area); a new group needs a README and at least %d files"
                        % (domain, pb, GROUP_FILES[0]))
                    if runner:  # a one-file group fails lint, so name the fallback
                        add(UPDATE_GROUP_README, "%s/%s/" % (domain, runner[0]), None,
                            "or, until the new area has %d files, put the file here (p=%.2f) and name it in the README"
                            % (GROUP_FILES[0], runner[1]), runner[1], sure=False)
                else:
                    g = self.corpus.domains[domain].groups[best]
                    ref = "%s(new topic).md" % g.ref
                    why += "; it goes in %s (p=%.2f)" % (g.ref, pb)
                    add(UPDATE_GROUP_README, g.ref, None, "the README must name the new topic; routing reads only the README to pick the group")
                    if len(g.files) + 1 > GROUP_FILES[1]:
                        add(SPLIT_GROUP, g.ref, None, "the group would have %d files; the format allows %d" % (len(g.files) + 1, GROUP_FILES[1]))
            if nearest:
                why += "; nearest file is %s (p=%.2f), link it with Related:" % nearest
            for f in facts:
                add(NEW_FILE, ref, f.id, why)
            # Facts with only a weak existing home in this domain may belong in the new file instead.
            weak = []
            for pl in plan.placements:
                if pl.fact in facts or (pl.fact.expected_domain or pl.domain) != domain:
                    continue
                home = max(((r, pt) for r, pt in pl.touches.items() if r.startswith(domain + "/")), key=lambda kv: kv[1], default=None)
                if home and home[1] < self.strong_home:
                    weak.append("%s (best existing home %s, p=%.2f)" % (pl.fact.id, home[0], home[1]))
            if weak:
                acts[(NEW_FILE, ref)].why += "; it may also hold " + ", ".join(weak)
            if nearest:
                add(ADD_RELATED, nearest[0], None, "point at the new %s file" % domain)

        self._index_actions(change, plan, gains, acts, add)
        return list(acts.values())

    def _index_actions(self, change: Change, plan: Plan, gains: Dict[str, List[str]], acts, add) -> None:
        """What keeps the new text findable: summaries, index size, links, twins."""
        entities = change.entities
        for ref, fids in gains.items():
            doc = self.corpus.get(ref)
            text = (doc.title + " " + doc.summary_text()).lower()
            missing = [e for e in entities if e.lower() not in text and _mentions(plan, fids, e)]
            if missing and (ADD_SECTION, ref) in acts:
                add(UPDATE_SUMMARY, ref, None, "the summary never names %s; routing reads only the summary to pick this file"
                    % " or ".join(repr(e) for e in missing))
            added = sum(1 for (k, r) in acts if k == ADD_SECTION and r == ref and acts[(k, r)].sure)
            if added and len(doc.sections) + added > INDEX_ENTRIES[1]:
                add(SPLIT_FILE, ref, None, "index would have %d entries; the format allows %d"
                    % (len(doc.sections) + added, INDEX_ENTRIES[1]))

        # Files that take the same fact should know about each other.
        unlinked: Dict[str, Dict[str, Tuple[float, List[str]]]] = {}
        for pl in plan.placements:
            refs = [r for r, pt in pl.ranked_files() if pt >= self.touch_threshold]
            for a in refs:
                for b in refs:
                    if a < b and b not in self.corpus.get(a).related and a not in self.corpus.get(b).related:
                        p, fids = unlinked.setdefault(a, {}).get(b, (0.0, []))
                        unlinked[a][b] = (max(p, min(pl.touches[a], pl.touches[b])), fids + [pl.fact.id])
        for a, others in unlinked.items():
            for b, (p, fids) in others.items():
                add(ADD_RELATED, "%s%s%s" % (a, LINK, b), None, "both take %s; neither links the other with Related:"
                    % ", ".join(fids), p, sure=False).facts = fids

        # A product rule with nothing on the engineering side (or the reverse) is worth a question.
        for ref in list(gains):
            doc = self.corpus.get(ref)
            twin = self._twin(doc)
            gained = acts.get((ADD_SECTION, ref))
            if twin is not None and twin.ref not in gains and gained is not None and gained.sure:
                add(CHECK_TWIN, twin.ref, None, "%s gains a section but its twin gets nothing; is there a %s side to it?"
                    % (ref, twin.domain), sure=False)

    # ------------------------------------------------------------------ helpers
    def _nearest(self, plan: Plan, facts: List[Fact], domain: str) -> Optional[Tuple[str, float]]:
        best: Optional[Tuple[str, float]] = None
        ids = {f.id for f in facts}
        for pl in plan.placements:
            if pl.fact.id not in ids:
                continue
            for ref, pt in pl.touches.items():
                if ref.startswith(domain + "/") and (best is None or pt > best[1]):
                    best = (ref, pt)
        return best

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
    def _state(fact: Fact, change: Change) -> Dict[str, Any]:
        state: Dict[str, Any] = {"fact": fact.text}
        if fact.shape:
            state["shape"] = fact.shape
        if change.summary:
            state["part_of_change"] = change.summary
        return state

    @staticmethod
    def _charge(plan: Plan, result: Result) -> None:
        plan.requests += 1
        plan.input_tokens += result.usage.input_tokens
        plan.model = result.model


def _mentions(plan: Plan, fact_ids: List[str], entity: str) -> bool:
    """An entity matters to a file if a fact landing there names it (or no fact landed, only ripples)."""
    if not fact_ids:
        return False
    texts = [f.text.lower() for f in plan.change.facts if f.id in fact_ids]
    return any(entity.lower() in t for t in texts)
