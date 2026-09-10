# Deploying Cylist

Cylist runs on **dnu-home-1**, a machine at home. Every push to `main` that
passes CI deploys itself there, and the result is served over HTTPS at
<https://dnu-home-1.tail222f46.ts.net>.

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
                              tailscale funnel :443
                                     ↓
                              127.0.0.1:8000  ← app container
                                     ↓
                              postgres (no published port)
```

Nothing is exposed to your router. The runner reaches GitHub outbound, and
Tailscale terminates HTTPS — so there is no inbound port forward, no deploy key,
and no secret of any kind stored in this repository.

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
  app.env        CYLIST_DATABASE_URL, CYLIST_PASSWORD_HASH, CYLIST_VAULT_KEY
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

```bash
sudo tailscale funnel --bg --https 443 http://127.0.0.1:8000
```

Note the shape of that. `tailscale funnel --bg 443` looks like it means "turn
Funnel on for port 443" and does not: the argument is the *target*, so it
quietly repoints the proxy at `127.0.0.1:443`, where nothing is listening — and
the site answers 502 while `funnel status` cheerfully reports Funnel on.

That terminates HTTPS, proxies to the app on loopback — the only thing that
reaches it — and puts the URL on the public internet, with the app's own
password login as the only gate. Which is why production sets
`CYLIST_ENVIRONMENT=prod`, so the session cookie is issued `Secure`.

It needs root unless you run `sudo tailscale set --operator=$USER` once, after
which it does not. `tailscale serve status` shows where it currently points, and
`sudo tailscale funnel --https=443 off` takes it back off the public internet.

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
| `502` | The `tailscale funnel --bg 443` foot-gun above. |

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
served at <https://dnu-home-1.tail222f46.ts.net:8443>, on the tailnet only. It
is not funnelled to the public internet the way production is.

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
| served at | `:443`, funnelled publicly | `:8443`, tailnet only |
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
rows. Only `Settings.is_production` still means production alone.

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
  app.env        CYLIST_DATABASE_URL, CYLIST_PASSWORD_HASH, CYLIST_VAULT_KEY
  postgres.env   POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB
  backups/       the dumps staging deploys leave behind
```

Both files `chmod 600`, as production's are. Then put it on the tailnet:

```bash
sudo tailscale serve --bg --https 8443 http://127.0.0.1:8001
```

`serve` and not `funnel`: staging is reachable from your devices, and from
nowhere else.

## Dev

Dev runs on the same machine, on **:8002**, served at
<https://dnu-home-1.tail222f46.ts.net:9443>, tailnet only — the same as
staging, except for one thing: it is not tied to a branch.

The workflow is [`.github/workflows/deploy-dev.yml`](.github/workflows/deploy-dev.yml),
triggered by hand from the Actions tab (`workflow_dispatch`). GitHub's own
"Run workflow" dropdown lets you pick any branch, and the checkout step
follows whatever you picked — so the loop is: push work to a branch, run this
workflow against that branch, poke at it on the tailnet, and only once it
looks right does it earn a place on `staging`.

It does not wait on CI's test jobs the way the `main` deploy does. That is
deliberate — dev exists so half-finished work can be looked at before it is
finished, not after it has already cleared the bar staging demands. (Staging's
own deploy does not wait on them either, but for a different reason: the push
to `staging` has already run them.)

```bash
make dev-deploy   # build, migrate, restart          (scripts/deploy.sh dev)
make dev-logs     # follow it
make dev-ps       # what is running
make dev-down     # stop it (volumes kept)
```

### What differs from staging, and why

| | staging | dev |
| --- | --- | --- |
| compose project | `cylist-staging` | `cylist-dev` |
| image tag | `cylist:staging` | `cylist:dev` |
| port | `127.0.0.1:8001` | `127.0.0.1:8002` |
| served at | `:8443` | `:9443` |
| secrets | `~/cylist-staging` | `~/cylist-dev` |
| deploy trigger | manual, `staging` only | manual, any branch |
| data | copied from production | its own, empty to start |
| `CYLIST_ENVIRONMENT` | `staging` | `preview` |

**`CYLIST_ENVIRONMENT=preview`, not `dev`.** `CYLIST_ENVIRONMENT` already means
something in the app: the literal value `"dev"` is what `Settings` defaults to
on someone's own machine, where the session cookie is *not* `Secure` and
`make seed` is allowed to run — see `Settings.is_deployed` in
`backend/app/config.py`. Setting the real value to `"dev"` here would have
quietly picked up that machine's behavior on a stack that is, in every way
that matters, a deployment: one container behind `tailscale serve`, reached
over HTTPS. `"preview"` gets it into `is_deployed` alongside staging and
production without touching what `"dev"` means anywhere else.

### One-time setup

```
~/cylist-dev/
  app.env        CYLIST_DATABASE_URL, CYLIST_PASSWORD_HASH, CYLIST_VAULT_KEY
  postgres.env   POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB
  backups/       the dumps dev deploys leave behind
```

Both files `chmod 600`. Dev starts from an empty database — it is never
restored from production, so unlike staging's `app.env` it needs only its own
`CYLIST_VAULT_KEY` (`make vault-key`), not production's.

```bash
sudo tailscale serve --bg --https 9443 http://127.0.0.1:8002
```

`serve`, not `funnel`, exactly as staging: reachable from your devices, and
from nowhere else.

## Dashboard

A one-page status view of all three environments, at
<https://dnu-home-1.tail222f46.ts.net:7443> — tailnet only, same as staging and
dev.

[`scripts/dashboard/server.py`](scripts/dashboard/server.py) is stdlib-only
Python: no dependencies to install, no build step. It answers `/` with a
static page and `/api/status` with, per environment:

* **container state and uptime** — `docker inspect` on `cylist-<env>-app-1`.
* **health and request counts** — the app's own `/api/v1/health` and
  `/api/v1/health/requests` (added in `backend/app/routers/health.py`,
  counted by an in-process middleware that resets on every restart — see
  `backend/app/core/metrics.py`), read over loopback. That is also why there
  is no CORS to configure: the browser only ever talks to this process, never
  to the three apps directly.
* **whether it is actually being served** — a real HTTP round trip to the
  address a browser would use, not a proxy for it. For staging and dev that
  is a direct request to their `tailscale serve` address; for production it
  goes further and forces the request through the public Funnel ingress with
  `curl --resolve`, the same check `~/.claude/CLAUDE.md` documents as the only
  one that catches the one outage already seen here — everything on-box
  correct, the ingress-side registration silently stale. This is slow enough
  (a real Tokyo round trip) that it runs on its own 45-second timer rather
  than blocking the page's poll.
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
