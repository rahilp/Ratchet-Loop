"""CP2 fixture: four separate test functions, one per monkeypatch-by-name form that re-points
`pkg.module_a.foo` once the real symbol move happens. Each re-point statement carries a unique
trailing `# MARKER: <name>` comment so tests/test_impact_analysis_repoint.py can locate its exact
expected line without hard-coding line numbers that would drift if this fixture is edited."""
from unittest.mock import Mock, patch

import pkg.module_a as module_a


def test_plain_attribute_assignment_monkeypatch():
    module_a.foo = Mock()  # MARKER: plain-attribute-assignment


def test_setattr_monkeypatch():
    setattr(module_a, "foo", Mock())  # MARKER: setattr-monkeypatch


def test_string_target_patch_monkeypatch():
    with patch("pkg.module_a.foo") as m:  # MARKER: string-target-patch
        m.return_value = "patched"


def test_patch_object_monkeypatch():
    with patch.object(module_a, "foo") as m:  # MARKER: patch-object
        m.return_value = "patched"
