"""CP3 fixture (round-1 fix for codex-both/X1, not a planned case): a text-based source-inspection
test -- opens another project file, reads its text, and checks it with a plain substring
containment check (never `ast.parse`). Used to prove `_file_source_scans` also recognizes a
text-based scan, not only an AST-based one."""
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "plain_fixture.py"


def test_old_module_name_is_gone():
    source = TARGET.read_text()
    assert "old_module" not in source
