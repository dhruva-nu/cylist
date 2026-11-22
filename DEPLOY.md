# Deploying Cylist

Cylist runs on **dnu-home-1**, a machine at home, on a single-node K3s cluster.
Every push to `main` that passes CI deploys itself there, and the result is
served over HTTPS at <https://cylist.in>. Staging and five dev slots run on the
same cluster, each at a hostname of its own.

The detail lives beside the code that does it:

- [`deploy/k3s/README.md`](deploy/k3s/README.md): the cluster itself, its
  install, network, isolation, registry and upgrades.
- [`deploy/helm/README.md`](deploy/helm/README.md): the chart every
  environment is installed from.
- [`deploy/observability/README.md`](deploy/observability/README.md): metrics,
  logs, traces, uptime probes and alerts.

This file is how those pieces fit together, and what to do with them.

## The shape of it

An environment is **one app container**, not the four that `docker compose up`
gives you in development. `backend/Dockerfile` builds the React app and copies
the bundle into the API image; FastAPI answers `/api/v1` and hands every other
path `index.html` for the router in the browser to resolve. One process, one
port, one origin — which is what makes the session cookie first-party and lets
CORS be switched off entirely.

Each environment is two Helm releases of `deploy/helm/cylist` in its own
namespace: `cylist-data` (Postgres, the blobs volume and the nightly backup)
and the app (`cylist`, or `cylist-N` for dev slot N).

```
GitHub                          dnu-home-1
──────                          ──────────
push to main
  └─ CI on GitHub's machines    self-hosted runner (polls outbound)
     backend · agents · web       └─ deploy/k3s/deploy.sh
     chart                           │ docker build, push to localhost:5000
        all green ───────────────────┤ secrets from ~/cylist-prod
                                     │ pg_dump the database
                                     │ helm upgrade: migrate hook, roll out
                                     │ wait for health through Traefik
                                     ↓
                    cloudflared tunnel → cylist.in, stage., dev., devN.
                                     ↓
                    Traefik on 127.0.0.1:80, routes by Host
                                     ↓
                    app pod (cylist-prod/cylist, …)
                                     ↓
                    cylist-data-postgres (no Service outside its namespace)
```

Nothing is exposed to your router — it cannot be, the ISP line is behind
carrier-grade NAT. The runner reaches GitHub outbound, and `cloudflared` reaches
Cloudflare outbound, which terminates HTTPS for every hostname — so there is no
inbound port forward, no deploy key, and no secret of any kind stored in this
repository.

| | namespace | app release | hostname | secrets |
| --- | --- | --- | --- | --- |
| production | `cylist-prod` | `cylist` | `cylist.in` | `~/cylist-prod` |
| staging | `cylist-staging` | `cylist` | `stage.cylist.in` | `~/cylist-staging` |
| dev slot N | `cylist-dev` | `cylist-N` | `devN.cylist.in` | `~/cylist-dev-shared` |
| dev index | `cylist-dev` | `dev-index` (not Helm) | `dev.cylist.in` | none |

## What a deploy does

[`deploy/k3s/deploy.sh`](deploy/k3s/deploy.sh) is the whole of it, and CI runs
exactly that script — so a deploy from GitHub and one you run by hand are the
same code path. `deploy/k3s/deploy.sh` is production, `deploy/k3s/deploy.sh
staging` staging, `deploy/k3s/deploy.sh dev N` slot N. In order:

1. **Build the image** and push it to the in-cluster registry as
   `localhost:5000/cylist:<short sha>`. Both halves compile here: `npm run
   build` type-checks the SPA and `uv sync --frozen` rejects a stale lockfile.
   A build that fails has touched nothing that is running. A tree with local
   changes gets a `-dirty-<time>` tag, never the commit's.
2. **Update the secrets** with [`deploy/k3s/secrets.sh`](deploy/k3s/secrets.sh),
   from the env files on this machine (see *Secrets*).
3. **Make sure Postgres is up**: the `cylist-data` release.
4. **Dump the database** into `<secrets dir>/backups/k3s/`, keeping the last
   ten. This happens *before* migrations because `alembic upgrade` is the only
   step that redeploying the previous commit cannot undo.
5. **Migrate and roll out**: `helm upgrade --rollback-on-failure --wait`. The
   chart's pre-upgrade hook Job runs `alembic upgrade head`; if it fails, or
   the new pod never becomes ready, Helm puts the previous release back.
6. **Check health**: `/api/v1/health` through Traefik on `127.0.0.1:80`, by
   hostname. If it never answers, the deploy fails and prints the pod's logs.

The script uses whatever kubeconfig `KUBECONFIG` names. CI gives it
`/home/dnu2/.kube/cylist-deployer.yaml`, the `cylist-deployer` service account,
which can reach the `cylist-*` namespaces and nothing else; it is written by
[`deploy/k3s/deployer-kubeconfig.sh`](deploy/k3s/deployer-kubeconfig.sh).

## One-time setup on the server

The cluster, its platform manifests and the registry are in
[`deploy/k3s/README.md`](deploy/k3s/README.md), and monitoring is
`deploy/observability/install.sh`. What follows is Cylist's own.

### Secrets

They live in `~/cylist-prod`, deliberately outside any checkout, so CI cannot
overwrite them:

```
~/cylist-prod/
  app.env                    CYLIST_DATABASE_URL, CYLIST_PASSWORD_HASH, CYLIST_VAULT_KEY,
                             (CYLIST_VAULT_KEYS, once rotated), JEV_API_KEY
  postgres.env               POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB
  fcm-service-account.json   the Firebase key for pushes (production only)
  backups/k3s/               the dumps deploy.sh leaves behind
```

All of them `chmod 600`. Every deploy turns them into the namespace's Secrets
(`cylist-app`, `cylist-postgres` and, for production, `cylist-fcm`), so editing
a file and redeploying is how a secret changes. Two values are changed on the
way in: the host in `CYLIST_DATABASE_URL` becomes the in-cluster Postgres,
whatever the file says, and `CYLIST_FCM_SERVICE_ACCOUNT_FILE` is dropped,
because the chart mounts the key and sets the path itself. Everything that is
not actually secret — `CYLIST_ENVIRONMENT`, the data directory, the empty CORS
list, the client URLs — is in the chart's `values*.yaml` instead, where you can
read it.

> **`CYLIST_VAULT_KEY` is the one irreplaceable value here.** Change it and
> every vault secret already stored becomes unreadable. It is deliberately kept
> out of the backups, so keep a copy somewhere you can recover it.

#### Rotating the vault key

Changing a key is never done by editing `CYLIST_VAULT_KEY`. The vault holds a
numbered *keyring*: `CYLIST_VAULT_KEY` is version 1, and later versions go in
`CYLIST_VAULT_KEYS` as JSON. Every stored ciphertext — vault secrets and hook
signing secrets alike — carries the version that sealed it and opens with that
key; everything new is sealed with the highest version. So a rotation, for a
suspected leak or on a schedule, is three steps, and nothing is unreadable at
any point along the way:

1. **Add a key.** Generate one with `make vault-key` (only the value is used),
   keep a copy beside the old one, and add it to `app.env` with the next
   number, leaving `CYLIST_VAULT_KEY` where it is:

   ```
   CYLIST_VAULT_KEYS={"2": "<new key>"}
   ```

   Redeploy (`make deploy`). From now on new and edited secrets are sealed
   under version 2; old rows still open under version 1.

2. **Reseal.** Re-encrypt every row still on an older key, while the app keeps
   serving:

   ```bash
   kubectl -n cylist-prod exec deploy/cylist -c app -- python -m app.cli vault-reseal
   ```

   It works in batches of 200 rows (`--batch-size` to change), each its own
   transaction, so it can be interrupted and run again. It ends by counting
   the rows on each version and naming the versions no row needs any more. A
   row that will not open — tampered with, or sealed under a key already gone
   — is named, left as it was, and makes the command exit 1.

3. **Retire the old key.** Once the reseal says version 1 is no longer needed,
   empty `CYLIST_VAULT_KEY=` and redeploy. The keyring is now just version 2.
   A later rotation adds `"3"` the same way, and retiring 2 means removing it
   from `CYLIST_VAULT_KEYS`.

Three things outlive the retirement and still need the old key:

- **Backups.** A dump taken before the reseal holds version-1 ciphertext.
  Keep the retired key, offline, for as long as you keep those dumps.
- **Staging.** It restores production's data, so its `app.env` needs
  production's keyring — add the same `CYLIST_VAULT_KEYS` there in step 1, and
  retire the old key there in step 3.
- **Dev slots** restored from production, for the same reason.

For a *leaked* key, retiring it is the point; do steps 1–3 back to back, and
remember the leak exposed every secret sealed under it, so the credentials in
the vault and every hook's signing secret should be changed at their source
too.

#### Push notifications

Push notifications (CYLIST-107) need a Firebase service-account key for the
`cylist-mobile` project, kept as `~/cylist-prod/fcm-service-account.json`
(`chmod 600`). `secrets.sh` refuses to deploy production without it. It becomes
the `cylist-fcm` Secret, which `values-prod.yaml` names as `app.fcmSecret`; the
chart mounts it and points `CYLIST_FCM_SERVICE_ACCOUNT_FILE` at it. Staging and
dev have no `fcmSecret`, so pushes are off there.

The log's start-up lines say `Push notifications are on` with the Firebase
project, or why they are off. Without the key the server runs as before and
every push is a debug line; a key that cannot be read is an error line, not a
failed start. Pushes go only to phones whose token is still live, so revoking a
phone's token in the web app silences it.

Generate the two Cylist secrets with `make hash-password` and `make vault-key`.
`JEV_API_KEY` is TypeSafe's, for jev (see backend/.env.example); without it the
server still runs, but `ask_docs` answers nothing and agents read the code.

### The runner

A self-hosted GitHub Actions runner, labelled `cylist`, lives in
`~/actions-runner` and is run by systemd as `dnu2`:

```bash
systemctl status github-runner-cylist
journalctl -u github-runner-cylist -f
```

It still needs Docker — every deploy runs `docker build` and `docker push` —
and it is a **system** unit, and the reason is worth knowing, because the
symptom is baffling on its own:

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
Runner jobs do not see `~/.local/bin`, so tools a workflow calls (`helm`,
`kubectl`, `jq`) belong in `/usr/local/bin`.

To re-register the runner (a new repository, or a revoked token):

```bash
gh api repos/dhruva-nu/cylist/actions/runners/registration-token -q .token
cd ~/actions-runner && ./config.sh --unattended --replace \
  --url https://github.com/dhruva-nu/cylist --token <token> \
  --name dnu-home-1 --labels cylist --work _work
```

### Serving it

Every environment is on the public internet through one Cloudflare Tunnel:
`cloudflared` on the server holds outbound connections to Cloudflare's edge,
Cloudflare terminates HTTPS, and requests are relayed down the tunnel to
Traefik on loopback, which routes each to its environment by `Host`. The app's
own password login is the only gate, which is why every deployed environment
issues its session cookie `Secure`.

It is a *locally-managed* tunnel, created from the CLI (the Zero Trust
dashboard asks for a payment method; this path does not):

```bash
cloudflared tunnel login                   # browser; writes ~/.cloudflared/cert.pem
cloudflared tunnel create cylist           # writes ~/.cloudflared/<id>.json
cloudflared tunnel route dns cylist cylist.in
```

`~/.cloudflared/config.yml` names the tunnel, its credentials file, and one
ingress rule per hostname, every one to `http://127.0.0.1:80`, with a
`http_status:404` catch-all:

| hostname | what |
| --- | --- |
| `cylist.in` | production |
| `stage.cylist.in` | staging |
| `dev.cylist.in` | the dev index |
| `devN.cylist.in` | dev slot N, 1–5 |

Each hostname is a CNAME to the tunnel (`cloudflared tunnel route dns cylist
<hostname>`, once), and each app release's Ingress claims its hostname in
Traefik. The rules are static: a deploy never touches them. To change them,
edit `~/.cloudflared/config.yml`, check it with `cloudflared tunnel ingress
validate`, copy it over `/etc/cloudflared/config.yml`, which is what the
service reads, and **restart the service** (`sudo systemctl restart
cloudflared`): it does not pick up a changed ingress on its own.
`cloudflared tunnel run cylist` serves it in the foreground for a test.

The service was installed with
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

On this machine, `curl -H 'Host: <hostname>' http://127.0.0.1/api/v1/health`
asks an environment through Traefik without the tunnel; that is what the
deploy's health check does.

Everything above is on the public internet, each behind its own password
login; only the status dashboard (`:7443`) is `tailscale serve`, tailnet-only.

**Turn on "Always Use HTTPS"** for the zone (Cloudflare dashboard → SSL/TLS →
Edge Certificates). Cloudflare otherwise serves plain `http://` as-is, and
every deployed environment issues its session cookie `Secure`, so a login over
`http://` succeeds and the very next request is `401`.

### Telling clients where to find it

An environment could be reachable at more than one address, and which one works
depends on where the laptop asking is. So each environment's values file lists
them as `CYLIST_CLIENT_URLS`, and the app hands the list to any client that asks
(`GET /api/v1/setup`, unauthenticated: it is read before a client has a token).
Today each lists only its public hostname — nothing deployed listens on a
localhost port — for example in `values-prod.yaml`:

```yaml
CYLIST_CLIENT_URLS: '["https://cylist.in"]'
```

`cylist setup` stores every address it is given, and the CLI and the MCP server
try them in order, moving on when one will not *connect* — see
[`cli/README.md`](cli/README.md). The app never dials these; they are advisory
data returned to callers. Change one and the client keeps the old list until its
next `cylist setup` or `cylist login`; an address that has gone away costs one
connection failure and is stepped over.

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

Two things hold a socket: a browser watching a board, and each agent session
reporting on a card. `wss:` follows `https:` automatically — the frontend
derives the scheme from the page it was served by — Cloudflare and
`cloudflared` forward `Upgrade` end to end, and Traefik's `web` entrypoint has
no read or write timeout (`deploy/k3s/host/traefik-config.yaml`), so a quiet
socket is not cut off. The blackbox exporter probes production's WebSocket
upgrade from outside (see the observability README).

To check it by hand, from a laptop rather than from the server — loopback
bypasses the very thing under test:

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
| `404` from Traefik | No Ingress claims that hostname — is the release installed? |
| `502` / Cloudflare error page | `cloudflared` is down or cannot reach `127.0.0.1:80` — `journalctl -u cloudflared`. |

A `101` does **not** prove the token is good: a bad credential is accepted
and then closed with 4401, deliberately, because a browser cannot read a
handshake's status code and would otherwise be unable to tell "signed out"
from "server down".

`GET /api/v1/health/realtime` reports how many sockets this process is
holding — agent connections and board watchers, counts only, no names. A board
that has silently fallen back to polling looks exactly like a working one from
the outside, and shows up here as watchers that never arrive.

If sockets turn out not to get through, nothing breaks. The board keeps its
ten-second poll as a fallback and the agent hooks keep reporting over HTTP;
what is lost is the promptness, not the correctness.

### More than one worker

Each environment runs one replica with one uvicorn process today, because one
is plenty, and the chart rolls it out with `Recreate` since the blobs volume is
ReadWriteOnce. It no longer has to be one process (CYLIST-133): `--workers N`
on the same database is safe. What used to make it unsafe, and what replaced
it:

* **Fan-out.** Who is connected is still a dictionary per process, but every
  committed event now also goes out as a Postgres `NOTIFY` on the
  `cylist_events` channel, and every process `LISTEN`s on a connection of its
  own and tells its own boards, long-polls and agent sockets
  (`backend/app/realtime/bus.py`). A board on one worker hears an agent on
  another. Each process holds one extra database connection for it.
* **Whether an agent session is alive.** That was a question about one
  process's memory. The process holding an agent's socket now stamps
  `agent_session.socket_seen_at` about once a minute, and any process's
  reaper reads that.
* **The startup sweep** ended every open session when a process started,
  which would have ended the sessions a peer was holding. It is gone; a
  session whose socket died with its process is reaped once it has been
  quiet past `AGENT_QUIET_AFTER`. Every process runs the reaper, and a
  Postgres advisory lock makes sure only one pass at a time does the work.

Two things stay per process and are worth knowing before adding workers:
`GET /api/v1/health/realtime` counts the sockets *the process that answered*
holds, not the deployment's; and the sign-in throttle (CYLIST-121) is
in-process, so N workers let N times as many attempts through.

## Logs

Every app writes one JSON object per line to stdout (`CYLIST_LOG_FORMAT=json`,
set by the chart). Alloy ships every pod's log to Loki, kept for 30 days, so
logs outlive the pod: Grafana → Explore → Loki, for example
`{environment="prod", container="app", level="ERROR"}`. For the running pod
alone, `make prod-logs` (or `staging-logs`, `dev-logs SLOT=N`) follows it with
`kubectl logs`. How to reach Grafana is in the observability README.

`CYLIST_LOG_TO_FILE` is not worth setting in the cluster: the app's `/data` is
scratch space (only `/data/blobs` is a volume), so the file goes with the pod.

**The `client` on each line is the real caller**, not Traefik. The app only
ever hears from Traefik's pod, so the chart sets
`CYLIST_TRUSTED_PROXY_CIDRS='["10.42.0.0/16"]'`, the pod network: a request
from inside it has its client taken from `CF-Connecting-IP` (Cloudflare sets
it; nothing upstream can append to it), and the proxy's address is kept beside
it as `peer`. A request from anywhere else keeps its peer as the client and its
headers are ignored. The same address feeds the sign-in log and the login rate
limit (`429` with `Retry-After` after five attempts at one account, or thirty
from one address, a minute). If the cluster is ever put behind a different
proxy, the networks it connects from go in that list and nothing else does.

`CYLIST_LOG_LEVEL=DEBUG` in an environment's `app.env` adds every static asset
and every health check — for a problem being chased, not for leaving on.

Every line carries the id of the request that produced it, and every response
carries the same id in `X-Request-ID`. When somebody reports a failure, that id
is the one thing worth asking them for.

Traces go to Tempo: the chart sets `CYLIST_OTLP_ENDPOINT` to Alloy
(`http://alloy.monitoring:4318`). Each request is a server span with a child for
every statement and every hook delivery, under `service.name=cylist` and
`deployment.environment` set from `CYLIST_ENVIRONMENT`. Production samples a
fifth of requests (`CYLIST_TRACE_SAMPLE_RATIO=0.2` in `values-prod.yaml`);
everywhere else samples all of them. The two records point at each other: a log
line from a traced request carries `trace_id` and `span_id`, and the server span
carries the request id as `cylist.request_id`. Health checks are never traced,
and a caller's `traceparent` is ignored — a trace starts here.

## When a deploy fails

**`permission denied ... /var/run/docker.sock`,** from a user who is in the
`docker` group — the runner's groups, not the user's. See *The runner*, and
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

**A migration failed, or the new pod never became ready** — Helm rolled the
release back, so the previous version is still serving. The schema stays
wherever the migration left it. `kubectl -n <namespace> logs job/<release>-migrate`
(or `kubectl get jobs`) shows why. Fix it and deploy again, or restore the dump
`deploy.sh` took moments earlier; see *Rolling back*. A dev slot's
database secret is put back as the previous release had it.

**`missing <dir>/app.env`** — `secrets.sh` found no env files for that
environment. See *Secrets*; for dev, *Dev → One-time setup*.

## Staging

Staging runs on the same cluster, namespace `cylist-staging`, from the
**`staging` branch** — served at <https://stage.cylist.in>, through the same
Cloudflare Tunnel as production and just as public, behind its own password
login.

It is deliberately production's shape, not development's: the same
`backend/Dockerfile` `runtime` image, the same chart, the same
`deploy/k3s/deploy.sh` — only the argument differs.

```bash
make staging-deploy     # build, migrate, roll out         (deploy/k3s/deploy.sh staging)
make staging-refresh    # reload it from production's data (deploy/k3s/staging-refresh.sh)
make staging-logs       # follow it
make staging-ps         # what is running
```

Staging is deployed when you ask for it, and not on every push. Pushing to
`staging` runs CI's test jobs and stops there; the deploy is
[`.github/workflows/deploy-staging.yml`](.github/workflows/deploy-staging.yml),
triggered by hand from the Actions tab (`workflow_dispatch`) on the same
self-hosted runner. Run it from the `staging` branch — from any other branch it
fails on its first step rather than putting that branch on staging, which is
what dev is for.

So the test result is still there to read before you trigger, on the commit the
push produced; what the workflow does not do is decide *when* staging moves.
A merge into `staging` leaves whatever is running there running.

### What differs from production, and why

| | production | staging |
| --- | --- | --- |
| namespace | `cylist-prod` | `cylist-staging` |
| values | `values-prod.yaml` | `values-staging.yaml` |
| served at | `cylist.in` | `stage.cylist.in` — both public, via the tunnel |
| secrets | `~/cylist-prod` | `~/cylist-staging` |
| `CYLIST_ENVIRONMENT` | `prod` | `staging` |
| priority class | `cylist-prod` | `cylist-staging`, evicted before production and monitoring |
| nightly backups kept | 14 | 7 |
| pushes | on | off |

**`CYLIST_ENVIRONMENT=staging`** is the one that is load-bearing. The app
treats `staging` and `prod` alike wherever being *deployed* is what matters —
`Settings.is_deployed`. It issues the session cookie `Secure`, because both are
reached over HTTPS; and `make seed` refuses to run, because staging holds real
rows. It also names the cookie `cylist_session_staging` rather than
production's `cylist_session`, so that signing in to one never signs you out of
the other (CYLIST-81).

> A `Secure` cookie is dropped by the browser over plain HTTP, and the symptom
> is a login that returns 200 and then does not stick. Reach staging through
> its HTTPS address.

### Staging holds production's data

`make staging-refresh` (or the *Refresh staging from production* workflow)
replaces staging's database *and* its uploaded blobs with a copy taken from
production at that moment — the two move together, or staging ends up with
file rows pointing at blobs it does not have. Production is only read:
`pg_dump`, and `tar` over its app pod's blobs mount. Staging comes back at
production's migration; *Deploy staging* migrates it forward.

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
  backups/k3s/   the dumps staging deploys leave behind
```

Both files `chmod 600`, as production's are. Serving it is the
`stage.cylist.in` ingress rule in the tunnel config — see *Serving it* — and
nothing per deploy.

## Dev

Dev is **five slots**, `dev_1` to `dev_5`, in namespace `cylist-dev` — so five
branches can be up side by side instead of taking turns. Slot N is the release
`cylist-N`, served at `https://devN.cylist.in/`, through the same Cloudflare
Tunnel as everything else. <https://dev.cylist.in/> is a small index of which
branch and commit is in each slot, when it went there, and which database it is
on.

The slots differ in the code they run, not in the data they show: all five use
**one Postgres** and **one blobs volume** (dev's `cylist-data` release), so a
project, card or file made on one slot is there on every other. See *One
database for every slot* below.

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
would fight over its release — while different slots never wait on each
other. Deploying into a slot replaces whatever branch was there; the index says
what that was.

It does not wait on CI's test jobs the way the `main` deploy does. That is
deliberate — dev exists so half-finished work can be looked at before it is
finished, not after it has already cleared the bar staging demands. (Staging's
own deploy does not wait on them either, but for a different reason: the push
to `staging` has already run them.)

```bash
make dev-deploy SLOT=2   # build, migrate, roll out      (deploy/k3s/deploy.sh dev 2)
make dev-logs SLOT=2     # follow it
make dev-ps SLOT=2       # what is running
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
entry flagged `is_agent`, which it looks up from `GET /api/v1/people`. It reads
`slot`, `url`, `branch` and `sha` from the deploy step's outputs, which
`deploy.sh` writes for a slot deploy.

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
| release | `cylist-staging/cylist` | `cylist-dev/cylist-N` |
| values | `values-staging.yaml` | `values-dev.yaml` + `values-dev-N.yaml` |
| served at | `stage.cylist.in` | `devN.cylist.in` |
| secrets | `~/cylist-staging` | `~/cylist-dev-shared`, the same for every slot |
| deploy trigger | manual, `staging` only | manual, any branch, pick a slot |
| data | copied from production | shared by all five slots, never restored from production; a copy of its own for a branch with unmerged migrations |
| `CYLIST_ENVIRONMENT` | `staging` | `preview` |
| `CYLIST_BASE_PATH` | unset | empty |

**A hostname each, no base path.** Each slot is served at the root of its own
hostname, exactly like production and staging, so `CYLIST_BASE_PATH` is empty.
The app still supports being served under a prefix — see
`backend/app/core/base_path.py` — but nothing deployed uses it.

**`CYLIST_ENVIRONMENT=preview`, not `dev`.** `CYLIST_ENVIRONMENT` already means
something in the app: the literal value `"dev"` is what `Settings` defaults to
on someone's own machine, where the session cookie is *not* `Secure` and
`make seed` is allowed to run — see `Settings.is_deployed` in
`backend/app/config.py`. Setting the real value to `"dev"` here would have
quietly picked up that machine's behavior on what is, in every way that
matters, a deployment: one container behind the tunnel, reached over HTTPS.
`"preview"` gets it into `is_deployed` alongside staging and production without
touching what `"dev"` means anywhere else.

**Production's jev key.** `secrets.sh dev` copies production's `JEV_API_KEY`
into dev's `cylist-app` Secret on every deploy — jev is a paid API, not
per-environment state, so a key that exists once is rotated once. Dev keeps its
own if production has none.

### What a slot deploy does beyond the usual

`deploy/k3s/deploy.sh dev N` runs the same build, secrets, dump, migrate, roll
out and health check as production and staging, with
[`scripts/dev-slot.sh`](scripts/dev-slot.sh) for the slot-specific helpers.
Around that it:

1. **Chooses the slot's database** — the shared one or a copy of its own; see
   below.
2. **Records what it deployed** on the slot's Deployment, as `cylist.in/branch`,
   `cylist.in/sha`, `cylist.in/deployed-at` and `cylist.in/database`
   annotations.
3. **Drops the slot's own database** once it is up on the shared one, if its
   branch no longer needs one.
4. **Regenerates the index**: reads those annotations from every slot's
   Deployment, writes the page into the `dev-index` ConfigMap, applies
   [`deploy/k3s/dev-index.yaml`](deploy/k3s/dev-index.yaml) (a small httpd
   Deployment, Service and Ingress for `dev.cylist.in`) and restarts it so the
   new page shows at once. That step holds a lock
   (`~/.cylist-dev.lock.k3s`), since the page is shared by all five slots.

### One-time setup

```
~/cylist-dev-shared/
  app.env        CYLIST_DATABASE_URL, CYLIST_PASSWORD_HASH, CYLIST_VAULT_KEY
  postgres.env   POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB
  backups/k3s/   the dumps slot deploys leave behind
```

Both files `chmod 600`, made by hand: a slot deploy fails on its secrets step
if either is missing. The database URL's host does not matter (it is rewritten
to the in-cluster Postgres); its password must match `postgres.env`. The shared
database starts empty — it is never restored from production, so unlike
staging's `app.env` it needs only its own `CYLIST_VAULT_KEY`, not production's.
The password hash is for opening the first account (`make hash-password`).

Serving is the `devN.cylist.in` and `dev.cylist.in` ingress rules in the
tunnel config — see *Serving it* — and nothing per deploy.

### One database for every slot

Every slot deploy dumps the shared database to `~/cylist-dev-shared/backups/k3s`
before it migrates anything. A migration is the one thing five branches cannot
share, so **the shared database only ever takes migrations that are already on
`main`**:

* **A branch with no migrations of its own** — every file under
  `backend/alembic/versions` is also on `origin/main`, byte for byte — runs on
  the shared database, and `alembic upgrade head` is run against it. That only
  ever applies migrations `main` has, so it can never take the shared database
  somewhere `main` cannot follow.
* **A branch with migrations `main` does not have** (new, changed or
  uncommitted) gets a database of its own on the same Postgres,
  `cylist_slot_N`. Each deploy drops it, restores the dump just taken of the
  shared one into it, and migrates that. The slot starts with everything the
  others show; its schema changes and whatever is done on it stay in the slot
  and are gone at its next deploy. Its `CYLIST_DATABASE_URL` is in the Secret
  `cylist-slot-N-db`, which `values-dev-N.yaml` lists in `app.extraSecrets`
  and which wins over the shared `cylist-app`.
* When a slot goes back to a branch with no migrations of its own, that Secret
  is deleted and, once the slot is up on the shared database, `cylist_slot_N`
  is dropped.

`scripts/dev-slot.sh` fetches `main` to compare against (set
`CYLIST_DEV_MAIN_BRANCH` to compare against another branch); if it cannot find
it at all, the slot gets a database of its own rather than risk the shared one.

A branch cut before `main`'s latest migration meets a database at a revision
it has never heard of. Dev's values set `app.migrations.tolerateUnknownRevision`,
so the migration Job lets the slot start on that schema unmigrated rather than
failing: an older branch on a newer schema mostly works, and dev is for
looking. Rebase the branch onto `main` to get its own migrations applied.

## Dashboard

A one-page status view of production, staging and the five dev slots, at
<https://dnu-home-1.tail222f46.ts.net:7443> — tailnet only, the one thing on
this machine that is not on the tunnel.

> **It predates K3s.** Its container state comes from `docker inspect` and its
> health from loopback ports the Docker stacks used, so since the cutover only
> its public-reachability column means anything. Grafana (see the observability
> README) is the status view for the cluster.

[`scripts/dashboard/server.py`](scripts/dashboard/server.py) is stdlib-only
Python: no dependencies to install, no build step. It answers `/` with a
static page and `/api/status` with, per environment, container state, health,
whether it is actually being served (a real request to its public hostname
through Cloudflare's edge, pinned with `curl --resolve` so a stale answer in the
host's own resolver cannot fake it), and the age of its newest backup.

It is not deployed by CI. `scripts/cylist-dashboard.service` runs it from
`~/cylist-dashboard`, deliberately *not* from a checkout: the self-hosted
runner's working directory can be pointed at any branch by the dev workflow,
and this service must not have its own code swapped out from under it because
someone deployed something unrelated.

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

On dnu-home-1, with a kubeconfig that reaches the cluster (see
[`deploy/k3s/README.md`](deploy/k3s/README.md)):

```bash
cd ~/actions-runner/_work/cylist/cylist   # or any checkout
git pull
make deploy                               # deploy/k3s/deploy.sh
```

`make prod-logs` follows the app, `make prod-ps` says what is running.

## Backups and rolling back

There are two kinds of dump, both on this machine's disk:

- **Pre-deploy dumps**, database only, taken by every deploy before it
  migrates: `<secrets dir>/backups/k3s/cylist-<stamp>.dump`, the newest ten.
- **Nightly backups**, database *and* uploaded blobs, made by the chart's
  `cylist-data-backup` CronJob at 03:30 IST into the `cylist-data-backups`
  volume in each namespace: 14 kept in production, 7 in staging and dev. Each
  is a directory holding `database.dump` and `data.tar.gz`, laid out as
  `scripts/backup.sh` lays it out. The `BackupStale` and `BackupJobFailed`
  alerts watch it; `kubectl -n <namespace> create job
  --from=cronjob/cylist-data-backup backup-manual` runs one now.

Neither includes `CYLIST_VAULT_KEY`, and neither leaves the box: a lost disk
loses them with the data.

Code rolls back by deploying an older commit — check it out and run
`make deploy` — or with `helm -n cylist-prod rollback cylist`, which never
touches Postgres or the blobs. Schema does not roll back, which is what the
pre-migration dump is for:

```bash
kubectl -n cylist-prod scale deploy/cylist --replicas=0
kubectl -n cylist-prod exec -i cylist-data-postgres-0 -c postgres -- \
  sh -c 'pg_restore --clean --if-exists --no-owner -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < ~/cylist-prod/backups/k3s/cylist-<stamp>.dump
kubectl -n cylist-prod scale deploy/cylist --replicas=1
```

`make backup` runs `scripts/backup.sh`, for a database you can reach from this
shell — your own development one, say. It takes the uploaded files as well as
the database.
