"""CP3 fixture (round-2 fix for X2, corrected in round 3; not a planned case): reads a file whose
path comes from an unresolved expression (not a literal path this module can classify as `.py` or
an obviously-data extension) and pairs it with a plain text-based containment check (no
`ast.parse`). Used to prove an unresolved/unknown read target paired only with a text-based check
resolves to "ambiguous"/manual-check-needed, not "no-scan" -- round 3 corrected round 2's fix,
which had wrongly folded a genuinely-unresolved read target into the same bucket as a
confirmed-data one."""
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = Path(os.environ.get("SOME_TARGET_PATH", str(HERE)))


def test_marker_is_present():
    content = TARGET.read_text()
    assert "marker" in content
