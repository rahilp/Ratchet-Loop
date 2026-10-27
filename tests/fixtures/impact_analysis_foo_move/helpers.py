"""CP2 fixture: helper wrappers called by test_hidden_patch.py. The real re-point site (a
`patch.object()` call) lives in here, one call removed from the obvious test body, so a naive scan
that only inspects test functions directly would miss it."""
from unittest.mock import patch

import pkg.module_a as module_a


def _apply_legacy_shim():
    with patch.object(module_a, "foo") as m:  # MARKER: hidden-patch
        m.return_value = "shimmed"
        return m
