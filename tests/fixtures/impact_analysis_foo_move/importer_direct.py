"""CP2 fixture: imports the moving symbol directly by name."""
from pkg.module_a import foo  # MARKER: importer-direct


def call_it():
    return foo()
