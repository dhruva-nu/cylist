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

Push to `staging` and CI does the deploy for you, on the same self-hosted
runner, gated on the same three test jobs.

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
