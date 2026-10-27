"""CP3 fixture (round-2 fix for X1, not a planned case): an `ast.parse` source scan whose read call
is `read_bytes()` rather than `read_text()`/`read()`. Used to prove `_file_source_scans` recognizes
`read_bytes` as a file-content read, so this valid ast.parse-based scan is not silently classified
as no-scan."""
import ast
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "plain_fixture.py"


def test_everything_still_adds_up():
    tree = ast.parse(TARGET.read_bytes())
    defined = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    assert "test_adds_two_numbers" in defined
