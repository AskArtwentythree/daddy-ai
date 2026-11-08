#!/bin/sh
# Daddy AI one-line setup (macOS and Linux).
#   Start a new team:   curl -fsSL https://raw.githubusercontent.com/AskArtwentythree/daddy-ai/main/install.sh | sh
#   Join a team:        curl -fsSL https://raw.githubusercontent.com/AskArtwentythree/daddy-ai/main/install.sh | sh -s -- INVITE_CODE
#                       (a team link asks for your name and email; DADDY_NAME and DADDY_EMAIL answer it ahead of time)
# Installs uv and the `daddy` command, creates or joins a family on the hosted Daddy AI (or SERVER_URL, the
# optional second argument, for a self-hosted one), connects Cursor, Claude Code and the Claude app, and opens
# `daddy watch`. On a computer that is already connected it just updates and reconnects. Safe to run again.
set -eu

CODE=${1:-}
SERVER=${2:-${DADDY_SERVER:-}}
REPO=${DADDY_SOURCE:-"git+https://github.com/AskArtwentythree/daddy-ai#subdirectory=packages/cli"}

step() { printf '\n\033[1m%s\033[0m\n' "$1"; }
ask() {  # ask VAR "Question" — reads from the terminal even when this script is piped into sh
  eval "current=\${$1:-}"
  while [ -z "$current" ]; do
    printf '%s: ' "$2"
    read -r current </dev/tty || { echo; echo "No terminal to ask on; set $1 and run again." >&2; exit 1; }
  done
  eval "$1=\$current"
}

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

if [ -n "$SERVER" ]; then
  set -- --server "$SERVER"
else
  set --
fi

if [ -n "$CODE" ]; then
  step "3/4 Joining your team"
  TTY=/dev/null
  if ( : </dev/tty ) 2>/dev/null; then TTY=/dev/tty; fi
  if ! "$DADDY" join "$CODE" "$@" <"$TTY"; then
    if "$DADDY" status >/dev/null 2>&1; then
      echo "Keeping this computer's current team."
    else
      echo "Ask your admin for a new invite or team link: they run \`daddy invite -p <project>\`." >&2
      exit 1
    fi
  fi
elif "$DADDY" status >/dev/null 2>&1; then
  step "3/4 Already connected"
  echo "Keeping your current team. To join another one too: DADDY_HOME=~/.daddy-<team> daddy join <code>"
else
  step "3/4 Starting a new team (you will be its admin)"
  DADDY_FAMILY=${DADDY_FAMILY:-} DADDY_NAME=${DADDY_NAME:-} DADDY_EMAIL=${DADDY_EMAIL:-}
  ask DADDY_FAMILY "Team name"
  ask DADDY_NAME "Your name"
  ask DADDY_EMAIL "Your email"
  "$DADDY" init --family "$DADDY_FAMILY" --name "$DADDY_NAME" --email "$DADDY_EMAIL" "$@"
fi

step "4/4 Connecting your AI apps"
"$DADDY" connect all || echo "No Cursor, Claude Code or Claude app found; install one, then run \`daddy connect all\`."

step "Done!"
if [ -z "${DADDY_NO_WINDOW:-}" ]; then
  "$DADDY" watch --window || echo "Run \`daddy watch\` in a new terminal to get messages from teammates live."
fi
echo "Messages, files and skills from teammates appear in the \`daddy watch\` window as they arrive."
echo "Restart your AI app, then ask it: \"Daddy, who am I?\""
if [ -z "$CODE" ]; then
  echo "Add your code:      cd your-project && daddy project create \"My App\" && daddy ingest ."
  echo "Invite teammates:   daddy invite -p my-app      (one link for your team chat)"
fi
