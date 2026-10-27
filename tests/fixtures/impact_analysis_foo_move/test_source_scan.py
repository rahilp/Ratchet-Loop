"""CP2 fixture: a source-inspection check whose own function name and comments never mention 'foo'
or any planned move (named test_no_forbidden_helpers_in_core on purpose) -- it just happens to
AST-scan pkg/module_a.py's own text looking for a literal helper name."""
import ast
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "pkg" / "module_a.py"


def test_no_forbidden_helpers_in_core():
    tree = ast.parse(TARGET.read_text())
    defined = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    assert "foo" in defined  # MARKER: source-inspection-scan
