"""CP2 fixture: the same source-inspection scan as test_source_scan.py, but spelled with a single
combined path literal instead of separate Path-division operands."""
import ast
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "pkg/module_a.py"


def test_no_forbidden_helpers_combined_path():
    tree = ast.parse(TARGET.read_text())
    defined = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    assert "foo" in defined  # MARKER: source-inspection-combined-path
