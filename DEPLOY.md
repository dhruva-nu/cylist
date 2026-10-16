# Deploying Cylist

Cylist runs on **dnu-home-1**, a machine at home. Every push to `main` that
passes CI deploys itself there, and the result is served over HTTPS at
<https://cylist.in>.

## The shape of it

Production is **one container**, not the four that `docker compose up` gives
you in development. `backend/Dockerfile` builds the React app and copies the
bundle into the API image; FastAPI answers `/api/v1` and hands every other path
`index.html` for the router in the browser to resolve. One process, one port,
one origin — which is what makes the session cookie first-party and lets CORS
be switched off entirely.

```
GitHub                          dnu-home-1
──────                          ──────────
push to main
  └─ CI on GitHub's machines    self-hosted runner (polls outbound)
     backend · agents · web       └─ scripts/deploy.sh
        all green ───────────────────┤ build image (SPA + API)
                                     │ pg_dump the database
                                     │ alembic upgrade head
                                     │ restart, wait for health
                                     ↓
                         cloudflared tunnel → cylist.in
                                     ↓
                              127.0.0.1:8000  ← app container
                                     ↓
                              postgres (no published port)
```

Nothing is exposed to your router — it cannot be, the ISP line is behind
carrier-grade NAT. The runner reaches GitHub outbound, and `cloudflared` reaches
Cloudflare outbound, which terminates HTTPS for `cylist.in` — so there is no
inbound port forward, no deploy key, and no secret of any kind stored in this
repository.

## What a deploy does

[`scripts/deploy.sh`](scripts/deploy.sh) is the whole of it, and CI runs exactly
that script — so a deploy from GitHub and one you run by hand over SSH are the
same code path. In order:

1. **Build the image.** Both halves compile here: `npm run build` type-checks
   the SPA and `uv sync --frozen` rejects a stale lockfile. A build that fails
   has touched nothing that is running.
2. **Dump the database** into `~/cylist-prod/backups`, keeping the last ten.
   This happens *before* migrations because `alembic upgrade` is the only step
   that redeploying the previous commit cannot undo.
3. **Migrate,** in a one-off container. A migration that fails fails the deploy
   visibly, rather than leaving a container restarting against a schema it does
   not match.
4. **Restart and wait for health.** If `/api/v1/health` never answers, the
   deploy fails and prints the container's logs.

## One-time setup on the server

### Secrets

They live in `~/cylist-prod`, deliberately outside any checkout, so CI cannot
overwrite them:

```
~/cylist-prod/
  app.env        CYLIST_DATABASE_URL, CYLIST_PASSWORD_HASH, CYLIST_VAULT_KEY,
                 JEV_API_KEY
  postgres.env   POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB
  backups/       the dumps deploy.sh leaves behind
```

Both files are `chmod 600`. Everything that is not actually secret —
`CYLIST_ENVIRONMENT`, the data directory, the empty CORS list — is set in
`docker-compose.prod.yml` instead, where you can read it.

> **`CYLIST_VAULT_KEY` is the one irreplaceable value here.** Change it and
> every vault secret already stored becomes unreadable. It is deliberately kept
> out of the backups, so keep a copy somewhere you can recover it.

Generate the two Cylist secrets with `make hash-password` and `make vault-key`.
`JEV_API_KEY` is TypeSafe's, for jev (see backend/.env.example); without it the
server still runs, but `ask_docs` answers nothing and agents read the code.
Set it before deploying 0033, too: that migration asks jev which topic each old
scratchpad note belongs under, and without a key files them all under
*Engineering → Agents*.

### The runner

A self-hosted GitHub Actions runner, labelled `cylist`, lives in
`~/actions-runner` and is run by systemd as `dnu2`:

```bash
systemctl status github-runner-cylist
journalctl -u github-runner-cylist -f
```

It is a **system** unit, and the reason is worth knowing, because the symptom is
baffling on its own:

> `permission denied while trying to connect to the Docker daemon socket`
> — from a user who is plainly in the `docker` group.

A systemd *user* manager fixes its supplementary groups once, when it starts. If
`dnu2` joined `docker` after that — which is to say, at any point after the last
login — then every `systemctl --user` service keeps the older group set for as
long as that manager lives, however many times the service itself is restarted.
An SSH login gets fresh groups and works fine, so deploying by hand succeeds
while the identical deploy from CI fails. Restarting the user manager would fix
it and would also kill the desktop session, since `dnu2` is the machine's
logged-in user.

`SupplementaryGroups=docker` in a system unit sidesteps all of it: the group is
granted at exec, every time. It also means the runner comes up at boot rather
than at login, which is what you want on a server.

The unit is [`scripts/github-runner-cylist.service`](scripts/github-runner-cylist.service);
installing it is a copy into `/etc/systemd/system` and an `enable --now`.

To re-register the runner (a new repository, or a revoked token):

```bash
gh api repos/dhruva-nu/cylist/actions/runners/registration-token -q .token
cd ~/actions-runner && ./config.sh --unattended --replace \
  --url https://github.com/dhruva-nu/cylist --token <token> \
  --name dnu-home-1 --labels cylist --work _work
```

### Serving it

Production is on the public internet as <https://cylist.in> through a
Cloudflare Tunnel: `cloudflared` on the server holds outbound connections to
Cloudflare's edge, Cloudflare terminates HTTPS, and requests for `cylist.in` are
relayed down the tunnel to the app on loopback — the only thing that reaches it.
The app's own password login is the only gate, which is why production sets
`CYLIST_ENVIRONMENT=prod`, so the session cookie is issued `Secure`.

It is a *locally-managed* tunnel, created from the CLI (the Zero Trust
dashboard asks for a payment method; this path does not):

```bash
cloudflared tunnel login                   # browser; writes ~/.cloudflared/cert.pem
cloudflared tunnel create cylist           # writes ~/.cloudflared/<id>.json
cloudflared tunnel route dns cylist cylist.in
```

`~/.cloudflared/config.yml` names the tunnel, its credentials file, and one
ingress rule per deployment on this machine — a hostname each, every target a
loopback port — with a `http_status:404` catch-all:

| hostname | → | what |
| --- | --- | --- |
| `cylist.in` | `127.0.0.1:8000` | production |
| `stage.cylist.in` | `127.0.0.1:8001` | staging |
| `dev.cylist.in` | `127.0.0.1:8010` | the dev index |
| `devN.cylist.in` | `127.0.0.1:801N` | dev slot N, 1–5 |

Each hostname is a CNAME to the tunnel (`cloudflared tunnel route dns cylist
<hostname>`, once). The rules are static: a deploy never touches them, it only
has to be up on its port. To change them, edit `~/.cloudflared/config.yml`,
validate, and copy it over `/etc/cloudflared/config.yml`, which is what the
service reads. `cloudflared tunnel ingress validate` checks it, and
`cloudflared tunnel run cylist` serves it in the foreground for a test. The
real thing is the `cloudflared` systemd service, installed with
`sudo cloudflared --config ~/.cloudflared/config.yml service install --no-update-service`
(the `--config` matters: sudo changes `$HOME`). That copies `config.yml` to
`/etc/cloudflared/` but keeps reading the credentials JSON from
`~/.cloudflared/`, so that directory must stay:

```bash
systemctl status cloudflared
journalctl -u cloudflared -f               # "Registered tunnel connection" ×4 when healthy
```

To check it from outside, the MCP endpoint should answer `401` from Cylist
itself, not an error page from Cloudflare:

```bash
curl -si -X POST https://cylist.in/mcp | head -1
```

Everything above is on the public internet, each behind its own password
login; only the dashboard (`:7443`) is still `tailscale serve`, tailnet-only.
The old Funnel route for production is off (`tailscale funnel --https=443
off`).

**Turn on "Always Use HTTPS"** for the zone (Cloudflare dashboard → SSL/TLS →
Edge Certificates). Cloudflare otherwise serves plain `http://` as-is, and
every deployed environment issues its session cookie `Secure`, so a login over
`http://` succeeds and the very next request is `401`.

### Telling clients where to find it

A stack is reachable at more than one address, and which one works depends on
where the laptop asking is — not on anything either end can settle once. So
each compose file lists them, and the app hands the list to any client that
asks (`GET /api/v1/setup`, unauthenticated: it is read before a client has a
token):

```yaml
CYLIST_CLIENT_URLS: '["http://localhost:8000","https://cylist.in"]'
```

| | production | staging | dev slot N |
|---|---|---|---|
| local | `http://localhost:8000` | `http://localhost:8001` | `http://localhost:801N` |
| remote | `cylist.in` | `stage.cylist.in` | `devN.cylist.in` |
| off the tailnet | yes | yes | yes — all through the one Cloudflare Tunnel |

`cylist setup` stores every address it is given, and the CLI and the MCP server
try them in order, moving on when one will not *connect*. That is what makes
one setup work on the tailnet, off it, and on this machine itself — see
[`cli/README.md`](cli/README.md).

Two things to keep straight:

* **The app never dials these.** They are advisory data returned to callers, so
  `localhost` in the list means *the caller's* localhost — right for something
  running on this machine, an instant refusal (and then the next address) for
  anything else. Cheap either way, which is why it stays first.
* **Every remote address works from anywhere**, since all of them are
  hostnames on the one tunnel; nothing here is tailnet-only any more. The
  failover mechanism is address-agnostic either way — it steps over what does
  not connect, it cannot invent reachability.

Change one of these and the client keeps the old list until its next `cylist
setup` or `cylist login`. Neither is urgent: an address that has gone away
costs one connection failure and is stepped over.

### MCP at `/mcp`

The same process serves the MCP tools over streamable HTTP at `/mcp`, beside
`/api/v1` and on the same tunnel. That means one `claude mcp add --transport
http` line connects a machine anywhere production is reachable, off the tailnet
included (see [`mcp/README.md`](mcp/README.md)). There is nothing to configure:
the `mcp/` package is a path dependency, copied into the image by
`backend/Dockerfile`, and `/mcp` answers only `POST` with a bearer token.

To check it from outside, a missing token should get `401` from Cylist itself,
not `502` from the proxy:

```bash
curl -si -X POST https://<host>/mcp | head -1
```

### WebSockets through the proxy

Two things hold a socket now: a browser watching a board, and each agent
session reporting on a card. `wss:` follows `https:` automatically — the
frontend derives the scheme from the page it was served by — and `tailscale
serve`/`funnel` forwards `Upgrade` end to end, so **there is nothing to
configure**. There is no flag for it, and there would be nothing to turn on
if it did not work.

**This has never been exercised in this deployment.** Until CYLIST-40 no
WebSocket traffic existed at all, so the paragraph above is a reasonable
expectation rather than an observation. Check it after the first deploy that
includes this, from a laptop rather than from the server — loopback bypasses
the very thing under test:

```bash
curl -isk -N --http1.1 \
  -H "Connection: Upgrade" -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Version: 13" \
  -H "Sec-WebSocket-Key: $(head -c16 /dev/urandom | base64)" \
  -H "Authorization: Bearer $CYLIST_TOKEN" \
  https://<host>/api/v1/agent-sessions/probe-1/ws | head -20
```

| First line | What it means |
| --- | --- |
| `101 Switching Protocols` | The upgrade survived the proxy. This is the answer you want. |
| `200` with HTML | The SPA mount swallowed it — wrong path, or a route registered after `mount_spa`. |
| `403` | The origin check refused it. |
| `502` / Cloudflare error page | `cloudflared` is down or cannot reach `127.0.0.1:8000` — `journalctl -u cloudflared`. |

A `101` does **not** prove the token is good: a bad credential is accepted
and then closed with 4401, deliberately, because a browser cannot read a
handshake's status code and would otherwise be unable to tell "signed out"
from "server down".

`GET /api/v1/health/realtime` reports how many sockets this process is
holding — agent connections and board watchers, counts only, no names. That
is the way to answer "did the upgrade actually work last Tuesday, when
nobody was looking": a board that has silently fallen back to polling looks
exactly like a working one from the outside, and shows up here as watchers
that never arrive.

If sockets turn out not to get through, nothing breaks. The board keeps its
ten-second poll as a fallback and the agent hooks keep reporting over HTTP;
what is lost is the promptness, not the correctness.

### Never add `--workers`

One uvicorn process per environment, and this is now a constraint rather
than a default. The registry of who is connected is a dictionary in that
process, which is a complete and correct answer for one worker and a wrong
one for two:

* Fan-out splits silently. An agent's change reaches the boards on its own
  worker and no others, so most open boards freeze — intermittently, and
  looking exactly like a frontend bug.
* The startup sweep, which ends every open agent session because a
  just-started process can hold no sockets, would end the sessions its peer
  workers are actively holding. Whichever worker restarts last wipes the
  board.

If the day comes that one process is not enough, `Hub.publish` in
`backend/app/realtime/hub.py` is the single fan-out entry point and takes a
JSON-serialisable dict, so backing it with Postgres `LISTEN`/`NOTIFY` is a
change to one method. Until somebody does that deliberately, do not add the
flag.

## Logs

`make prod-logs` follows the running container, and for most questions that is
the whole answer. Its limit is that it is Docker's buffer: `docker compose
down` takes it with it, and so does enough traffic.

For logs that outlive the container, add one line to `app.env` and redeploy:

```bash
CYLIST_LOG_TO_FILE=true
```

They are then written to `/data/logs/cylist.log` inside the container, which is
the `cylist-data` volume — the same one holding uploaded files, so
`scripts/backup.sh` already carries it. Rotation is at 10 MB with five files
kept, so the most this can ever occupy is 60 MB; an unbounded log file on this
machine would fill the disk and take Postgres down with it, which is a worse
outage than the missing logs it was meant to prevent.

Reading them on the server:

```bash
docker compose -f docker-compose.prod.yml exec app tail -f /data/logs/cylist.log
```

Two other variables are worth knowing. `CYLIST_LOG_LEVEL=DEBUG` adds every
static asset and every health check — for a problem being chased, not for
leaving on. `CYLIST_LOG_FORMAT=json` writes one JSON object per line, including
tracebacks, which is what to set if anything is ever pointed at these files:

```bash
docker compose -f docker-compose.prod.yml exec app \
  sh -c 'grep WARNING /data/logs/cylist.log' | jq -r '.context.path' | sort | uniq -c
```

Staging and dev take the same variables in their own `app.env`, and write to
their own volumes.

Every line carries the id of the request that produced it, and every response
carries the same id in `X-Request-ID`. When somebody reports a failure, that id
is the one thing worth asking them for.

## When a deploy fails

**`permission denied ... /var/run/docker.sock`,** from a user who is in the
`docker` group — the runner's groups, not the user's. See the note above, and
check the process itself:

```bash
grep ^Groups: /proc/$(systemctl show -p MainPID --value github-runner-cylist)/status
getent group docker        # the gid to look for
```

**That fixed nothing, and the journal says `A session for this runner already
exists`** — an older `Runner.Listener` survived a stop and still holds the
session, so it, and not the service you just repaired, is taking the jobs.
`pgrep -af Runner.Listener` lists them; `ps -o lstart= -p <pid>` tells you which
is the stale one. Kill it. `KillMode=mixed` in the unit is what stops this
happening again.

**A migration failed** — nothing was restarted, so the previous container is
still serving. Fix the migration and deploy again, or restore the dump
`deploy.sh` took moments earlier; see *Rolling back*.

## Staging

Staging runs on the same machine, from the **`staging` branch**, on **:8001** —
served at <https://stage.cylist.in>, through the same Cloudflare Tunnel as
production and just as public, behind its own password login.

It is deliberately production's shape, not development's: the same
`backend/Dockerfile` `runtime` image, the same one-container deploy, the same
`scripts/deploy.sh` — only the argument differs.

```bash
make staging-deploy     # build, migrate, restart          (scripts/deploy.sh staging)
make staging-refresh    # reload it from production's data
make staging-logs       # follow it
make staging-ps         # what is running
```

Staging is deployed when you ask for it, and not on every push. Pushing to
`staging` runs CI's three test jobs and stops there; the deploy is
[`.github/workflows/deploy-staging.yml`](.github/workflows/deploy-staging.yml),
triggered by hand from the Actions tab (`workflow_dispatch`) on the same
self-hosted runner. Run it from the `staging` branch — from any other branch it
fails on its first step rather than putting that branch on :8001, which is what
dev is for.

So the test result is still there to read before you trigger, on the commit the
push produced; what the workflow no longer does is decide *when* staging moves.
A merge into `staging` leaves whatever is running on :8001 running.

### What differs from production, and why

| | production | staging |
| --- | --- | --- |
| compose project | `cylist-prod` | `cylist-staging` |
| image tag | `cylist:latest` | `cylist:staging` |
| port | `127.0.0.1:8000` | `127.0.0.1:8001` |
| served at | `cylist.in` | `stage.cylist.in` — both public, via the tunnel |
| secrets | `~/cylist-prod` | `~/cylist-staging` |
| `CYLIST_ENVIRONMENT` | `prod` | `staging` |

Two of those are load-bearing rather than cosmetic:

**The image tag.** Staging builds `cylist:staging`, never `cylist:latest`. If
both wrote the same tag, a staging build would move the tag production restarts
from, and the next `docker compose up` in production would silently adopt
staging's code.

**`CYLIST_ENVIRONMENT=staging`.** The app treats `staging` and `prod` alike
wherever being *deployed* is what matters — `Settings.is_deployed`. It issues
the session cookie `Secure`, because both sit behind `tailscale serve` and are
reached over HTTPS; and `make seed` refuses to run, because staging holds real
rows. It also names the cookie `cylist_session_staging` rather than
production's `cylist_session`: both are on the same host, a browser does not
separate cookies by port, and with one name signing in to either signed you out
of the other (CYLIST-81).

> A `Secure` cookie is dropped by the browser over plain HTTP, and the symptom
> is a login that returns 200 and then does not stick. Reach staging through
> its HTTPS address, not `http://127.0.0.1:8001` directly.

### Staging holds production's data

`make staging-refresh` replaces staging's database *and* its uploaded blobs with
a copy taken from production at that moment — the two move together, or staging
ends up with file rows pointing at blobs it does not have. Production is only
read: `pg_dump`, and a read-only mount of its data volume.

That has a consequence worth being explicit about:

> **Staging's secrets are production's secrets.** The dump carries real vault
> ciphertext, so `~/cylist-staging/app.env` must hold the **production**
> `CYLIST_VAULT_KEY` for any of it to decrypt. That irreplaceable key now exists
> in two directories on this machine. Staging's password hash and database
> password are its own; the vault key cannot be.

If you would rather staging not hold real secrets, give it its own vault key and
seed it instead of refreshing — `make seed` against a staging database with
`CYLIST_ENVIRONMENT=dev`. Then vault entries restored from production will not
decrypt, which is the trade.

### One-time setup

```
~/cylist-staging/
  app.env        CYLIST_DATABASE_URL, CYLIST_PASSWORD_HASH, CYLIST_VAULT_KEY,
                 JEV_API_KEY
  postgres.env   POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB
  backups/       the dumps staging deploys leave behind
```

Both files `chmod 600`, as production's are. Serving it is the
`stage.cylist.in` ingress rule in the tunnel config — see *Serving it* — and
nothing per deploy.

## Dev

Dev is **five slots**, `dev_1` to `dev_5`, on the same machine — so five
branches can be up side by side instead of taking turns on one stack. Slot N
listens on **:801N** and is served at `https://devN.cylist.in/`, through the
same Cloudflare Tunnel as everything else. <https://dev.cylist.in/> is a small
index of which branch and commit is in each slot and when it went there.

The slots differ in the code they run, not in the data they show: all five use
**one Postgres** and **one blob volume**, so a project, card or file made on
one slot is there on every other. See *One database for every slot* below.

A slot is the same as staging except for one thing: it is not tied to a branch.
The workflow is [`.github/workflows/deploy-dev.yml`](.github/workflows/deploy-dev.yml),
triggered by hand from the Actions tab (`workflow_dispatch`). GitHub's own
"Run workflow" dropdown lets you pick any branch, and the checkout step follows
whatever you picked; the inputs are the **slot** and, optionally, the **card**
to tell. So the loop is: push work to a branch, run this workflow against that
branch into a free slot, poke at it at its hostname, and only once it looks right
does it earn a place on `staging`.

```bash
gh workflow run deploy-dev.yml --ref feature/my-branch -f slot=2 -f card=CYLIST-79
```

Runs for the same slot queue behind each other — two deploys into one slot
would fight over its containers — while different slots never wait on each
other. Deploying into a slot replaces whatever branch was there; the index says
what that was.

It does not wait on CI's test jobs the way the `main` deploy does. That is
deliberate — dev exists so half-finished work can be looked at before it is
finished, not after it has already cleared the bar staging demands. (Staging's
own deploy does not wait on them either, but for a different reason: the push
to `staging` has already run them.)

```bash
make dev-deploy SLOT=2   # build, migrate, restart       (scripts/deploy.sh dev 2)
make dev-logs SLOT=2     # follow it
make dev-ps SLOT=2       # what is running
make dev-down SLOT=2     # stop it (volumes kept)
```

### Telling the card where it went

`card` is optional. Given a card reference (`CYLIST-79`; anything not shaped
`KEY-number` is refused before the deploy starts), a successful deploy ends by
commenting on that card in production Cylist, as **Agent**:

```
Deployed to dev_2 — https://dev2.cylist.in/ — branch feature/my-branch @ 0123456789ab
```

That is the same line the `deploy-dev` project skill
([`docs/skills/deploy-dev/SKILL.md`](docs/skills/deploy-dev/SKILL.md)) has an
agent post through MCP after it moves the card to **Dev**. The workflow posts
it too, so that a deploy started by hand from the Actions tab is on the card as
well. An agent reads the card first and posts only if the line isn't already
there. The step is
[`scripts/dev-card-comment.sh`](scripts/dev-card-comment.sh):
`POST /api/v1/tasks/<card>/comments`, with `author_id` set to the directory
entry flagged `is_agent`, which it looks up from `GET /api/v1/people`.

It needs one secret, **`CYLIST_AGENT_TOKEN`**: a production API token with
`read` and `write`, minted by someone who can comment on the board. A token
acts as whoever minted it, so their role decides what it may do. Comments need
the `comments` permission. To mint one:

```bash
curl -sX POST https://cylist.in/api/v1/tokens \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"deploy-dev workflow","scopes":["read","write"]}'
gh secret set CYLIST_AGENT_TOKEN          # paste the token; add --env dev to scope it to the dev environment
```

The secret is optional. Without it, the step prints a `::warning::` with the
line to post by hand, and the agent's own comment is the only record. The step
never fails the run, because the deploy it reports on has already succeeded.
An unreachable production, a revoked token (401) or an unknown card (404) are
all warnings. The token goes to curl in a config file, never on a command line,
and is never printed. To point the step at a Cylist other than production, set
the repository variable `CYLIST_URL`.

### What differs from staging, and why

| | staging | dev slot N |
| --- | --- | --- |
| compose project | `cylist-staging` | `cylist-dev-N` |
| image tag | `cylist:staging` | `cylist:dev-N` |
| port | `127.0.0.1:8001` | `127.0.0.1:801N` |
| served at | `stage.cylist.in` | `devN.cylist.in` |
| secrets | `~/cylist-staging` | `~/cylist-dev-shared`, the same for every slot |
| deploy trigger | manual, `staging` only | manual, any branch, pick a slot |
| data | copied from production | shared by all five slots, empty to start |
| `CYLIST_ENVIRONMENT` | `staging` | `preview` |
| `CYLIST_BASE_PATH` | unset | unset |

All five come from one file, [`docker-compose.dev.yml`](docker-compose.dev.yml),
which takes the slot from `CYLIST_DEV_SLOT` and has no default for it — a
command that forgot which slot it meant fails instead of acting on another.

**A hostname each, no base path.** Each slot is served at the root of its own
hostname, exactly like production and staging, so `CYLIST_BASE_PATH` is empty.
The slots used to share one tailnet port and be told apart by path
(`:9443/dev_N`, with `CYLIST_BASE_PATH=/dev_N` so the app knew the prefix the
browser saw); the app still supports that shape — see
`backend/app/core/base_path.py` — and a slot built that way keeps its old
prefix until it is deployed again, which is why a slot deploy also removes any
`:9443/dev_N` mapping it finds.

**`CYLIST_ENVIRONMENT=preview`, not `dev`.** `CYLIST_ENVIRONMENT` already means
something in the app: the literal value `"dev"` is what `Settings` defaults to
on someone's own machine, where the session cookie is *not* `Secure` and
`make seed` is allowed to run — see `Settings.is_deployed` in
`backend/app/config.py`. Setting the real value to `"dev"` here would have
quietly picked up that machine's behavior on a stack that is, in every way
that matters, a deployment: one container behind the tunnel, reached over
HTTPS. `"preview"` gets it into `is_deployed` alongside staging and
production without touching what `"dev"` means anywhere else.

### What a slot deploy does beyond the usual

`scripts/deploy.sh dev N` runs the same build, dump, migrate, restart and health
check as production and staging. Around that,
[`scripts/dev-slot.sh`](scripts/dev-slot.sh):

1. **Creates the shared secrets** on the first deploy of any slot, if
   `~/cylist-dev-shared` has neither file, and the shared blob volume
   `cylist-dev-blobs`: a random database password, a fresh vault key, and the
   bootstrap password hash copied from the old single dev stack's
   `~/cylist-dev/app.env` (override with `CYLIST_DEV_TEMPLATE=<path to an
   app.env>`). Files that already exist are never touched. With no hash to
   copy the deploy warns, and nobody can sign in to the slots until one is added
   (`make hash-password`) and it is deployed again.
2. **Copies production's `JEV_API_KEY`** into the shared `app.env`, on every
   deploy — jev is a paid API, not per-environment state, so a key that exists
   once is rotated once.
3. **Records what it deployed** in `~/cylist-dev-N/deployed`, one `key=value`
   per line: `branch`, `sha`, `time` (UTC).
4. **Retires the slot's own Postgres and the old single dev stack** if their
   containers are still there (see below).
5. **Regenerates the index** at `~/cylist-dev-index/index.html` from the five
   `deployed` files. It is a static file, so it is only as current as the last
   slot deploy. A `busybox httpd` container, `cylist-dev-index`, serves that
   directory read-only on `127.0.0.1:8010`; the deploy starts it if it is not
   running. `dev.cylist.in` is relayed to that port by the tunnel.
6. **Removes the slot's old `:9443/dev_N` tailscale mapping** if one is still
   there, from before the slots had hostnames — a slot built for its hostname
   has no base path, so the old route would answer with a broken app. Best
   effort: it needs tailscale's operator, and a slot that cannot clean up is
   still a working slot. Once no slot has one left, `tailscale serve --https
   9443 off` takes the port down.
7. **Checks the slot answers through the tunnel** at
   `https://devN.cylist.in/api/v1/health`, and fails the deploy if it never
   does — up on loopback but not served is not deployed.

Steps 4–6 hold a lock (`~/.cylist-dev.lock`), since the index is shared by
all five slots. The workflow's deploy step also exposes
`slot`, `url`, `branch` and `sha` as step outputs, for a later step that wants
to say where a branch went. The *Comment on the card* step reads them.

### One-time setup

Nothing by hand — the first slot deploy creates the shared directory, and each
slot's first deploy its own:

```
~/cylist-dev-shared/
  app.env        CYLIST_DATABASE_URL, CYLIST_PASSWORD_HASH, CYLIST_VAULT_KEY,
                 JEV_API_KEY
  postgres.env   POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB
  backups/       the dumps slot deploys leave behind
~/cylist-dev-N/
  deployed       branch, sha, time of slot N's last deploy
```

Both env files `chmod 600`. The shared database starts empty — it is never
restored from production, so unlike staging's `app.env` it needs only its own
`CYLIST_VAULT_KEY`, not production's.

Serving is the `devN.cylist.in` and `dev.cylist.in` ingress rules in the
tunnel config — see *Serving it* — and nothing per deploy. Only the index
directory is served as files, by the `cylist-dev-index` container; the slot
directories hold secrets and are never mounted into it.

### One database for every slot

[`docker-compose.dev-db.yml`](docker-compose.dev-db.yml) is the one Postgres,
compose project `cylist-dev-db`, with no published port. It makes the docker
network `cylist-dev`; every slot joins it and reaches the database as
`cylist-dev-db`. Each slot mounts the shared volume `cylist-dev-blobs` at
`/data/blobs`, since a file is a database row plus bytes on disk; its logs stay
in its own volume. `make dev-db-ps` and `make dev-db-logs` look at the
database.

Every slot deploy dumps the shared database to `~/cylist-dev-shared/backups`
and then runs `alembic upgrade head` against it. So **a branch with a new
migration changes the schema under every slot.** If the database is already at
a revision the deploying branch does not have — another slot's branch added a
migration and got there first — the deploy warns and starts the slot on that
schema unmigrated, rather than failing: an older branch on a newer schema
mostly works, and dev is for looking. Two branches whose migrations fork from
the same parent cannot both be migrated; whichever deploys second runs
unmigrated until one of them is rebased.

What one process per slot means against one database, and is accepted for dev:

* **Live board updates stay within a slot.** The WebSocket fan-out is
  in-process, so a change made on dev_2 reaches boards open on dev_2 at once,
  and boards open on dev_4 on their next load.
* **A slot restarting ends open agent sessions** — every process ends them at
  start-up, believing itself the only one — and each slot's reaper can end a
  session whose socket is held by another slot.
* **Every slot runs a hook courier**, so a queued hook delivery may be picked
  up by any of them.

Before the database was shared, each slot had a Postgres of its own. A slot's
first deploy after the change removes that container; its volume is kept, as
are the old per-slot `app.env` and `postgres.env` in `~/cylist-dev-N`, which
nothing reads any more:

```bash
docker volume rm cylist-dev-N_postgres-data
rm ~/cylist-dev-N/app.env ~/cylist-dev-N/postgres.env
rm -r ~/cylist-dev-N/backups
```

### The old single dev stack

Before the slots, dev was one stack: compose project `cylist-dev`, image
`cylist:dev`, `127.0.0.1:8002`, served at the root of `:9443`. The first slot
deploy retires it — removes its containers and network and the `cylist:dev`
image — and the index takes over `:9443/`, which replaces its serve mapping.
Every later deploy finds nothing left to do.

Its data is left alone. Once nothing in it is wanted (and once the slots have
taken their bootstrap password hash from it):

```bash
docker volume rm cylist-dev_postgres-data cylist-dev_cylist-data
rm -r ~/cylist-dev
```

## Dashboard

A one-page status view of production, staging and the five dev slots, at
<https://dnu-home-1.tail222f46.ts.net:7443> — tailnet only, the one thing on
this machine that is not on the tunnel.

[`scripts/dashboard/server.py`](scripts/dashboard/server.py) is stdlib-only
Python: no dependencies to install, no build step. It answers `/` with a
static page and `/api/status` with, per environment:

* **container state and uptime** — `docker inspect` on `cylist-<env>-app-1`
  (`cylist-dev-N-app-1` for a slot).
* **health and request counts** — the app's own `/api/v1/health` and
  `/api/v1/health/requests` (added in `backend/app/routers/health.py`,
  counted by an in-process middleware that resets on every restart — see
  `backend/app/core/metrics.py`), read over loopback. That is also why there
  is no CORS to configure: the browser only ever talks to this process, never
  to the apps directly.
* **whether it is actually being served** — a real HTTP round trip to the
  address a browser would use, not a proxy for it. For staging and dev that
  is a direct request to their `tailscale serve` address; for production it
  is a request to `https://cylist.in` through Cloudflare's edge, pinned to one
  of the zone's anycast addresses with `curl --resolve` so a stale answer in
  the host's own resolver cannot fake it. That is the only check that proves
  the tunnel is registered and relaying, rather than that the app answers on
  loopback. It runs on its own 45-second timer rather than blocking the
  page's poll.
* **last backup age** — the newest file in `~/cylist-<env>/backups`.

Machine-wide, it also reports disk usage. The page polls `/api/status` every
eight seconds; the slower public-reachability check updates independently in
the background and is served from cache.

It is not deployed by CI. `scripts/cylist-dashboard.service` runs it from
`~/cylist-dashboard`, deliberately *not* from a checkout: the self-hosted
runner's working directory can be pointed at any branch by the dev workflow,
and this service must not have its own code swapped out from under it because
someone deployed something unrelated.

### One-time setup

```bash
make dashboard-sync                                    # copies the two files into place
sudo cp scripts/cylist-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cylist-dashboard
sudo tailscale serve --bg --https 7443 http://127.0.0.1:8090
```

To pick up a change to `server.py` or `index.html`: `make dashboard-sync`,
then `sudo systemctl restart cylist-dashboard`.

## Deploying by hand

```bash
ssh dnu2@dnu-home-1
cd ~/actions-runner/_work/cylist/cylist   # or any checkout
git pull
make deploy
```

`make prod-logs` follows the app, `make prod-ps` says what is running.

## Rolling back

Code rolls back by deploying an older commit — check it out and run
`make deploy`. Schema does not, which is what the pre-migration dump is for:

```bash
cd ~/cylist-prod/backups
docker compose -f ~/…/docker-compose.prod.yml exec -T postgres \
  pg_restore --clean --no-owner -U cylist -d cylist < cylist-<stamp>.dump
```

`make backup` is the separate, keep-it-forever backup: it takes the uploaded
file blobs as well as the database, which the deploy dumps do not.
