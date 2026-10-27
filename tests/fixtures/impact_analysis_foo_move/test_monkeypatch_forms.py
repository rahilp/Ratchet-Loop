"""CP2 fixture: pytest's monkeypatch.setattr forms and an aliased `patch` import, none of which the
original scan recognized."""
from unittest.mock import patch as mock_patch

import pkg.module_a as module_a


def test_monkeypatch_setattr_module_object(monkeypatch):
    monkeypatch.setattr(module_a, "foo", lambda: "patched")  # MARKER: monkeypatch-setattr-module


def test_monkeypatch_setattr_string_path(monkeypatch):
    monkeypatch.setattr("pkg.module_a.foo", lambda: "patched")  # MARKER: monkeypatch-setattr-string


def test_aliased_patch_import():
    with mock_patch("pkg.module_a.foo") as m:  # MARKER: aliased-patch-import
        m.return_value = "patched"
