#!/bin/sh
# Let this machine's agents use a Cylist board, from nothing, in one command.
#
#   curl -fsSL https://raw.githubusercontent.com/dhruva-nu/cylist/main/scripts/install.sh \
#     | sh -s -- --url https://cylist.example.ts.net
#
# Linux and macOS; scripts/install.ps1 is the same thing for Windows. What it
# does is install `uv` if it is missing, install the `cylist` CLI and the
# `cylist-mcp` server from the repository, and then hand over to `cylist
# setup`, which does the part that needs a password.
#
# Three things are worth knowing about how it is written.
#
# **It is POSIX sh, not bash.** The machine this has to work on is somebody
# else's, and `/bin/sh` is dash on Debian and Ubuntu. A bashism here would
# fail on the exact machines this exists to serve.
#
# **It reopens stdin from the terminal.** Piped into `sh`, this script *is*
# stdin, so the password prompt would read the rest of the script rather than
# what the person typed. `cylist setup` prompts through /dev/tty on its own,
# but the reopen makes every other prompt behave too.
#
# **It installs nothing it cannot name.** No `sudo`, no system package
# manager, nothing outside `~/.local`. A one-line installer that needs root
# is a one-line installer nobody should run.

set -eu

REPO="https://github.com/dhruva-nu/cylist.git"
REF="main"
URL=""

usage() {
    cat <<'EOF'
Usage: install.sh [--url URL] [--ref REF] [--repo URL] [-- SETUP ARGS...]

  --url URL    The Cylist server to set this machine up against. Without it,
               cylist setup looks for http://localhost:8000.
  --ref REF    Branch or tag to install from. Default main.
  --repo URL   Install from a fork rather than the upstream repository.

Anything after -- is passed to `cylist setup` (see `cylist setup --help`).
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --url) URL="${2:?--url needs a value}"; shift 2 ;;
        --url=*) URL="${1#--url=}"; shift ;;
        --ref) REF="${2:?--ref needs a value}"; shift 2 ;;
        --ref=*) REF="${1#--ref=}"; shift ;;
        --repo) REPO="${2:?--repo needs a value}"; shift 2 ;;
        --repo=*) REPO="${1#--repo=}"; shift ;;
        -h|--help) usage; exit 0 ;;
        --) shift; break ;;
        *) echo "install.sh: unknown option $1" >&2; usage >&2; exit 2 ;;
    esac
done

say() { printf '\033[1m==>\033[0m %s\n' "$1" >&2; }
die() { printf 'install.sh: %s\n' "$1" >&2; exit 1; }

# --- uv ---------------------------------------------------------------------

# Asked before anything is put in front of it: a uv already on PATH is the one
# the person uses, and prepending ~/.local/bin unconditionally would silently
# prefer a stale copy there over the /usr/bin one they installed on purpose.
if ! command -v uv >/dev/null 2>&1; then
    say "Installing uv (astral.sh), into ~/.local — no sudo, nothing system-wide."
    if command -v curl >/dev/null 2>&1; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- https://astral.sh/uv/install.sh | sh
    else
        die "neither curl nor wget is installed, so uv cannot be fetched."
    fi
    # uv writes this to put itself on PATH; sourcing it saves reopening a shell.
    if [ -f "$HOME/.local/bin/env" ]; then
        # Written by uv's installer a moment ago, so there is nothing on disk
        # for shellcheck to follow when it reads this.
        # shellcheck disable=SC1091
        . "$HOME/.local/bin/env"
    else
        PATH="$HOME/.local/bin:$PATH"
        export PATH
    fi
    command -v uv >/dev/null 2>&1 || die "uv installed but is not on PATH."
fi

# --- The CLI and the MCP server --------------------------------------------

# Both from the same ref, so a machine bootstrapped from one branch does not
# end up with an MCP server from another. --force because this script is safe
# to run again, and a half-finished earlier attempt must not be what stops it.
say "Installing cylist and cylist-mcp from $REPO@$REF."
uv tool install --force "git+$REPO@$REF#subdirectory=cli"
uv tool install --force "git+$REPO@$REF#subdirectory=mcp"

# Where uv just put them, asked rather than assumed — and only added to PATH
# for the rest of this script. uv puts it on the user's PATH itself, for the
# shells that come after this one.
if ! command -v cylist >/dev/null 2>&1; then
    installed_bin="$(uv tool dir --bin 2>/dev/null || echo "$HOME/.local/bin")"
    PATH="$installed_bin:$PATH"
    export PATH
fi
command -v cylist >/dev/null 2>&1 || die "cylist installed but is not on PATH."

# Read by `cylist setup` if it ever has to install the MCP server itself —
# on a later run, after an upgrade removed it. Same ref as everything else.
CYLIST_MCP_SOURCE="git+$REPO@$REF#subdirectory=mcp"
export CYLIST_MCP_SOURCE

# --- Setting up -------------------------------------------------------------

# Piped into `sh`, stdin is this script. Anything that prompts would read the
# rest of it as the answer. Skipped when the caller has said the password is
# coming down the pipe on purpose.
#
# The openability test is a subshell, and it is not belt-and-braces: /dev/tty
# can exist and be readable by mode while `open` still fails with ENXIO,
# which is every container and CI runner — no controlling terminal. A bare
# `exec </dev/tty` there is fatal under `set -e` and takes the installer down
# at the last step, after everything is installed. `-r` alone does not catch
# it; only trying does.
wants_stdin=no
for argument in "$@"; do
    [ "$argument" = "--password-stdin" ] && wants_stdin=yes
done
if [ "$wants_stdin" = no ] && [ ! -t 0 ] && (: </dev/tty) 2>/dev/null; then
    exec </dev/tty
fi

say "Running cylist setup."
if [ -n "$URL" ]; then
    exec cylist --url "$URL" setup "$@"
else
    exec cylist setup "$@"
fi
