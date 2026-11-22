# What `deploy/k3s/deploy.sh dev N` needs that production and staging do not.
# Sourced by deploy/k3s/deploy.sh, never run on its own; it relies on warn from
# there.
#
# Dev is five slots rather than one environment, so that five branches can be
# looked at side by side. Slot N is its own Helm release (cylist-N) in the
# cylist-dev namespace. All five share one database and one set of secrets
# (~/cylist-dev-shared), so they show the same data. Each has a hostname of its
# own on the Cloudflare Tunnel that also carries production (see DEPLOY.md):
#
#   https://dev.cylist.in/        an index: which branch and commit is in each slot
#   https://devN.cylist.in/       slot N
#
# What lives here is what deploy.sh asks of a slot: the URLs, the name of a
# slot's own database, the check for migrations main does not have, and the
# index page it regenerates after every slot deploy.
#
# shellcheck shell=bash

DEV_ROOT="${CYLIST_DEV_ROOT:-$HOME}"
# The domain the slots hang off: slot N is https://devN.$DEV_DOMAIN/ and the
# index https://dev.$DEV_DOMAIN/.
DEV_DOMAIN="${CYLIST_DEV_DOMAIN:-cylist.in}"
DEV_SLOTS=(1 2 3 4 5)
# deploy.sh serialises the index update on "$DEV_LOCK.k3s".
DEV_LOCK="$DEV_ROOT/.cylist-dev.lock"
# The shared database only ever takes migrations that are on this branch of
# origin; see _dev_unmerged_migrations.
DEV_MAIN_BRANCH="${CYLIST_DEV_MAIN_BRANCH:-main}"
DEV_MIGRATIONS=backend/alembic/versions
DEV_REPO_URL="${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY:-dhruva-nu/cylist}"

dev_slot_url() { printf 'https://dev%s.%s/' "$1" "$DEV_DOMAIN"; }
dev_index_url() { printf 'https://dev.%s/' "$DEV_DOMAIN"; }
# A slot's own database, when it has one. deploy.sh points the slot at it
# through the cylist-slot-N-db secret; with no secret the slot is on the shared
# database.
dev_slot_db_name() { printf 'cylist_slot_%s' "$1"; }

# --- The database ---------------------------------------------------------------

# Five branches share one database, and a migration is not something they can
# share. A branch's unmerged migration would change the schema under the other
# four, two branches whose migrations fork from the same parent could not both
# be applied, and a migration that never reached main would leave the database
# at a revision main has never heard of — after which no deploy of main could
# migrate it again until somebody repaired it by hand.
#
# So the shared database only ever takes migrations that are already on main.
# A branch with migrations of its own (any file under alembic/versions that main
# does not have, or has differently) gets a database of its own instead,
# cylist_slot_N, made afresh on every deploy from the dump of the shared one
# deploy.sh has just taken, and then migrated. It starts with everything the
# other slots show; what it changes, schema or data, stays in the slot and is
# gone at its next deploy. A slot whose branch has no migrations of its own goes
# back to the shared database, and its copy is dropped once it is up.

# The files under alembic/versions that this tree has and origin/main does not,
# one per line — new, changed or renamed here, or not yet committed. Nothing
# when the branch brings no migrations of its own, which includes a branch
# that is merely behind main. Fails if there is no origin/main to compare with.
_dev_unmerged_migrations() {
  local main="origin/$DEV_MAIN_BRANCH" depth=()
  # actions/checkout fetches the one commit it deploys, so main has to be
  # fetched too. Only as shallow as the checkout already is: a full clone run
  # by hand must not be turned into a shallow one.
  [[ "$(git rev-parse --is-shallow-repository 2> /dev/null)" == true ]] && depth=(--depth=1)
  git fetch --quiet --no-tags "${depth[@]}" origin \
    "+refs/heads/$DEV_MAIN_BRANCH:refs/remotes/$main" 2> /dev/null ||
    warn "could not fetch $DEV_MAIN_BRANCH; comparing migrations with the $main already here"
  git rev-parse --verify --quiet "$main^{commit}" > /dev/null || return 1
  {
    git diff --name-only --diff-filter=ACMRT "$main" -- "$DEV_MIGRATIONS"
    git ls-files --others --exclude-standard -- "$DEV_MIGRATIONS"
  } | sort -u
}

# --- The index at dev.<domain>/ ------------------------------------------------

# A static page, rewritten on every slot deploy from the five slots' Deployment
# annotations (deploy/k3s/deploy.sh) — so it is only ever as stale as the last
# deploy.

# One slot's row of the index: slot, then branch, sha, time and database, or
# the slot alone when it is empty.
dev_index_row() {
  local n="$1" branch="${2:-}" sha="${3:-}" deployed_at="${4:-}" database="${5:-}" commit
  if [[ -z "$branch$sha" ]]; then
    printf '<tr class="empty"><th>dev_%s</th><td colspan="4">empty</td></tr>' "$n"
    return 0
  fi
  # A slot deployed before slots could have their own was on the shared one.
  if [[ "$database" == own ]]; then database="its own"; else database=shared; fi
  if [[ "$sha" =~ ^[0-9a-f]{40}$ ]]; then
    commit="<a href=\"$(_html "$DEV_REPO_URL")/commit/$sha\"><code>${sha:0:12}</code></a>"
  else
    commit="<code>$(_html "$sha")</code>"
  fi
  printf '<tr><th><a href="%s">dev_%s</a></th><td><code>%s</code></td><td>%s</td><td><time>%s</time></td><td>%s</td></tr>' \
    "$(dev_slot_url "$n")" "$n" "$(_html "$branch")" "$commit" "$(_html "$deployed_at")" "$database"
}

# The whole index page around the rows, on stdout.
dev_index_page() {
  local rows="$1"
  cat << EOF
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
<thead><tr><th>Slot</th><th>Branch</th><th>Commit</th><th>Deployed (UTC)</th><th>Database</th></tr></thead>
<tbody>
$rows</tbody>
</table>
</div>
</main>
</body>
</html>
EOF
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
