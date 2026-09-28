# jev-docs

Route a reader's question to the one documentation section that answers it, using
the [Jev System One API](../jev-sdk) instead of embeddings or generated text.

```
"why do we have 20 copies of Book A in inventory?"            (a live route, jev-1.13.0)
   -> domain:  product                      (p=0.99)
   -> group:   store                        (p=1.00)   same request as the domain
   -> file:    product/store/inventory.md   (p=1.00)
   -> section: #book-a-minimum-stock        (p=1.00, relevance 0.97)
```

Three hops, each a calibrated `Choice` over things the docs author wrote for exactly
this purpose: the domain descriptions, the file summaries, the index blurbs. The router
never reads section bodies, so the docs format is what makes routing work.

A big domain is split into group folders (`product/store/inventory.md`), each with a short
README. The group Choice rides in the same request as the domain Choice, and the file Choice
then only runs inside the top two groups, so a domain of 200 files costs the same requests
as one of 8.
Read **[DOCS_FORMAT.md](DOCS_FORMAT.md)** first. **[PLAN.md](PLAN.md)** has the
implementation plan and what is left.

## Layout

```
DOCS_FORMAT.md        how to write docs so any software fits (the contract), flat or grouped
PLAN.md               implementation plan and status
src/jevdocs/          the engine
  corpus.py           parse a docs tree, lint it
  questions.py        build the Jev questions for each hop
  router.py           Router: question -> Route (domain, file, section, twin, confidences)
  placement.py        Placer: a change (split into facts) -> every file and section it touches
  check.py            snapshot, then after an edit: findable, overlap, moved routes, links
  mock.py             offline stand-in for the Jev API (lexical, uncalibrated)
  cli.py              jevdocs route | lint | tree | eval | place | snapshot | check
poc/                  detached proof of concept: bookstore docs (grouped), eval set, standalone poc.py
  changes/            example changes for `jevdocs place` (book-c.json)
tests/                offline tests
.claude/skills/docs-change/   the loop an agent follows to document a change
```

## Run the POC web page

```bash
uv sync
uv run poc/server.py                      # open http://127.0.0.1:8765
JEV_API_KEY=... uv run poc/server.py      # same page against the real Jev API
```

Ask a question and the page shows the answer section, then every step: what Jev was asked,
every option it chose between with the text it read, the probability of each, the
confidence, and the path probability. The file step keeps the top two files; the section
step asks inside both in one request; a final relevance step reads each candidate
section's text and asks Jev whether it is relevant. The answer is the most probable
candidate that passes, or, when none passes, the most probable one marked unverified. The dropdown opens any doc, with the routed section
highlighted. `?q=...` in the URL asks straight away, so a route can be shared as a link.
The POC uses only the jev SDK; it shares the docs with the engine but none of its code.

Each answer also shows the time it took and its price in rupees. Time is shown three ways: in
the browser, on the server, and inside each Jev call. Price uses TypeSafe's listed rate of
$0.042 per million input tokens, with output tokens free, converted at ₹95.9 per dollar
(the 26 September 2026 rate). Override either rate without editing code:

```bash
JEV_USD_PER_MTOK=0.042 JEV_INR_PER_USD=96.2 uv run poc/server.py
```

Under the mock, token counts are estimates and times are near zero.

## Run the engine

```bash
uv run jevdocs lint --docs poc/docs
uv run jevdocs tree --docs poc/docs
uv run jevdocs route "why do we have 20 copies of Book A?" --docs poc/docs --show
uv run jevdocs eval poc/questions.jsonl --docs poc/docs
uv run poc/poc.py --mock                  # the standalone POC (jev SDK only, no engine code)

JEV_API_KEY=... uv run jevdocs eval poc/questions.jsonl --docs poc/docs --live
```

Without `JEV_API_KEY` the commands use the mock, which answers by keyword overlap
through the real SDK client and `httpx.MockTransport`. It proves the plumbing, not the
routing quality.

## Document a change

Routing picks the one section that answers a question. Documenting a change is the other
direction: one change fans out into many files, some sections new, some existing ones made
wrong. The engine plans the edit and checks it; the writing is done by a person or an agent
(the `docs-change` skill in `.claude/skills/` teaches Claude Code the loop).

```bash
uv run jevdocs snapshot --docs poc/docs --eval poc/questions.jsonl        # 1. before editing
uv run jevdocs place poc/changes/book-c.json --docs poc/docs -v           # 2. where it goes
#                                                                           3. write the edits
uv run jevdocs check --docs poc/docs --eval poc/questions.jsonl \
    --change poc/changes/book-c.json                                        # 4. findable? broke anything?
uv run jevdocs check ... --record --save                                    # 5. on PASS: facts join the eval set
```

A change file is the change split into facts, each tagged with its section shape
(DOCS_FORMAT.md §5), plus the entities a reader will type:

```json
{"title": "Book C delivery format",
 "entities": ["Book C", "Kafka"],
 "facts": [{"id": "rule", "shape": "rule", "text": "Book C is delivered as a printed hard copy to elderly customers ..."},
           {"id": "contract", "shape": "contract", "text": "The Kafka topic book-c-delivery carries one message per ..."}]}
```

`place` asks, per fact, which files need an edit (a yes/no per file, so a fact can land in
several), then where in each: an existing section or a new one. It then reads every section of
every touched file against the change to find the ripples no fact names (an overview table, a
roles list), and adds what keeps the new text findable: index entries, summary edits when a
file gains a section about an entity its summary never names, `Related:` links, twins, and a
new file when no file in the fact's domain accepts it.

`check` compares the docs with the snapshot. Every added or changed section is asked its own
blurb as a question, and every fact of the change is routed: each must reach an edited section
and pass the relevance check on its text. A miss names the hop that lost it (domain, file,
section or relevance) and what to fix. It also flags content that now lives in two places,
eval questions that routed correctly before the edit and no longer do, and new `Related:`
links that do not go both ways.

On the Book C change, `place` took 31 requests (2 s); the full `check` with 28 eval questions
takes about 250 requests (10 s, under ₹1 at the listed rate). The three problems it caught on
the first draft, and their fixes, are in the commit that added `poc/docs/*/store/delivery.md`.

## Use the engine

```python
from jev import Jev
from jevdocs import load, lint, Router

corpus = load("docs")
assert not lint(corpus)
router = Router(corpus, Jev())
route = router.route("how is the Book A restock enforced?")
route.ref          # "engineering/store/inventory.md#book-a-restock-job"   (where it is)
route.key          # "engineering/inventory.md#book-a-restock-job"         (what it is: survives a move)
route.text         # the section body
route.status       # "ok" | "ambiguous" | "not_documented"
route.also         # the product twin's route when the question spans why and how
route.explain()
```
