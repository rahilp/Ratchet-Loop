#!/usr/bin/env bash
# Install Ratchet Loop without a plugin manager, for any supported agent CLI.
#   ./install.sh [--host claude|codex|gemini|copilot|all] [--project]
# Claude Code gets its copy in ~/.claude (or ./.claude with --project). Every other host shares one copy in
# $RATCHET_HOME (default ~/.ratchet-loop), even with --project; --project only moves Claude's copy and every host's hook config into the repo. The hooks are then written into each host's own config by `ratchet.py install-hooks`.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
HOST="claude"; SCOPE="user"
die() { echo "install.sh: $1" >&2; exit 1; }
while [[ $# -gt 0 ]]; do case "$1" in
  --host) [[ $# -ge 2 ]] || die "--host needs a value (claude, codex, gemini, copilot or all)"; HOST="$2"; shift 2;;
  --project) SCOPE="project"; shift;;
  *) die "unknown option '$1' (use --host claude|codex|gemini|copilot|all and --project)";;
esac; done
case "$HOST" in
  claude|codex|gemini|copilot|all) ;;
  *) die "unknown host '$HOST' (use claude, codex, gemini, copilot or all)";;
esac

if [[ "$SCOPE" == "project" ]]; then CLAUDE_DEST="$PWD/.claude"; else CLAUDE_DEST="$HOME/.claude"; fi
OTHER_DEST="${RATCHET_HOME:-$HOME/.ratchet-loop}"

copy_to() {  # copy_to <dest>: replace every skill folder (no stale files) and refresh the agent prompts
  local dest="$1" s n
  mkdir -p "$dest/skills" "$dest/agents"
  for s in "$SRC"/skills/*/; do n=$(basename "$s"); rm -rf "$dest/skills/$n"; cp -R "$s" "$dest/skills/$n"; done
  cp "$SRC"/agents/*.md "$dest/agents/"
  chmod +x "$dest/skills/ratchet-orchestrator/scripts/ratchet.py" "$dest"/skills/ratchet-ui-compare/scripts/* "$dest"/skills/ratchet-adversarial-loop/scripts/*
  echo "Installed Ratchet Loop into $dest (6 skills, $(ls "$SRC"/agents/*.md | wc -l | tr -d ' ') reviewer/agent prompts)."
}
hooks_for() {  # hooks_for <dest> <host>...
  local dest="$1"; shift
  local h
  for h in "$@"; do python3 "$dest/skills/ratchet-orchestrator/scripts/ratchet.py" install-hooks --host "$h" --scope "$SCOPE"; done
}

NONCLAUDE=()
case "$HOST" in
  claude) copy_to "$CLAUDE_DEST"; hooks_for "$CLAUDE_DEST" claude;;
  all)    copy_to "$CLAUDE_DEST"; copy_to "$OTHER_DEST"
          hooks_for "$CLAUDE_DEST" claude; hooks_for "$OTHER_DEST" codex gemini copilot; NONCLAUDE=(1);;
  *)      copy_to "$OTHER_DEST"; hooks_for "$OTHER_DEST" "$HOST"; NONCLAUDE=(1);;
esac
if [[ ${#NONCLAUDE[@]} -gt 0 ]]; then
  echo "For Codex, Gemini and Copilot: link the folders in $OTHER_DEST/skills into the CLI's own skills folder, or have your agent read the skill files from there."
fi
echo "Restart your agent CLI, then say to it: \"Set up Ratchet and check that its hooks are installed.\""
