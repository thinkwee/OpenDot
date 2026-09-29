#!/usr/bin/env bash
# OpenDot one-liner installer (macOS + Linux):
#
#   curl -fsSL https://raw.githubusercontent.com/thinkwee/OpenDot/master/install.sh | bash
#
# Needs only git and curl. Clones (or updates) the repo into $DOT_DIR (default
# ~/OpenDot), then hands off to dot.sh, which fetches Python via uv, installs
# everything, asks which AI to use, starts the server and opens it in your browser.
#
# Idempotent: running it again on an existing checkout just does a `git pull` and
# restarts, same as `./dot.sh update`.
set -euo pipefail

REPO_URL="${DOT_REPO_URL:-https://github.com/thinkwee/OpenDot.git}"
DOT_DIR="${DOT_DIR:-$HOME/OpenDot}"

c() { printf "\033[%sm%s\033[0m" "$1" "$2"; }
say()  { echo -e "  $(c '38;5;209' '✦') $*"; }
warn() { echo -e "  $(c '33' '!') $*"; }
die()  { echo -e "  $(c '31' '✗') $*" >&2; exit 1; }

need() {
  command -v "$1" >/dev/null 2>&1 || die "'$1' is required but wasn't found. $2"
}

echo
say "OpenDot installer"

need git "Install it: https://git-scm.com/downloads (macOS: xcode-select --install, Debian/Ubuntu: sudo apt install git)"

need curl "Install it with your package manager (it ships with macOS)."

if [[ -d "$DOT_DIR/.git" ]]; then
  say "OpenDot is already checked out at $DOT_DIR — updating…"
  git -C "$DOT_DIR" pull --ff-only
elif [[ -e "$DOT_DIR" ]]; then
  die "$DOT_DIR already exists and isn't an OpenDot checkout. Set DOT_DIR to somewhere else, e.g.: DOT_DIR=~/opendot2 curl ... | bash"
else
  say "cloning into $DOT_DIR…"
  git clone --depth 1 "$REPO_URL" "$DOT_DIR"
fi

cd "$DOT_DIR"
chmod +x dot.sh

echo
say "handing off to ./dot.sh — it installs what it needs, asks which AI to use,"
say "starts OpenDot and opens it in your browser."
echo
exec ./dot.sh </dev/tty  # the wizard needs your keyboard, not this piped script
