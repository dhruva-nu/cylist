"""Check an edit to the docs: is the new text findable, and did any old route move?

    snap = snapshot(corpus, router, cases)        # BEFORE editing; saved with save_snapshot()
    ... edit the docs ...
    report = check(load("docs"), router, load_snapshot("docs"), cases, change)
    report.ok, report.explain()

What it does, with the router exactly as readers will meet it:

  diff        fingerprints every file summary and section against the snapshot:
              which sections were added, changed or removed (no Jev)
  findable    routes a probe per added or changed section (its own blurb, asked as a
              question) and every fact of the change: each must land on a section this
              edit added or changed. A miss says which hop lost it: the domain, the file
              (fix the summary) or the section (fix the blurb)
  overlap     a probe whose route also passed the relevance check on some OTHER section
              means the same content now lives in two places
  regressions routes every eval question and compares with the snapshot's routes: a
              question that was right and is now wrong is a regression
  links       new files need `Related:` both ways, and a product file needs an
              engineering side (or says why not); lint runs too
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .corpus import Corpus, DocFile, Problem, lint
from .placement import Change
from .router import NOT_DOCUMENTED, Route, Router

SNAPSHOT = Path(".jevdocs") / "snapshot.json"  # inside the docs root; `load` skips dot-directories


# ----------------------------------------------------------------------------- fingerprints
def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def fingerprint(corpus: Corpus) -> Dict[str, Any]:
    """Per file, keyed by identity (`domain/name.md`) so that moving a file between groups is a move."""
    out = {}
    for doc in corpus.files():
        out[doc.key] = {
            "path": doc.ref,
            "title": doc.title,
            "summary": _h(doc.summary_text()),
            "related": doc.related,
            "sections": {s.anchor: _h("%s\n%s\n%s" % (s.title, s.blurb, s.body)) for s in doc.sections},
            "blurbs": {s.anchor: _h(s.blurb) for s in doc.sections},
        }
    return out


def group_fingerprint(corpus: Corpus) -> Dict[str, str]:
    return {g.ref: _h(g.description) for g in corpus.groups()}


@dataclass
class Diff:
    added_files: List[str] = field(default_factory=list)
    removed_files: List[str] = field(default_factory=list)
    added: List[str] = field(default_factory=list)  # "file#anchor"
    changed: List[str] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)
    summaries: List[str] = field(default_factory=list)  # files whose summary changed
    moved: List[Tuple[str, str]] = field(default_factory=list)  # (old path, new path): same file, other group
    groups_added: List[str] = field(default_factory=list)
    groups_changed: List[str] = field(default_factory=list)  # README edited: the group hop reads it

    @property
    def touched(self) -> List[str]:
        """Sections a probe may land on and count as found."""
        return self.added + self.changed

    @property
    def empty(self) -> bool:
        return not (self.added_files or self.removed_files or self.added or self.changed or self.removed
                    or self.summaries or self.moved or self.groups_added or self.groups_changed)

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


def diff(before: Dict[str, Any], corpus: Corpus, groups_before: Optional[Dict[str, str]] = None) -> Diff:
    """Refs in the result are where things are now; a removed file is named by where it was."""
    now = fingerprint(corpus)
    d = Diff()
    d.added_files = sorted(now[k]["path"] for k in set(now) - set(before))
    d.removed_files = sorted(before[k].get("path", k) for k in set(before) - set(now))
    for key, f in now.items():
        old = before.get(key)
        ref = f["path"]
        if old is None:
            d.added += ["%s#%s" % (ref, a) for a in f["sections"]]
            continue
        if old.get("path", key) != ref:
            d.moved.append((old.get("path", key), ref))
        if old["summary"] != f["summary"]:
            d.summaries.append(ref)
        for a, h in f["sections"].items():
            if a not in old["sections"]:
                d.added.append("%s#%s" % (ref, a))
            elif old["sections"][a] != h:
                d.changed.append("%s#%s" % (ref, a))
        d.removed += ["%s#%s" % (ref, a) for a in old["sections"] if a not in f["sections"]]
    if groups_before is not None:
        g_now = group_fingerprint(corpus)
        d.groups_added = sorted(set(g_now) - set(groups_before))
        d.groups_changed = sorted(g for g in g_now if g in groups_before and g_now[g] != groups_before[g])
    return d


# ----------------------------------------------------------------------------- snapshot
def route_all(router: Router, questions: List[str], max_workers: int = 8) -> List[Route]:
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return list(pool.map(router.route, questions))


def _route_record(route: Route) -> Dict[str, Any]:
    """Compared by file identity, so a file moving between groups is not a moved route; `path` is for people."""
    return {"ref": route.key, "also": route.also.key if route.also else None, "status": route.status,
            "path": route.ref, "also_path": route.also.ref if route.also else None}


def snapshot(corpus: Corpus, router: Optional[Router], cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The docs' fingerprints, plus where every eval question routes today (skipped without a router)."""
    routes = {}
    if router is not None and cases:
        for case, route in zip(cases, route_all(router, [c["question"] for c in cases])):
            routes[case["question"]] = {**_route_record(route), "correct": expected_match(case, route)}
    return {"version": 1, "created": _dt.datetime.now().isoformat(timespec="seconds"),
            "docs": fingerprint(corpus), "groups": group_fingerprint(corpus), "routes": routes}


def snapshot_path(root: Path) -> Path:
    return Path(root) / SNAPSHOT


def save_snapshot(root: Path, snap: Dict[str, Any]) -> Path:
    path = snapshot_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap, indent=1) + "\n")
    return path


def load_snapshot(root: Path) -> Optional[Dict[str, Any]]:
    path = snapshot_path(root)
    return json.loads(path.read_text()) if path.exists() else None


# ----------------------------------------------------------------------------- eval expectations
def expected_match(case: Dict[str, Any], route: Route) -> Optional[bool]:
    """True/False against the case's expectations; None when the case states none."""
    keys = [k for k in ("status", "domain", "group", "file", "section") if k in case]
    if not keys:
        return None
    return all(_matches(k, case[k], route) for k in keys)


def _matches(key: str, expected, route: Route) -> bool:
    """`expected` may be a string or a list of acceptable strings. The `also` route counts."""
    accepted = expected if isinstance(expected, list) else [expected]
    routes = [route] + ([route.also] if route.also else [])
    for r in routes:
        if key == "status" and r is route and r.status in accepted:
            return True
        if key == "domain" and r.file and r.file.domain in accepted:
            return True
        if key == "group" and r.file and r.file.group in accepted:
            return True
        if key == "file" and r.file and (r.file.name in accepted or r.file.ref in accepted or r.file.key in accepted):
            return True
        if key == "section" and r.section_obj and r.section_obj.anchor in accepted:
            return True
    return False


# ----------------------------------------------------------------------------- the report
@dataclass
class Probe:
    kind: str  # "section" (its own blurb) or "fact" (from the change)
    question: str
    expected: List[str]  # refs that count as found
    target: str = ""  # the section a section probe belongs to; the fact id for a fact probe
    route: Optional[Route] = None
    found: bool = False
    lost_at: str = ""  # answerable | domain | file | section | relevance
    hint: str = ""
    overlaps: List[Tuple[str, float]] = field(default_factory=list)  # other sections that passed relevance
    old_overlaps: List[Tuple[str, float]] = field(default_factory=list)  # the same, for a changed section: may predate the edit

    @property
    def relevance(self) -> Optional[float]:
        """The relevance of the route that counted: the expected one when it was hit, else the primary."""
        if self.route is None:
            return None
        for r in (self.route, self.route.also):
            if r is not None and r.ref and r.ref in self.expected and r.relevance is not None:
                return r.relevance
        return self.route.relevance

    def landed(self) -> List[str]:
        if self.route is None:
            return []
        return [r for r in (self.route.ref, self.route.also.ref if self.route.also else None) if r]

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "target": self.target, "question": self.question, "found": self.found,
                "landed": self.landed(), "lost_at": self.lost_at, "hint": self.hint,
                "relevance": None if self.relevance is None else round(self.relevance, 3),
                "overlaps": [{"ref": r, "relevance": round(p, 3)} for r, p in self.overlaps],
                "old_overlaps": [{"ref": r, "relevance": round(p, 3)} for r, p in self.old_overlaps]}


@dataclass
class Moved:
    question: str
    before: Optional[str]
    after: Optional[str]
    was_correct: Optional[bool]
    now_correct: Optional[bool]
    unrelated: bool = False  # nothing the edit changed can move it (see check()): likely run-to-run variance

    @property
    def regression(self) -> bool:
        return self.was_correct is True and self.now_correct is False and not self.unrelated

    def to_dict(self) -> Dict[str, Any]:
        return {**self.__dict__, "regression": self.regression}


@dataclass
class Report:
    diff: Optional[Diff]
    problems: List[Problem] = field(default_factory=list)  # lint
    link_problems: List[str] = field(default_factory=list)
    probes: List[Probe] = field(default_factory=list)
    moved: List[Moved] = field(default_factory=list)
    wrong: List[Tuple[str, Optional[str]]] = field(default_factory=list)  # eval questions wrong now (no snapshot)
    routes: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # for saving as the next snapshot
    requests: int = 0
    input_tokens: int = 0
    notes: List[str] = field(default_factory=list)

    @property
    def regressions(self) -> List[Moved]:
        return [m for m in self.moved if m.regression]

    @property
    def misses(self) -> List[Probe]:
        return [p for p in self.probes if not p.found]

    @property
    def overlapping(self) -> List[Probe]:
        return [p for p in self.probes if p.overlaps]

    @property
    def ok(self) -> bool:
        return not (self.problems or self.link_problems or self.misses or self.regressions or self.overlapping)

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "diff": self.diff.to_dict() if self.diff else None,
                "lint": [str(p) for p in self.problems], "links": self.link_problems,
                "probes": [p.to_dict() for p in self.probes],
                "moved": [m.to_dict() for m in self.moved],
                "regressions": [m.question for m in self.regressions],
                "wrong_now": [{"question": q, "route": r} for q, r in self.wrong],
                "notes": self.notes, "requests": self.requests, "input_tokens": self.input_tokens}

    def explain(self) -> str:
        L: List[str] = []
        d = self.diff
        if d is not None:
            L.append("changed:  %d file(s) added, %d section(s) added, %d changed, %d removed, %d summary edit(s)"
                     % (len(d.added_files), len(d.added), len(d.changed), len(d.removed), len(d.summaries)))
            if d.moved or d.groups_added or d.groups_changed:
                L.append("          %d file(s) moved between groups, %d group(s) added, %d group README(s) edited"
                         % (len(d.moved), len(d.groups_added), len(d.groups_changed)))
            for label, refs in (("new group", d.groups_added), ("README", d.groups_changed), ("new file", d.added_files),
                                ("added", d.added), ("changed", d.changed), ("removed", d.removed), ("summary", d.summaries),
                                ("deleted", d.removed_files), ("moved", ["%s -> %s" % m for m in d.moved])):
                for r in refs:
                    L.append("  %-9s %s" % (label, r))
        for n in self.notes:
            L.append("note:     %s" % n)

        L.append("")
        L.append("lint:     %s" % ("clean" if not self.problems else "%d problem(s)" % len(self.problems)))
        L += ["  " + str(p) for p in self.problems]
        L.append("links:    %s" % ("ok" if not self.link_problems else "%d problem(s)" % len(self.link_problems)))
        L += ["  " + p for p in self.link_problems]

        if self.probes:
            found = sum(p.found for p in self.probes)
            L.append("findable: %d/%d probe(s) land on a section this edit added or changed" % (found, len(self.probes)))
            for p in self.probes:
                mark = "ok  " if p.found else "MISS"
                rel = "" if p.relevance is None else "  relevant=%.2f" % p.relevance
                label = p.target if p.kind == "fact" else "#" + p.target.split("#")[-1]
                L.append("  %s %-7s %-34s -> %s%s" % (mark, p.kind, label[:34], " + ".join(p.landed()) or p.route.status, rel))
                if not p.found:
                    L.append("       lost at the %s hop: %s" % (p.lost_at, p.hint))
                for ref, rp in p.overlaps:
                    L.append("       OVERLAP: %s also passed the relevance check (%.2f); say it in one place and point there from the other" % (ref, rp))
                for ref, rp in p.old_overlaps:
                    L.append("       note: %s also passed the relevance check (%.2f); this section existed before, so it may predate the edit" % (ref, rp))

        if self.moved or self.routes:
            L.append("routes:   %d eval question(s), %d moved, %d regression(s)" % (len(self.routes), len(self.moved), len(self.regressions)))
            for m in self.moved:
                tag = ("REGRESSION" if m.regression else "fixed" if m.was_correct is False and m.now_correct
                       else "moved (was wrong, still wrong)" if m.was_correct is False else "moved")
                if m.unrelated:
                    tag += " (likely Jev variance: nothing this edit changed can move this route)"
                L.append("  %-10s %s\n             %s -> %s" % (tag, m.question, m.before, m.after))
        for q, r in self.wrong:
            L.append("  wrong      %-60s -> %s" % (q[:60], r))

        L.append("")
        L.append("result:   %s" % ("PASS" if self.ok else "FAIL: " + ", ".join(
            s for s, n in (("lint", self.problems), ("links", self.link_problems), ("not findable", self.misses),
                           ("overlap", self.overlapping), ("regressions", self.regressions)) if n)))
        L.append("cost:     %d request(s), %d input tokens" % (self.requests, self.input_tokens))
        return "\n".join(L)


# ----------------------------------------------------------------------------- check
def check(
    corpus: Corpus,
    router: Optional[Router],
    snap: Optional[Dict[str, Any]],
    cases: List[Dict[str, Any]],
    change: Optional[Change] = None,
) -> Report:
    # A snapshot from before groups existed has none: every group is then new.
    report = Report(diff(snap["docs"], corpus, snap.get("groups", {})) if snap else None)
    report.problems = lint(corpus)
    report.link_problems = links(corpus, report.diff, snap["docs"] if snap else None)
    if snap is None:
        report.notes.append("no snapshot: run `jevdocs snapshot` before editing to see what changed and what moved")
    elif report.diff.empty:
        report.notes.append("the docs are identical to the snapshot")
    if router is None:
        return report

    probes = _probes(corpus, report.diff, change)
    questions = [p.question for p in probes] + [c["question"] for c in cases]
    routes = route_all(router, questions)
    for r in routes:
        report.requests += r.requests
        report.input_tokens += r.input_tokens

    added = report.diff.added if report.diff else []
    for p, r in zip(probes, routes):
        p.route = r
        _judge(p, router, corpus, added)
    report.probes = probes

    old = (snap or {}).get("routes") or {}
    d = report.diff
    # File identities whose text this edit changed, and those it moved or regrouped. A move or a
    # README can change which file wins; only a change to a file's own text can change which of
    # its sections wins, because the section Choice reads nothing else.
    key = lambda ref: corpus.get(ref).key if corpus.get(ref) else ref
    content = {key(r) for r in set(d.added_files) | set(d.summaries) | {x.split("#")[0] for x in d.touched + d.removed}} if d else set()
    edited = set(content)
    if d:
        edited |= {key(m[1]) for m in d.moved}
        for g in d.groups_changed + d.groups_added:
            edited |= {doc.key for doc in corpus.files() if doc.ref.startswith(g)}
    for case, r in zip(cases, routes[len(probes):]):
        now = _route_record(r)
        correct = expected_match(case, r)
        report.routes[case["question"]] = {**now, "correct": correct}
        before = old.get(case["question"])
        if before is not None:
            if (before["ref"], before["also"], before["status"]) != (now["ref"], now["also"], now["status"]):
                ends = {x.split("#")[0] for x in (before["ref"], before["also"], now["ref"], now["also"]) if x}
                same_file = len(ends) == 1 and before["status"] == now["status"]
                unrelated = report.diff is not None and (not ends & edited or (same_file and not ends & content))
                report.moved.append(Moved(case["question"], _show(before), _show(now), before.get("correct"), correct, unrelated))
        elif correct is False:
            report.wrong.append((case["question"], _show(now)))
    if old and not report.moved:
        report.notes.append("every eval question routes exactly as it did at the snapshot")
    return report


def _show(rec: Dict[str, Any]) -> str:
    out = rec.get("path") or rec["ref"] or rec["status"]
    also = rec.get("also_path") or rec.get("also")
    return out + (" + " + also if also else "")


def _probes(corpus: Corpus, d: Optional[Diff], change: Optional[Change]) -> List[Probe]:
    """A section probe per added or changed section whose blurb is new, plus a probe per fact."""
    probes: List[Probe] = []
    targets = d.touched if d else []
    for ref in targets:
        file_ref, anchor = ref.split("#")
        doc = corpus.get(file_ref)
        s = doc.section(anchor) if doc else None
        if s is None or not s.blurb:
            continue
        # The blurb is the author's own statement of what the section answers.
        probes.append(Probe("section", s.blurb[0].upper() + s.blurb[1:] + "?", [ref], ref))
    if change is not None:
        for f in change.facts:
            probes.append(Probe("fact", f.text, list(targets), f.id))
    return probes


def _judge(p: Probe, router: Router, corpus: Corpus, added: List[str]) -> None:
    r = p.route
    landed = p.landed()
    bar = router.relevance_threshold
    # Found means: routed to an expected section AND that section's text passed the relevance check.
    hits = [x for x in (r, r.also) if x is not None and x.ref and (x.ref in p.expected if p.expected else True)]
    passed = [x for x in hits if x.relevance is None or x.relevance >= bar]
    p.found = bool(passed)
    # The same content in two places: another checked candidate passed the relevance bar too.
    # Only an ADDED section's overlap is this edit's doing; a changed one's may predate it.
    chosen = set(landed)
    for c in r.checked:
        if c.ref not in chosen and c.ref not in p.expected and c.relevance is not None and c.relevance >= bar:
            if p.found:
                (p.overlaps if p.kind == "fact" or p.target in added else p.old_overlaps).append((c.ref, c.relevance))
    if p.found:
        return
    if hits:
        x = hits[0]
        p.lost_at = "relevance"
        if p.kind == "section":
            p.hint = ("routed to it, but reading its text Jev scored relevance %.2f < %.2f: the body does not "
                      "say what the blurb promises. Make them agree" % (x.relevance, bar))
        else:
            p.hint = ("landed on %s, which does not state this fact (relevance %.2f < %.2f): the section that "
                      "does lost the section hop, so its blurb must name what the fact says" % (x.ref, x.relevance, bar))
        return
    if r.status == NOT_DOCUMENTED:
        p.lost_at, p.hint = "answerable", "Jev judged this not a docs question (P=%.2f); reword the blurb" % r.answerable
        return
    want_files = {e.split("#")[0] for e in p.expected}
    if not want_files:
        p.lost_at, p.hint = "-", "this edit added or changed no section, so a fact has nowhere to land"
        return
    want_domains = {f.split("/")[0] for f in want_files}
    if r.file is None or r.file.domain not in want_domains and not (r.also and r.also.file and r.also.file.domain in want_domains):
        p.lost_at = "domain"
        p.hint = "routed to %s; the question reads as %s. Reword it in %s words" % (
            r.ref, r.domain.chosen, "/".join(sorted(want_domains)))
        return
    if r.file.ref not in want_files and not (r.also and r.also.file and r.also.file.ref in want_files):
        wanted = sorted(want_files)[0]
        want_doc = corpus.get(wanted)
        if want_doc is not None and want_doc.group and want_doc.domain == r.file.domain and want_doc.group != r.file.group \
                and r.topic is not None and wanted not in r.topic.probabilities:
            p.lost_at = "group"
            rank = dict(r.group.ranked()) if r.group else {}
            p.hint = "the group hop never opened %s%s (it chose %s); its README must name what this section is about" % (
                want_doc.domain + "/" + want_doc.group + "/",
                " (p=%.2f)" % rank[want_doc.group] if want_doc.group in rank else "", r.file.group + "/")
            return
        p.lost_at = "file"
        rank = dict(r.topic.ranked()) if r.topic else {}
        p.hint = "%s won the file hop%s; the summary of %s must name what this section is about" % (
            r.file.ref, " (%s p=%.2f)" % (wanted, rank[wanted]) if wanted in rank else "", wanted)
        return
    p.lost_at = "section"
    p.hint = "the right file, but #%s won; make the blurbs of the two sections say different things" % (
        r.section_obj.anchor if r.section_obj else "?")


# ----------------------------------------------------------------------------- links
def links(corpus: Corpus, d: Optional[Diff], before: Optional[Dict[str, Any]] = None) -> List[str]:
    """For new and changed files: new `Related:` links go both ways, and a new file has a twin or a link across."""
    out: List[str] = []
    if d is None:
        return out
    before = before or {}
    refs = set(d.added_files) | {r.split("#")[0] for r in d.touched}
    for ref in sorted(refs):
        doc = corpus.get(ref)
        if doc is None:
            continue
        old = {o.key for o in map(corpus.get, before.get(doc.key, {}).get("related", [])) if o is not None}
        for rel in doc.related:
            other = corpus.get(rel)
            if other is not None and other.key in old:
                continue  # an existing one-way link is not this edit's problem
            if other is not None and not _links_back(corpus, other, doc):
                out.append("%s has Related: %s, but %s does not link back" % (doc.ref, other.ref, other.ref))
        if ref in d.added_files:
            if not doc.related:
                out.append("%s is new and has no Related: line; link it to the files it depends on" % doc.ref)
            elif not any(corpus.get(r) is not None and corpus.get(r).domain != doc.domain for r in doc.related) \
                    and not any(n != doc.domain and doc.name in dom.files for n, dom in corpus.domains.items()):
                out.append("%s is new and links to nothing in another domain; add its %s twin to Related:"
                           % (doc.ref, "engineering" if doc.domain == "product" else "product"))
    return out


def _links_back(corpus: Corpus, other: DocFile, doc: DocFile) -> bool:
    return any(corpus.get(r) is doc for r in other.related)
