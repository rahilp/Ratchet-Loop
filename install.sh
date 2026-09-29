#!/usr/bin/env bash
# Install Ratchet Loop without a plugin manager, for any supported agent CLI.
#   ./install.sh [--host claude|codex|gemini|copilot|all] [--project]
# Skills and reviewer prompts are copied to the host's folder (Claude Code: ~/.claude, others: ~/.ratchet-loop),
# then the hooks are written into that host's config by `ratchet.py install-hooks`.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
HOST="claude"; SCOPE="user"
while [[ $# -gt 0 ]]; do case "$1" in
  --host) HOST="$2"; shift 2;;
  --project) SCOPE="project"; shift;;
  *) echo "unknown option $1"; exit 1;;
esac; done
if [[ "$HOST" == "claude" ]]; then
  if [[ "$SCOPE" == "project" ]]; then DEST="$PWD/.claude"; else DEST="$HOME/.claude"; fi
else
  DEST="${RATCHET_HOME:-$HOME/.ratchet-loop}"
fi
mkdir -p "$DEST/skills" "$DEST/agents"
for s in "$SRC"/skills/*/; do n=$(basename "$s"); rm -rf "$DEST/skills/$n"; cp -R "$s" "$DEST/skills/$n"; done
cp "$SRC"/agents/*.md "$DEST/agents/"
RATCHET="$DEST/skills/ratchet-orchestrator/scripts/ratchet.py"
chmod +x "$RATCHET" "$DEST/skills/ratchet-ui-compare/scripts/"* "$DEST/skills/ratchet-adversarial-loop/scripts/"*
python3 "$RATCHET" install-hooks --host "$HOST" --scope "$SCOPE"
echo "Installed Ratchet Loop into $DEST (6 skills, $(ls "$SRC"/agents/*.md | wc -l | tr -d ' ') reviewer/agent prompts)."
if [[ "$HOST" != "claude" ]]; then
  echo "Point your agent at the skills in $DEST/skills (e.g. symlink them into its skills folder) — see README 'Other agent CLIs'."
fi
echo "Restart your agent CLI, run 'python3 $RATCHET doctor' in a project, then ask it to use the ratchet orchestrator."
