"""An offline stand-in for the Jev API so the router can be exercised without a key.

    jev = mock_client(corpus)            # a real `jev.Jev` over an httpx.MockTransport
    Router(corpus, jev).route("...")

It answers every Choice / Score by lexical overlap between the state and each option's
description (IDF-weighted, softmaxed), and every Noul by overlap with `true` vs `false`
criteria. It is NOT calibrated and it knows nothing a keyword does not say; it exists
to prove the plumbing and the docs shape, not the routing quality. Two router Nouls get
rules of their own because keywords cannot express them:

    answerable    -> share of the question's content words that appear anywhere in the corpus
    spans_domains -> high only when the question has both a "why" word and a "how" word
    relevant      -> share of the question's content words found in the section text
    touches_N     -> share of the fact's content words found in the file's card (placement)
    group_touches_N -> the same, over the group's README and file summaries
    affected      -> share of the change's content words found in the section text (placement)
"""
from __future__ import annotations

import json
import math
import re
from itertools import count
from typing import Any, Dict, Iterable, List, Optional, Set

import httpx
from jev import Jev

from .corpus import Corpus

MODEL = "jev-mock-0"
TEMPERATURE = 1.4  # sharpness of the softmax over overlap scores

STOPWORDS = set(
    "a an the and or of to in on at for with by from as is are was were be been do does did "
    "we our us you your it its this that these those there here have has had can could should "
    "would will what when who whom whose about into over under than then so if not no yes any "
    "all some one two i me my they them their get got".split()
)
WHY_WORDS = {"why", "reason", "rule", "policy", "promise", "decided", "allowed", "rationale", "purpose"}
HOW_WORDS = {"how", "enforced", "enforce", "implemented", "implement", "mechanism", "code", "table",
             "endpoint", "api", "job", "cron", "service", "query", "schema", "run", "deploy", "where"}

_WORD = re.compile(r"[a-z0-9][a-z0-9'+-]*")


def stem(token: str) -> str:
    for suffix in ("ies", "ing", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            token = token[: -len(suffix)] + ("y" if suffix == "ies" else "")
            break
    return token[:6]


def content_tokens(text: Any) -> Set[str]:
    if not isinstance(text, str):
        text = json.dumps(text)
    return {stem(t) for t in _WORD.findall(text.lower()) if t not in STOPWORDS}


class MockJev:
    """The request handler. Stateless apart from a request counter."""

    def __init__(self, vocabulary: Optional[Iterable[str]] = None):
        self.vocabulary = {stem(v) for v in vocabulary} if vocabulary else set()
        self._ids = count(1)
        self.requests: List[Dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"models": [{"name": MODEL, "description": "lexical mock", "release_date": "2026-01-01"}]})
        body = json.loads(request.content)
        self.requests.append(body)
        state = body["state"]
        answers = {name: self.answer(name, q, state) for name, q in body["questions"].items()}
        payload = {"model": MODEL, "answers": answers, "usage": {"input_tokens": len(request.content) // 4, "output_tokens": 0}}
        return httpx.Response(200, json=payload, headers={"x-typesafe-request-id": "mock-%d" % next(self._ids)})

    def answer(self, name: str, q: Dict[str, Any], state: Any) -> Dict[str, Any]:
        kind = q["type"]
        if kind == "choice":
            probs = self._distribution(state, q["criteria"])
            top = max(probs, key=probs.get)
            return {"type": "choice", "choice": top, "confidence": _confidence(probs), "probabilities": probs}
        if kind == "score":
            levels = {str(i): desc for i, desc in enumerate(q["criteria"])}
            probs = self._distribution(state, levels)
            expected = sum(int(k) * p for k, p in probs.items())
            return {"type": "score", "score": expected, "confidence": _confidence(probs), "legend": levels, "probabilities": probs}
        return {"type": "noul", "noul": self._noul(name, q, state)}

    def _distribution(self, state: Any, options: Dict[str, Any]) -> Dict[str, float]:
        qtok = content_tokens(state)
        opt_tokens = {label: content_tokens(label.replace("-", " ")) | content_tokens(desc) for label, desc in options.items()}
        n = len(options)
        df: Dict[str, int] = {}
        for toks in opt_tokens.values():
            for t in toks:
                df[t] = df.get(t, 0) + 1
        scores = {label: sum(math.log((n + 1) / df[t]) for t in qtok & toks) for label, toks in opt_tokens.items()}
        if not any(scores.values()):
            return {label: round(1.0 / n, 4) for label in options}
        m = max(scores.values())
        weights = {label: math.exp(TEMPERATURE * (s - m)) for label, s in scores.items()}
        z = sum(weights.values())
        return {label: round(w / z, 4) for label, w in weights.items()}

    def _noul(self, name: str, q: Dict[str, Any], state: Any) -> float:
        qtok = content_tokens(state)
        raw = {t.lower() for t in _WORD.findall(state.lower())} if isinstance(state, str) else set()
        if name == "answerable" and self.vocabulary:
            if not qtok:
                return 0.1
            covered = len(qtok & self.vocabulary) / len(qtok)
            return round(min(0.98, 0.15 + 0.85 * covered), 4)
        if name == "relevant" and isinstance(state, dict):
            asked = content_tokens(state.get("question", ""))
            text = content_tokens(state.get("documentation_section", {}))
            if not asked:
                return 0.5
            return round(min(0.97, max(0.03, 1.6 * len(asked & text) / len(asked) - 0.1)), 4)
        if name.startswith(("touches_", "group_touches_")) and isinstance(state, dict):
            fact = content_tokens(state.get("fact", ""))
            ins = q.get("instructions") or {}
            card = content_tokens(ins.get("file") or ins.get("group") or {})
            if not fact:
                return 0.1
            return round(min(0.95, max(0.03, 2.2 * len(fact & card) / len(fact) - 0.1)), 4)
        if name == "affected" and isinstance(state, dict):
            change = content_tokens(state.get("change", ""))
            text = content_tokens(state.get("documentation_section", {}))
            if not change:
                return 0.1
            return round(min(0.95, max(0.03, 2.0 * len(change & text) / len(change) - 0.2)), 4)
        if name == "spans_domains":
            return 0.85 if (raw & WHY_WORDS and raw & HOW_WORDS) else 0.15
        crit = q.get("criteria") or {}
        st = len(qtok & content_tokens(crit.get("true", "")))
        sf = len(qtok & content_tokens(crit.get("false", "")))
        return round(0.5 + 0.4 * math.tanh(0.8 * (st - sf)), 4)


def _confidence(probs: Dict[str, float]) -> float:
    ranked = sorted(probs.values(), reverse=True)
    return round(ranked[0] - (ranked[1] if len(ranked) > 1 else 0.0), 4)


def mock_client(corpus: Optional[Corpus] = None, handler: Optional[MockJev] = None) -> Jev:
    """A real SDK client whose HTTP layer is the mock; `corpus` seeds the answerable check."""
    handler = handler or MockJev(corpus.vocabulary() if corpus else None)
    return Jev("mock-key", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
