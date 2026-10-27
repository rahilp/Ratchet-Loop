"""CP3 fixture (round-1 fix for codex-both/X1, not a planned case): reads another project file's
content but pairs it with no recognized scan/check shape -- neither `ast.parse` nor a text-based
containment/regex/assertIn check. Used to prove a read with an unrecognized check shape is reported
manual-check-needed rather than silently decided as 'does not scan'."""
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "plain_fixture.py"


def test_file_is_not_empty():
    source = TARGET.read_text()
    assert len(source) > 0
