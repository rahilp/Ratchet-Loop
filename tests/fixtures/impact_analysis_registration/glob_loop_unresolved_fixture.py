"""CP3 fixture (round-3 fix for codex-both/X1, not a planned case): reads a file whose path is a
glob-loop variable (`for path in ROOT.rglob('*.py'): source = path.read_text()`) -- a read target
this module cannot statically resolve to either "looks like Python source" or "confirmed-data" --
and pairs it with a plain text-based containment check (no `ast.parse`). Used to prove a genuinely
unresolved read target paired only with a text-based check resolves to "ambiguous"/
manual-check-needed, never silently "no-scan", even though the loop is plainly walking `.py` files
at runtime."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def test_old_name_is_gone_everywhere():
    for path in ROOT.rglob("*.py"):
        source = path.read_text()
        assert "OLD_NAME" not in source
