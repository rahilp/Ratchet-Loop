"""CP3 fixture: shaped like the CP5-X78 case. Its own name and the words in this docstring/its
comments never spell out the rule it is used to exercise -- it just happens to open another
project file, read its text, parse it, and check it for a literal name. (Not meant to ever be
executed: impact_analysis.py's registration check only reads this file's own source text and
parses it with ast, the same way it would read a real test file on disk.)"""
import ast
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "plain_fixture.py"


def test_everything_still_adds_up():
    tree = ast.parse(TARGET.read_text())
    defined = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    assert "test_adds_two_numbers" in defined
