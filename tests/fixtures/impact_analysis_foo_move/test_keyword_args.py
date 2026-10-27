"""CP2 fixture: setattr/monkeypatch.setattr/patch/patch.object spelled with keyword arguments
instead of positional ones. Added by the round-03 fix loop for the keyword-argument gap: the
detectors used to only read `node.args` by position, so any of these keyword forms were silently
missed."""
from unittest.mock import patch

import pkg.module_a as module_a


def test_setattr_keyword_args(monkeypatch):
    monkeypatch.setattr(target=module_a, name="foo", value=lambda: "patched")  # MARKER: setattr-keyword


def test_patch_keyword_target():
    with patch(target="pkg.module_a.foo", new=lambda: "patched"):  # MARKER: patch-keyword-target
        pass


def test_patch_object_keyword_args():
    with patch.object(target=module_a, attribute="foo"):  # MARKER: patch-object-keyword-args
        pass
