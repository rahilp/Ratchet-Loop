"""CP2 fixture: a source-inspection check using a literal collection compared via containment
(`expected <= defined`) rather than a bare string literal directly inside the Compare node."""
import ast
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "pkg" / "module_a.py"


def test_inventory_set_is_subset():
    tree = ast.parse(TARGET.read_text())
    defined = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    expected = {"foo"}
    assert expected <= defined  # MARKER: source-inspection-literal-collection
