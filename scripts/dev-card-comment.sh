#!/usr/bin/env bash
# Tell a Cylist card which dev slot its branch went to. Run by the Deploy dev
# workflow after a successful deploy, when it was given a `card`:
#
#   Deployed to dev_2 — https://dnu-home-1.tail222f46.ts.net:9443/dev_2/ — branch feature/x @ 0123456789ab
#
# That line is the same one the deploy-dev skill (docs/skills/deploy-dev) has an
# agent post through MCP, so a deploy started by hand from GitHub leaves the
# same record on the card as one an agent started. The agent looks for it
# before posting its own, which is why the wording here and there must match.
#
# Everything comes in through the environment:
#
#   CARD                 the card, e.g. CYLIST-79
#   SLOT URL BRANCH SHA  the deploy step's outputs (scripts/dev-slot.sh)
#   CYLIST_AGENT_TOKEN   a production API token (read + write); unset means skip
#   CYLIST_URL           production, default https://dnu-home-1.tail222f46.ts.net
#
# The deploy has already happened by the time this runs, so nothing here fails
# the run: a missing token, an unknown card or production being down is a
# ::warning:: on the run and the agent's own comment is the fallback. The one
# exit 1 is `check`, which runs before the deploy, on a malformed card.
#
#   scripts/dev-card-comment.sh check   validate CARD; nothing is sent
#   scripts/dev-card-comment.sh post    post the comment
set -euo pipefail

CARD_PATTERN='^[A-Z][A-Z0-9]*-[0-9]+$'

check_card() {
  if [[ ! "${CARD:-}" =~ $CARD_PATTERN ]]; then
    echo "::error::card must be a card reference like CYLIST-79, not '${CARD:-}'."
    exit 1
  fi
}

# A JSON string, for text that is not ours: a branch may hold a double quote.
# Git refuses backslashes and control characters in a ref, so escaping the two
# characters JSON cares about and dropping any control character is complete.
json_string() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="$(printf '%s' "$s" | tr -d '\000-\037')"
  printf '"%s"' "$s"
}

comment_text() {
  printf 'Deployed to dev_%s — %s — branch %s @ %s' "$SLOT" "$URL" "$BRANCH" "${SHA:0:12}"
}

post() {
  check_card
  if [[ -z "${CYLIST_AGENT_TOKEN:-}" ]]; then
    echo "::warning::No CYLIST_AGENT_TOKEN secret, so $CARD was not told it is on dev_$SLOT. Post this on it by hand: $(comment_text)"
    return 0
  fi

  local base="${CYLIST_URL:-https://cylist.in}"
  local work agent_id status
  work="$(mktemp -d)"
  # shellcheck disable=SC2064  # expanded now: $work is local, gone by EXIT
  trap "rm -rf '$work'" EXIT
  # The token goes to curl in a config file, so it is in no process's
  # argument list and in nothing this step prints.
  printf 'header = "Authorization: Bearer %s"\n' "$CYLIST_AGENT_TOKEN" > "$work/auth"
  chmod 600 "$work/auth"

  # A token acts as whoever minted it, and a comment with no author reads
  # "Unattributed" on the card. So it is written as the board's Agent — the
  # directory entry flagged is_agent, which every project has as a member —
  # found by the flag, as Cylist itself finds it, since the name is editable.
  # PersonRead is flat, so no brace sits between an entry's id and its flag.
  agent_id=""
  if status="$(_api GET /api/v1/people "$work/people")" && [[ "$status" == 200 ]]; then
    agent_id="$(grep -o '{"id":"[0-9a-f-]*"[^{}]*"is_agent":true' "$work/people" |
      head -n1 | sed 's/^{"id":"\([0-9a-f-]*\)".*/\1/' || true)"
  fi

  local text
  text="$(json_string "$(comment_text)")"
  if [[ -n "$agent_id" ]]; then
    printf '{"body":%s,"author_id":"%s"}' "$text" "$agent_id" > "$work/body"
  else
    printf '{"body":%s}' "$text" > "$work/body"
  fi

  if ! status="$(_api POST "/api/v1/tasks/$CARD/comments" "$work/response" "$work/body")"; then
    echo "::warning::Could not reach $base to comment on $CARD; the deploy itself succeeded."
    return 0
  fi
  if [[ "$status" == 201 ]]; then
    echo "Commented on $CARD${agent_id:+ as Agent}: $(comment_text)"
  else
    # The response is the API's own error ({"detail": …}); it never holds the
    # token, and it says why (401 revoked, 403 no comments permission, 404 no
    # such card).
    echo "::warning::Commenting on $CARD failed with HTTP $status: $(head -c 500 "$work/response")"
  fi
}

# _api METHOD PATH OUT [BODY] — prints the HTTP status; non-zero only when
# production could not be reached at all.
_api() {
  local data=()
  [[ -n "${4:-}" ]] && data=(--header 'Content-Type: application/json' --data-binary "@$4")
  curl --config "$work/auth" --silent --show-error --max-time 30 \
    --request "$1" "$base$2" "${data[@]}" \
    --output "$3" --write-out '%{http_code}'
}

case "${1:-}" in
  check) check_card ;;
  post) post ;;
  *)
    echo "usage: $0 check|post" >&2
    exit 2
    ;;
esac
