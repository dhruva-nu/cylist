# What `scripts/deploy.sh dev N` does that production and staging do not.
# Sourced by deploy.sh, never run on its own; it relies on SLOT, PORT,
# SECRETS_DIR, step and warn from there.
#
# Dev is five slots rather than one stack, so that five branches can be looked
# at side by side. Slot N is its own compose project (cylist-dev-N), with its own
# image tag, port 801N, secrets in ~/cylist-dev-N and an empty database. All
# five share one HTTPS port, :9443, and are told apart by path:
#
#   https://<host>:9443/          an index: which branch and commit is in each slot
#   https://<host>:9443/dev_N/    slot N — `tailscale serve --set-path /dev_N`,
#                                 which strips the prefix before proxying to :801N
#
# Around deploy.sh's ordinary build-migrate-restart, a slot deploy:
#
#   before   creates the slot's secrets if it has none, and copies production's
#            jev key into them (dev_slot_prepare)
#   after    writes what it deployed into the slot directory, retires the old
#            single dev stack, regenerates the index, points tailscale at the
#            slot and checks the slot answers through it (dev_slot_publish)
#
# shellcheck shell=bash

DEV_ROOT="${CYLIST_DEV_ROOT:-$HOME}"
DEV_HOST="${CYLIST_DEV_HOST:-dnu-home-1.tail222f46.ts.net}"
DEV_HTTPS_PORT=9443
DEV_SLOTS=(1 2 3 4 5)
# Only index.html lives here, because tailscale serves the whole directory. The
# slot directories hold secrets and must never be what it serves.
DEV_INDEX_DIR="${CYLIST_DEV_INDEX_DIR:-$DEV_ROOT/cylist-dev-index}"
DEV_LOCK="$DEV_ROOT/.cylist-dev.lock"
DEV_REPO_URL="${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY:-dhruva-nu/cylist}"

dev_slot_dir() { printf '%s/cylist-dev-%s' "$DEV_ROOT" "$1"; }
dev_slot_url() { printf 'https://%s:%s/dev_%s/' "$DEV_HOST" "$DEV_HTTPS_PORT" "$1"; }

# --- Before the build ---------------------------------------------------------

dev_slot_prepare() {
  _dev_slot_provision
  _dev_slot_jev_key
}

# A slot that has never been deployed has no secrets yet, and a dev slot's are
# nothing anyone needs to choose: an empty database behind no published port,
# and a vault with nothing in it. So the first deploy makes them, rather than
# five directories having to be made by hand on the server. Files that already
# exist are never touched — an operator's own app.env wins.
#
# The one value that cannot be made up is the bootstrap password hash, since
# somebody has to know the password. It is copied from the retired single dev
# stack's app.env, whose password whoever used dev already knows.
_dev_slot_provision() {
  local dir="$SECRETS_DIR"
  [[ -e "$dir/app.env" || -e "$dir/postgres.env" ]] && return 0

  local template="${CYLIST_DEV_TEMPLATE:-$DEV_ROOT/cylist-dev/app.env}"
  local password vault_key
  password="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  # What `make vault-key` prints: 32 random bytes, URL-safe base64.
  vault_key="$(head -c 32 /dev/urandom | base64 | tr '+/' '-_')"

  step "Creating slot $SLOT's secrets in $dir"
  (
    umask 077
    mkdir -p "$dir"
    printf 'POSTGRES_USER=cylist\nPOSTGRES_PASSWORD=%s\nPOSTGRES_DB=cylist\n' \
      "$password" > "$dir/postgres.env"
    {
      printf 'CYLIST_DATABASE_URL=postgresql+asyncpg://cylist:%s@postgres:5432/cylist\n' "$password"
      printf 'CYLIST_VAULT_KEY=%s\n' "$vault_key"
      grep -m1 '^CYLIST_PASSWORD_HASH=.' "$template" 2>/dev/null || true
    } > "$dir/app.env"
  )
  if grep -q '^CYLIST_PASSWORD_HASH=.' "$dir/app.env"; then
    echo "   new database password and vault key; bootstrap password hash from $template"
  else
    warn "dev slot $SLOT has no CYLIST_PASSWORD_HASH, so nobody can sign in to open its first account. Add one to $dir/app.env (make hash-password) and deploy again."
  fi
}

# Dev asks jev with production's key rather than one of its own: jev is a paid
# API, not per-environment state, and a key that exists once is a key that is
# rotated once. Copied across on every deploy, so a slot follows a rotation, and
# never printed — it goes file to file on this machine.
_dev_slot_jev_key() {
  local dev="$SECRETS_DIR/app.env"
  local prod="${CYLIST_PROD_DIR:-$HOME/cylist-prod}/app.env"
  local key_line='^(CYLIST_)?JEV_API_KEY=.'
  # No app.env means half a slot directory; deploy.sh's own check says so next.
  [[ -f "$dev" ]] || return 0
  if ! grep -qE "$key_line" "$prod" 2>/dev/null; then
    warn "production has no JEV_API_KEY, so dev slot $SLOT keeps its own (if any)"
    return 0
  fi
  (
    umask 077
    { grep -vE '^(CYLIST_)?JEV_API_KEY=' "$dev" || true; grep -m1 -E "$key_line" "$prod"; } \
      > "$dev.new"
    mv "$dev.new" "$dev"
  )
  echo "   dev slot $SLOT uses production's jev key"
}

# --- After the app is healthy ---------------------------------------------------

dev_slot_publish() {
  local branch sha deployed_at
  branch="${CYLIST_DEPLOY_BRANCH:-${GITHUB_REF_NAME:-$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)}}"
  sha="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
  deployed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  step "Recording what is in slot $SLOT"
  # One `key=value` per line, so a shell (or the index below) can read it back
  # with sed and nothing else.
  printf 'branch=%s\nsha=%s\ntime=%s\n' "$branch" "$sha" "$deployed_at" \
    > "$SECRETS_DIR/deployed.new"
  mv "$SECRETS_DIR/deployed.new" "$SECRETS_DIR/deployed"
  echo "   $branch @ ${sha:0:12}, $deployed_at"

  # Two slots can deploy at once (by hand, or two runners), and the index and
  # tailscale's serve config are each one thing shared by all five: read,
  # changed, written. Serialise just that part.
  if command -v flock > /dev/null; then
    (flock 9; _dev_slot_publish_shared) 9> "$DEV_LOCK"
  else
    _dev_slot_publish_shared
  fi

  _dev_slot_check_served

  # For later steps of the workflow — a step that tells a card where its branch
  # went reads these rather than working the URL out again.
  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    {
      echo "slot=$SLOT"
      echo "url=$(dev_slot_url "$SLOT")"
      echo "branch=$branch"
      echo "sha=$sha"
    } >> "$GITHUB_OUTPUT"
  fi
}

_dev_slot_publish_shared() {
  _dev_retire_single_stack
  step "Regenerating the index at https://$DEV_HOST:$DEV_HTTPS_PORT/"
  dev_index_write
  echo "   $DEV_INDEX_DIR/index.html"
  _dev_slot_serve
}

# Before the slots there was one dev stack: project cylist-dev, :8002, served
# at the root of :9443. Its containers go the first time any slot deploys, and
# every later deploy finds nothing to do. Its volumes and ~/cylist-dev are left
# alone — see DEPLOY.md for removing them once nothing in them is wanted. Its
# serve mapping needs no step of its own: it was the `/` handler on :9443, which
# the index replaces.
_dev_retire_single_stack() {
  local ids
  ids="$(docker ps -aq --filter label=com.docker.compose.project=cylist-dev)"
  if [[ -n "$ids" ]]; then
    step "Retiring the single dev stack (cylist-dev, :8002)"
    # shellcheck disable=SC2086  # one id per word, on purpose
    docker rm --force $ids > /dev/null
    docker network rm cylist-dev_default > /dev/null 2>&1 || true
    echo "   containers removed; its volumes and $DEV_ROOT/cylist-dev are kept"
  fi
  docker image rm cylist:dev > /dev/null 2>&1 || true
}

# tailscale wants root to change serve config unless this user was made its
# operator (`sudo tailscale set --operator=$USER`, once). Try as ourselves, then
# a sudo that may not prompt, and say which one-liner fixes it if neither works.
_dev_ts() {
  tailscale "$@" > /dev/null && return 0
  command -v sudo > /dev/null && sudo -n tailscale "$@" > /dev/null 2>&1
}

_dev_slot_serve() {
  step "Serving slot $SLOT at $(dev_slot_url "$SLOT")"
  # Re-applying a mapping that is already there is a no-op, so this runs on
  # every deploy. --yes, where this tailscale has it, so nothing waits on a
  # prompt nobody will answer.
  local yes=()
  if tailscale serve --help 2>&1 | grep -q -- '--yes'; then yes=(--yes); fi
  if ! _dev_ts serve --bg "${yes[@]}" --https "$DEV_HTTPS_PORT" \
         --set-path "/dev_$SLOT" "http://127.0.0.1:$PORT" ||
     ! _dev_ts serve --bg "${yes[@]}" --https "$DEV_HTTPS_PORT" "$DEV_INDEX_DIR"; then
    echo "   tailscale would not change its serve config. Once, on this machine:" >&2
    echo "     sudo tailscale set --operator=$(id -un)" >&2
    echo "   and deploy again. The slot itself is up on 127.0.0.1:$PORT." >&2
    exit 1
  fi
  echo "   :$DEV_HTTPS_PORT/dev_$SLOT → 127.0.0.1:$PORT, :$DEV_HTTPS_PORT/ → $DEV_INDEX_DIR"
}

# The loopback health check has already passed; this one goes the way a browser
# does, through tailscale and the prefix, so a slot that is up but not served
# fails the deploy instead of passing it.
_dev_slot_check_served() {
  local url
  url="$(dev_slot_url "$SLOT")api/v1/health"
  step "Checking it is served"
  for attempt in $(seq 1 15); do
    if curl --fail --silent --show-error --max-time 10 "$url" > /dev/null; then
      echo "   $url is answering"
      return 0
    fi
    if [[ $attempt -eq 15 ]]; then
      echo "   $url never answered. tailscale serve status:" >&2
      tailscale serve status >&2 || true
      exit 1
    fi
    sleep 2
  done
}

# --- The index at :9443/ ------------------------------------------------------

# A static page, rewritten on every slot deploy from the five slot directories'
# `deployed` files — so it is only ever as stale as the last deploy, and needs
# no process of its own to serve it.
dev_index_write() {
  mkdir -p "$DEV_INDEX_DIR"
  local tmp="$DEV_INDEX_DIR/.index.html.$$" rows="" n file branch sha deployed_at commit
  for n in "${DEV_SLOTS[@]}"; do
    file="$(dev_slot_dir "$n")/deployed"
    if [[ -f "$file" ]]; then
      branch="$(sed -n 's/^branch=//p' "$file" | head -n1)"
      sha="$(sed -n 's/^sha=//p' "$file" | head -n1)"
      deployed_at="$(sed -n 's/^time=//p' "$file" | head -n1)"
      if [[ "$sha" =~ ^[0-9a-f]{40}$ ]]; then
        commit="<a href=\"$(_html "$DEV_REPO_URL")/commit/$sha\"><code>${sha:0:12}</code></a>"
      else
        commit="<code>$(_html "$sha")</code>"
      fi
      rows+="<tr><th><a href=\"/dev_$n/\">dev_$n</a></th><td><code>$(_html "$branch")</code></td><td>$commit</td><td><time>$(_html "$deployed_at")</time></td></tr>"$'\n'
    else
      rows+="<tr class=\"empty\"><th>dev_$n</th><td colspan=\"3\">empty</td></tr>"$'\n'
    fi
  done

  cat > "$tmp" << EOF
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cylist dev slots</title>
<style>
  :root { --bg: #fff; --fg: #1b1b1b; --muted: #6b6b6b; --line: #e4e4e4; --link: #1d6fd1; }
  @media (prefers-color-scheme: dark) {
    :root { --bg: #161616; --fg: #e8e8e8; --muted: #8f8f8f; --line: #2c2c2c; --link: #6aa8ff; }
  }
  body { background: var(--bg); color: var(--fg); margin: 0; padding: 32px 16px;
         font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
  main { max-width: 760px; margin: 0 auto; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  p { color: var(--muted); margin: 0 0 20px; }
  .wrap { overflow-x: auto; }
  table { border-collapse: collapse; width: 100%; }
  th, td { text-align: left; padding: 10px 12px 10px 0; border-top: 1px solid var(--line);
           vertical-align: top; white-space: nowrap; }
  thead th { color: var(--muted); font-weight: 500; font-size: 13px; border-top: 0; }
  td:nth-child(2) { white-space: normal; word-break: break-all; }
  a { color: var(--link); text-decoration: none; }
  a:hover { text-decoration: underline; }
  code { font: 13px ui-monospace, SFMono-Regular, Menlo, monospace; }
  .empty, .empty th { color: var(--muted); font-weight: 400; }
</style>
</head>
<body>
<main>
<h1>Cylist dev slots</h1>
<p>What each slot on dnu-home-1 is running. Deploy one with the <em>Deploy dev</em> workflow, picking the branch and the slot. Updated $(_html "$(date -u +%Y-%m-%dT%H:%M:%SZ)").</p>
<div class="wrap">
<table>
<thead><tr><th>Slot</th><th>Branch</th><th>Commit</th><th>Deployed (UTC)</th></tr></thead>
<tbody>
$rows</tbody>
</table>
</div>
</main>
</body>
</html>
EOF
  chmod 644 "$tmp"
  mv "$tmp" "$DEV_INDEX_DIR/index.html"
}

# Branch names are anyone's text; nothing of theirs reaches the page unescaped.
# The replacements are quoted so bash 5.2's patsub_replacement leaves `&` alone.
_html() {
  local s="$1"
  s="${s//'&'/'&amp;'}"
  s="${s//'<'/'&lt;'}"
  s="${s//'>'/'&gt;'}"
  s="${s//'"'/'&quot;'}"
  printf '%s' "$s"
}
