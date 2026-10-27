"""CP2-X06 fixture: a deeper relative level (`from .. import module_a`, level=2) from a subpackage,
resolving up to pkg.module_a rather than pkg.sub.module_a -- exercises node.level > 1 for the bare
`from .. import X` form."""
from .. import module_a  # MARKER: importer-relative-dotdot-bare


def patch_it():
    module_a.foo = lambda: "patched"  # MARKER: monkeypatch-relative-dotdot
