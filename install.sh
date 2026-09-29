#!/usr/bin/env bash
# Install Ratchet Loop without a plugin manager, for any supported agent CLI.
#   ./install.sh [--host auto|claude|codex|gemini|copilot|all] [--project]
# --host defaults to auto: the CLI running this script (your agent) is detected from its environment.
# Claude Code gets its copy in ~/.claude (or ./.claude with --project). Every other host shares one copy in
# $RATCHET_HOME (default ~/.ratchet-loop), even with --project; --project only moves Claude's copy and every host's hook config into the repo. The hooks are then written into each host's own config by `ratchet.py install-hooks`.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
HOST="auto"; SCOPE="user"
die() { echo "install.sh: $1" >&2; exit 1; }
while [[ $# -gt 0 ]]; do case "$1" in
  --host) [[ $# -ge 2 ]] || die "--host needs a value (auto, claude, codex, gemini, copilot or all)"; HOST="$2"; shift 2;;
  --project) SCOPE="project"; shift;;
  *) die "unknown option '$1' (use --host auto|claude|codex|gemini|copilot|all and --project)";;
esac; done
if [[ "$HOST" == "auto" ]]; then
  if [[ -n "${CLAUDECODE:-}${CLAUDE_CODE_ENTRYPOINT:-}" ]]; then HOST="claude"
  elif [[ -n "${CODEX_SANDBOX:-}${CODEX_THREAD_ID:-}${CODEX_SANDBOX_NETWORK_DISABLED:-}" ]]; then HOST="codex"
  elif [[ -n "${GEMINI_CLI:-}" ]]; then HOST="gemini"
  elif [[ -n "${COPILOT_CLI:-}${GITHUB_COPILOT_CLI:-}" ]]; then HOST="copilot"
  else HOST="claude"; echo "Could not tell which agent CLI is running this; installing for Claude Code. Use --host codex|gemini|copilot|all for another CLI."
  fi
  echo "Installing for: $HOST"
fi
case "$HOST" in
  claude|codex|gemini|copilot|all) ;;
  *) die "unknown host '$HOST' (use auto, claude, codex, gemini, copilot or all)";;
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

link_skills() {  # link_skills <host>...: make the shared skills visible in each CLI's own skills folder, if that CLI is set up
  local h dir s n linked=()
  for h in "$@"; do
    case "$h" in codex) dir="$HOME/.codex";; gemini) dir="$HOME/.gemini";; copilot) dir="$HOME/.copilot";; *) continue;; esac
    [[ -d "$dir" ]] || continue
    mkdir -p "$dir/skills"
    for s in "$OTHER_DEST"/skills/*/; do n=$(basename "$s"); rm -rf "$dir/skills/$n"; ln -s "$OTHER_DEST/skills/$n" "$dir/skills/$n"; done
    linked+=("$h")
    echo "Linked the Ratchet skills from $OTHER_DEST/skills into $dir/skills."
  done
  UNLINKED=()
  for h in "$@"; do [[ " ${linked[*]-} " == *" $h "* ]] || UNLINKED+=("$h"); done
}

UNLINKED=()
case "$HOST" in
  claude) copy_to "$CLAUDE_DEST"; hooks_for "$CLAUDE_DEST" claude;;
  all)    copy_to "$CLAUDE_DEST"; copy_to "$OTHER_DEST"
          hooks_for "$CLAUDE_DEST" claude; hooks_for "$OTHER_DEST" codex gemini copilot; link_skills codex gemini copilot;;
  *)      copy_to "$OTHER_DEST"; hooks_for "$OTHER_DEST" "$HOST"; link_skills "$HOST";;
esac
if [[ ${#UNLINKED[@]} -gt 0 ]]; then
  echo "For ${UNLINKED[*]}: that CLI's config folder was not found, so its skills were not linked. The skills are in $OTHER_DEST/skills; the agent can read them from there."
fi
echo "Restart your agent CLI, then say to it: \"Set up Ratchet and continue.\""
