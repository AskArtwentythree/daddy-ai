#!/bin/sh
# Daddy AI one-line setup for a new teammate (macOS and Linux):
#   curl -fsSL https://raw.githubusercontent.com/AskArtwentythree/daddy-ai/main/install.sh | sh -s -- INVITE_CODE SERVER_URL
# Installs uv and the `daddy` command, joins the family, and connects Cursor, Claude Code and the Claude app.
# Without arguments it only installs or updates `daddy` and reconnects. Safe to run again.
set -eu

CODE=${1:-}
SERVER=${2:-}
REPO=${DADDY_SOURCE:-"git+https://github.com/AskArtwentythree/daddy-ai#subdirectory=packages/cli"}

step() { printf '\n\033[1m%s\033[0m\n' "$1"; }

if [ -n "$CODE" ] && [ -z "$SERVER" ]; then
  echo "Usage: sh install.sh INVITE_CODE SERVER_URL   (copy the full line from your invite)" >&2
  exit 1
fi

step "1/4 Installing uv (Python tool manager)"
if command -v uv >/dev/null 2>&1; then
  UV=$(command -v uv)
  echo "Already installed: $UV"
else
  curl -LsSf https://astral.sh/uv/install.sh | sh
  UV="$HOME/.local/bin/uv"
fi

step "2/4 Installing the daddy command"
"$UV" tool install --force --reinstall --quiet "$REPO"
"$UV" tool update-shell >/dev/null 2>&1 || true
DADDY="$("$UV" tool dir --bin)/daddy"
echo "Installed: $DADDY"

step "3/4 Joining your family"
if [ -n "$CODE" ]; then
  if ! "$DADDY" join "$CODE" --server "$SERVER"; then
    if "$DADDY" status >/dev/null 2>&1; then
      echo "This computer is already connected, keeping that."
    else
      echo "Ask your admin for a new invite: run \`daddy invite <email> --name <name>\`." >&2
      exit 1
    fi
  fi
else
  "$DADDY" status >/dev/null 2>&1 || { echo "No invite code given and not connected yet." >&2; exit 1; }
  echo "Already connected."
fi

step "4/4 Connecting your AI apps"
"$DADDY" connect all

step "Done!"
if [ -z "${DADDY_NO_WINDOW:-}" ]; then
  "$DADDY" watch --window || echo "Run \`daddy watch\` in a new terminal to get messages from teammates live."
fi
echo "Messages, files and skills from teammates appear in the \`daddy watch\` window as they arrive."
