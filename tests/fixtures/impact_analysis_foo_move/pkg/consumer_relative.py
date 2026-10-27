"""CP2 fixture: imports the moving symbol via a package-relative import (`from .module_a import
foo`), exercising package-context resolution for ImportFrom.level > 0."""
from .module_a import foo  # MARKER: importer-relative


def call_it():
    return foo()
