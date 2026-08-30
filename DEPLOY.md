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
`~/actions-runner` and is run by systemd — as a **user** service rather than a
system one, so nothing about it needs root:

```bash
systemctl --user status github-runner-cylist
journalctl --user -u github-runner-cylist -f
```

`loginctl enable-linger dnu2` is what keeps it running when nobody is logged in,
and across reboots. The runner works as `dnu2`, who is in the `docker` group —
which is what lets it build without privilege.

To re-register it (a new repository, or a revoked token):

```bash
gh api repos/dhruva-nu/cylist/actions/runners/registration-token -q .token
cd ~/actions-runner && ./config.sh --unattended --replace \
  --url https://github.com/dhruva-nu/cylist --token <token> \
  --name dnu-home-1 --labels cylist --work _work
```

### Serving it

```bash
sudo tailscale serve --bg --https 443 http://127.0.0.1:8000
sudo tailscale funnel --bg 443
```

`serve` terminates HTTPS and proxies to the app on loopback, which is the only
thing that reaches it. `funnel` then puts that same URL on the public internet,
with the app's own password login as the only gate — which is why production
sets `CYLIST_ENVIRONMENT=prod`, so the session cookie is issued `Secure`.

Both need root unless you run `sudo tailscale set --operator=$USER` once, after
which they do not. `tailscale serve status` shows where it currently points, and
`sudo tailscale funnel --bg off` takes it back off the public internet without
disturbing the tailnet.

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
