#!/usr/bin/env bash
# Decides which of CI's suites a run needs, for the `changes` job in
# .github/workflows/ci.yml. Reads the event from the environment that job sets
# and writes one output per suite to $GITHUB_OUTPUT (stdout when unset, so it
# can be tried by hand):
#
#   backend, installers, frontend,  'true' or 'false'
#   android
#   agents                          JSON matrix `include` list for the CLI and
#                                   MCP server legs; '[]' when neither changed
#
# Everything runs when the work is going to `main` (a pull request into it, or
# the push that deploys), when this script or the workflow changed, and when
# the diff cannot be worked out. Otherwise a suite runs only if a path it reads
# is in the diff.
set -euo pipefail

out="${GITHUB_OUTPUT:-/dev/stdout}"

all=false
reason=""
files=""

if [[ "${EVENT:-}" == "pull_request" && "${PR_BASE_REF:-}" == "main" ]]; then
  all=true; reason="pull request into main"
elif [[ "${EVENT:-}" == "push" && "${REF:-}" == "refs/heads/main" ]]; then
  all=true; reason="push to main"
else
  if [[ "${EVENT:-}" == "pull_request" ]]; then
    base="${PR_BASE_SHA:-}"; head="${PR_HEAD_SHA:-}"
  else
    base="${PUSH_BEFORE:-}"; head="${PUSH_SHA:-}"
  fi
  # A new branch's first push has no `before` (all zeros); a force push may
  # name a commit the clone does not have. Neither is a diff to trust.
  if [[ -z "$base" || -z "$head" || "$base" =~ ^0+$ ]] \
    || ! files="$(git diff --name-only "$base" "$head" 2>/dev/null)"; then
    all=true; reason="no diff to compare against"
  fi
fi

changed() {
  # True when any changed path starts with one of the given prefixes.
  [[ "$all" == true ]] && return 0
  local prefix
  for prefix in "$@"; do
    grep -q -- "^${prefix}" <<<"$files" && return 0
  done
  return 1
}

if [[ "$all" == false ]] && changed .github/workflows/ci.yml scripts/ci-changes.sh; then
  all=true; reason="CI itself changed"
fi

flag() { if changed "$@"; then echo true; else echo false; fi; }

# The backend depends on the MCP server and the vendored jev packages by path
# (backend/pyproject.toml), so a change to either is a backend change too. The
# backend job also checks android/api/openapi.json against the API, so an edit
# to that file reruns it.
backend=$(flag backend/ mcp/ vendor/ android/api/openapi.json)
cli=$(flag cli/)
mcp=$(flag mcp/)
installers=$(flag scripts/install.sh scripts/install.ps1)
frontend=$(flag frontend/)
# The app's theme is checked against the web app's tokens (ThemeTokensTest), so
# a change to those is an Android change too.
android=$(flag android/ frontend/src/theme/tokens.css)

legs=()
if [[ "$cli" == true ]]; then
  legs+=('{"os":"ubuntu-latest","package":{"name":"CLI","dir":"cli"}}')
  legs+=('{"os":"windows-latest","package":{"name":"CLI","dir":"cli"}}')
fi
if [[ "$mcp" == true ]]; then
  legs+=('{"os":"ubuntu-latest","package":{"name":"MCP server","dir":"mcp"}}')
fi
agents="[$(IFS=,; echo "${legs[*]}")]"

{
  echo "backend=$backend"
  echo "installers=$installers"
  echo "frontend=$frontend"
  echo "android=$android"
  echo "agents=$agents"
} >>"$out"

if [[ "$all" == true ]]; then
  echo "Running every suite: $reason." >&2
else
  printf 'Changed files:\n%s\n' "$files" >&2
  echo "backend=$backend cli=$cli mcp=$mcp installers=$installers frontend=$frontend android=$android" >&2
fi
