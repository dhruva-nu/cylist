# jev

Python SDK for the **Jev System One API** by TypeSafe (`https://api.typesafe.ai`): ask typed
questions about any content and get back calibrated distributions instead of generated text.

| question | asks                                | answer                                               |
| -------- | ----------------------------------- | ---------------------------------------------------- |
| `Choice` | pick one label out of several       | `choice`, `probabilities` per label, `confidence`    |
| `Score`  | place it on an ordered scale        | expected `score` (e.g. 1.4), per-level probabilities |
| `Noul`   | a yes/no question or statement      | `probability` of yes / true                          |

Every question about one piece of content goes in a single request.

```bash
uv add ../jev-sdk          # or: pip install -e ../jev-sdk
export JEV_API_KEY=...
```

## Declare questions, ask them

```python
from jev import Jev, Choice, Score, Noul, Criteria

class Department(Choice):
    instructions = "Which department should handle this request?"
    billing   = Criteria("invoices, payments, refunds")
    technical = Criteria("bugs, outages, system errors")
    other     = Criteria()                        # label only; the model reads the name

class Urgency(Score):                             # criteria order is level order: 0, 1, 2
    instructions = "How urgent is this request?"
    low      = Criteria("can wait")
    soon     = Criteria("needs attention this week")
    critical = Criteria("needs attention today")

class ChurnRisk(Noul):
    instructions = "Does the customer threaten to cancel or leave?"
    true_means   = "says they will cancel or switch"   # optional
    false_means  = "no such threat"                     # optional

ticket = {"subject": "Charged twice", "body": "Refund one today or I'm cancelling."}

with Jev() as jev:
    result = jev.ask(ticket, Department, Urgency, ChurnRisk)

result[Department].choice          # "billing"
result[Department].probabilities   # {"billing": 0.97, "technical": 0.02, "other": 0.01}
result[Department].confidence      # 0.93, so a low value is a cue for human review
result[Urgency].score              # 1.78: between "soon" and "critical"
result[Urgency].label              # "critical" (the nearest level)
result[Urgency].probabilities      # {"low": 0.0, "soon": 0.21, "critical": 0.79}
result[ChurnRisk].probability      # 0.96
result[ChurnRisk].is_true()        # True (threshold defaults to 0.5)
result.usage.input_tokens, result.model, result.request_id
```

A question is sent under its snake_cased class name (`ChurnRisk` → `"churn_risk"`); set
`name = "..."` to override it. You can look answers up by class or by name. Bad declarations
(a Choice with one option, a Noul with Criteria, missing instructions) raise `TypeError` when
the class is defined, before any request is made. `instructions` and descriptions may be any
JSON value, not only strings. A subclass inherits its base's criteria and can add to them.

`state` may be a string, a dict or a list.

### Questions built at runtime

```python
from jev import choice, score, noul

Tone    = choice("tone", "What is the tone?", {"angry": "hostile", "calm": None})
Lang    = choice("language", "Which language is it in?", ["en", "de", "fr"])
Urgency = score("urgency", "How urgent?", ["can wait", "this week", "today"])  # labels "0".."2"
Spam    = noul("spam", "Is this unsolicited advertising?")

jev.ask(message, Tone, Lang, Urgency, Spam)[Tone].choice
```

### The raw wire format

```python
jev.system_one("buy now!!", {"spam": {"type": "noul", "instructions": "Is this spam?"}})
# -> the response dict unchanged: {"model": ..., "answers": {"spam": {"type": "noul", "noul": 0.98}}, "usage": ...}
```

### Models

```python
jev.models()                         # [Model(name="jev-latest", ...), Model(name="jev-preview", ...)]
Jev(model="jev-preview")             # client default
jev.ask(ticket, Department, model="jev-preview")   # per call
```

`result.model` is the concrete version that answered (for example `jev-1.13.0` when you asked for `jev-latest`).

## Async

```python
import asyncio
from jev import AsyncJev

async def triage(tickets):
    async with AsyncJev() as jev:
        return await asyncio.gather(*(jev.ask(t, Department, Urgency) for t in tickets))
```

## Observability: time and tokens

Observation is off by default. Once you turn it on, every call reports one event, including
failures and retries. The event carries `duration` (wall clock including retries), `latency`
(the final attempt only), `attempts`, `input_tokens` / `output_tokens`, `model` /
`response_model`, `request_id`, `questions` and `error`.

**Per client:** pick the metrics you want printed, one line per call, to stderr:

```python
Jev(observe="time")      # jev systemone jev-1.13.0 3q 200 | 324ms | req_01a0...
Jev(observe="tokens")    # jev systemone jev-1.13.0 3q 200 | tokens in=584 out=118 | req_01a0...
Jev(observe="all")       # jev systemone jev-1.13.0 3q 200 | 324ms | tokens in=584 out=118 | req_01a0...
```

**With no code change:** clients built without `observe=` read `JEV_OBSERVE`:

```bash
JEV_OBSERVE=all python my_app.py         # time | tokens | all | off
```

**Around a block of code:** this totals every call inside the block, from any client and
from any asyncio task started inside it. Blocks nest.

```python
from jev.observability import track

with track() as usage:                   # silent; track("tokens") also prints each call
    for t in tickets:
        jev.ask(t, Department, Urgency)

print(usage.summary())
# jev usage: 3 requests
#   time    total 1008ms  mean 336ms  p50 336ms  p95 347ms  max 348ms
#   tokens  in 1,744  out 354  total 2,098 (581 in / request)

usage.input_tokens, usage.total_time, usage.snapshot().p95_time, usage.by_model()
usage.summary("tokens")                  # only the metrics you care about
```

**Per result:** available whether or not observation is on:

```python
result.elapsed, result.attempts, result.usage.input_tokens
```

**Observers:** an observer is any callable that takes a `RequestEvent`. You can pass one, a
list, or append to `jev.observers` later. A failing observer is logged and never breaks the
call.

```python
from jev.observability import UsageTracker, LogObserver, ConsoleObserver, OpenTelemetryObserver

usage = UsageTracker()                   # running totals; thread-safe; share across clients
jev = Jev(observe=[
    usage,
    LogObserver("all"),                  # stdlib logging on logger "jev" (errors at WARNING)
    OpenTelemetryObserver(),             # spans + GenAI metrics; pip install "jev[otel]"
    lambda e: statsd.timing("jev.ms", e.duration_ms),
])
```

`OpenTelemetryObserver` records a `jev.systemone` / `jev.models` span with
`gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.usage.input_tokens` / `output_tokens`,
`jev.request_id`, `jev.attempts` and error status. It also records the histograms
`gen_ai.client.operation.duration` (s) and `gen_ai.client.token.usage` (split by
`gen_ai.token.type`). It uses the global tracer and meter providers unless you pass your own.

## Errors and retries

The client retries 408, 409, 429, 5xx and network errors up to `max_retries` times (default 2).
It uses exponential backoff with jitter and honours `Retry-After` / `Retry-After-Ms`, capped
at 60s.

```
JevError
├── JevConnectionError            network failure after all retries
│   └── JevTimeoutError
├── JevResponseError              2xx whose body is not the documented shape
└── JevAPIError                   non-2xx; .status_code .body .message .error_type .request_id
    ├── BadRequestError           400
    ├── AuthenticationError       401
    ├── PermissionDeniedError     403
    ├── NotFoundError             404
    ├── UnprocessableEntityError  422, where .errors lists each {loc, msg, type}
    ├── RateLimitError            429
    └── InternalServerError       5xx (529 = overloaded)
```

```python
from jev import UnprocessableEntityError

try:
    jev.system_one(state, raw_questions)
except UnprocessableEntityError as e:
    for err in e.errors:
        print(err["loc"], err["msg"])
```

## Configuration

| argument          | env            | default                   |
| ----------------- | -------------- | ------------------------- |
| `api_key`         | `JEV_API_KEY`  | none (required)           |
| `base_url`        | `JEV_BASE_URL` | `https://api.typesafe.ai` |
| `model`           |                | `jev-latest`              |
| `timeout`         |                | `30.0` s (or an `httpx.Timeout`) |
| `max_retries`     |                | `2`                       |
| `default_headers` |                | none                      |
| `observe`         | `JEV_OBSERVE`  | off: `"time"` / `"tokens"` / `"all"` / an observer / a list |
| `http_client`     |                | a new `httpx.Client` / `httpx.AsyncClient`, for proxies, mounts and tests |

## Development

```bash
uv run pytest                       # offline tests (httpx.MockTransport)
JEV_API_KEY=... uv run pytest -m live   # against the real API
uv run examples/triage.py           # a small support-ticket triage demo
```
