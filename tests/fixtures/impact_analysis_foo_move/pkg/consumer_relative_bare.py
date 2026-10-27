"""CP2-X06 fixture: imports module_a via a bare package-relative module import (`from . import
module_a as ma`, no direct symbol import) then reads the moving symbol off it by attribute --
exercises _collect_import_aliases resolving `node.module is None` relative ImportFrom forms, not
just the already-fixed `from .module_a import foo` spelling."""
from . import module_a as ma  # MARKER: importer-relative-bare-aliased


def call_it():
    return ma.foo()  # MARKER: importer-relative-bare-attr
