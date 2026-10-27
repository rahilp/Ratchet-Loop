"""CP2 fixture: the test body only calls a wrapper defined in helpers.py; the real
`patch.object()` call lives one call away inside that helper (see helpers.py), not in this file."""
from helpers import _apply_legacy_shim


def test_legacy_shim_still_works():
    _apply_legacy_shim()
